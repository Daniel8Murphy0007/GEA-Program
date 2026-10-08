# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""osdu - an OSDU-shaped export of a site: the manifest a large operator's data platform loads.

The OSDU Data Platform ingests a Manifest: one JSON document with ReferenceData, MasterData (wells, wellbores)
and Data (a WorkProduct, its WorkProductComponents, their Datasets), every record carrying an id, a kind
`osdu:wks:<group>--<Type>:<version>`, an access-control list, legal tags, and its data. This module writes that
document for one site, from the well-known schemas as published, and copies the files beside it:

- each well as `master-data--Well:1.0.0` and `master-data--Wellbore:1.0.0`, with its API and UIC numbers as
  name aliases, the gauge station as a vertical measurement, and its surface position as a SpatialLocation
  that carries BOTH the WGS84 coordinates the platform indexes and the coordinates as the operator gave them,
  on the datum they were given on, with the operation this program applied between them written out. A
  position whose datum is unknown gets no WGS84 coordinates - the platform would index a point that may be
  tens of metres wrong - and is named as a gap;
- each well's record as `work-product-component--WellLog:1.1.0` in the time domain (ZeroTime, the sampling
  interval, a curve per channel with its unit) over a `dataset--File.Generic` that is the historian file
  itself, with its size and SHA-256;
- each seismic station as a generic work-product component with its position and its records as datasets,
  because the seismic trace schema was not verified against a published example in the writing of this
  module, and a kind this program has not read is not a kind it writes;
- the site as the WorkProduct that holds the components.

What the operator declares and this module will not invent: the data partition, the ACL groups, the legal
tag and the countries - a manifest without them is not loadable, and the export says NOT LOADABLE with the
missing names rather than filling in placeholders that would load and be wrong. The reference-data ids
(units, facility types, CRSs) are written in the platform's own form against the declared partition.

What it will not call a measurement: a WGS84 position for a point on an unknown datum; a loadable manifest
without the operator's ACL and legal tag; a seismic trace component under a schema not read here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import geodesy as G

MANIFEST_KIND = 'osdu:wks:Manifest:1.0.0'
KINDS = {
    'well': 'osdu:wks:master-data--Well:1.0.0',
    'wellbore': 'osdu:wks:master-data--Wellbore:1.0.0',
    'welllog': 'osdu:wks:work-product-component--WellLog:1.1.0',
    'wpc_generic': 'osdu:wks:work-product-component--GenericWorkProductComponent:1.0.0',
    'wp': 'osdu:wks:work-product--GenericWorkProduct:1.0.0',
    'file': 'osdu:wks:dataset--File.Generic:1.0.0',
}
# geographic CRSs by this program's datum names, as EPSG codes the platform's reference data carries
CRS_BY_DATUM = {'WGS84': ('EPSG::4326', 'WGS 84'), 'NAD83': ('EPSG::4269', 'NAD83'), 'NAD27': ('EPSG::4267', 'NAD27')}
ENCODING = {'.csv': 'text%2Fcsv', '.mseed': 'application%2Fvnd.fdsn.mseed', '.json': 'application%2Fjson', '.las': 'text%2Fplain',
            '.xml': 'application%2Fxml', '.txt': 'text%2Fplain'}
UNITS = {'psi': 'psi', 'degf': 'degF', 'degc': 'degC', 'ft': 'ft', 'm': 'm', 'bbl/min': 'bbl%2Fmin', 'bbl/d': 'bbl%2Fd', '': 'unitless'}


@dataclass
class Context:
    """What the operator declares for the platform. Nothing here is this program's to decide."""
    partition: str = ''
    acl_owners: List[str] = field(default_factory=list)
    acl_viewers: List[str] = field(default_factory=list)
    legal_tags: List[str] = field(default_factory=list)
    countries: List[str] = field(default_factory=lambda: ['US'])
    source: str = 'GEA-Program'
    operator_org_id: str = ''                  # e.g. partition:master-data--Organisation:AcmeOperating:

    def missing(self) -> List[str]:
        out = []
        if not self.partition: out.append('data partition id (--partition)')
        if not self.acl_owners: out.append('ACL owners group (--acl-owner)')
        if not self.acl_viewers: out.append('ACL viewers group (--acl-viewer)')
        if not self.legal_tags: out.append('legal tag (--legal-tag)')
        return out

    def ref(self, group: str, value: str) -> str:
        """A reference-data or master-data id in the platform's form: <partition>:<group>:<value>:"""
        return f"{self.partition or 'PARTITION'}:{group}:{value}:"

    def envelope(self) -> dict:
        return {'acl': {'owners': list(self.acl_owners), 'viewers': list(self.acl_viewers)},
                'legal': {'legaltags': list(self.legal_tags), 'otherRelevantDataCountries': list(self.countries), 'status': 'compliant'}}


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _sk(kind: str, key: str) -> str:
    return f'surrogate-key:{kind}-{key}'


def _common(ctx: Context) -> dict:
    return {'ResourceCurationStatus': ctx.ref('reference-data--ResourceCurationStatus', 'Created'),
            'ResourceLifecycleStatus': ctx.ref('reference-data--ResourceLifecycleStatus', 'Loading'),
            'Source': ctx.source, 'ExistenceKind': ctx.ref('reference-data--ExistenceKind', 'Actual')}


# --------------------------------------------------------------------------------------------------------------
# the position: both coordinates, and the operation between them, written out
# --------------------------------------------------------------------------------------------------------------
def spatial_location(lat: float, lon: float, datum: Optional[str], ctx: Context, when: Optional[str] = None) -> dict:
    """A SpatialLocation as the schema has it: AsIngestedCoordinates on the datum the operator gave, Wgs84Coordinates
    only when that datum is known, and AppliedOperations saying what this program did between the two."""
    d = G.datum_name(datum)
    out: Dict[str, Any] = {'SpatialLocationCoordinatesDate': when or _now(),
                           'SpatialParameterTypeID': ctx.ref('reference-data--SpatialParameterType', 'Outline'),
                           'SpatialGeometryTypeID': ctx.ref('reference-data--SpatialGeometryType', 'Point'),
                           'AsIngestedCoordinates': {'type': 'AnyCrsFeatureCollection',
                                                     'features': [{'type': 'AnyCrsFeature', 'properties': {},
                                                                   'geometry': {'type': 'AnyCrsPoint', 'coordinates': [float(lon), float(lat)]}}]},
                           'AppliedOperations': []}
    if d in CRS_BY_DATUM:
        code, name = CRS_BY_DATUM[d]
        out['AsIngestedCoordinates']['CoordinateReferenceSystemID'] = ctx.ref('reference-data--CoordinateReferenceSystem', f'Geographic2D:{code}')
        p = G.to_wgs84(float(lat), float(lon), 0.0, d)
        out['Wgs84Coordinates'] = {'type': 'FeatureCollection',
                                   'features': [{'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Point', 'coordinates': [p['lon'], p['lat']]}}]}
        out['AppliedOperations'].append((f"transformation {name} to WGS 84 by this program's three-parameter datum shift (gea.geodesy); "
                                         f"1 point transformed; shift {float(p.get('shift_m') or 0.0):.1f} m") if d != 'WGS84'
                                        else 'none: the position was given on WGS 84')
        out['QualitativeSpatialAccuracyTypeID'] = ctx.ref('reference-data--QualitativeSpatialAccuracyType', 'Confirmed')
        out['_gap'] = None
    else:
        out['AppliedOperations'].append('none: the datum of the position as given is unknown, so no WGS 84 coordinates are written')
        out['QualitativeSpatialAccuracyTypeID'] = ctx.ref('reference-data--QualitativeSpatialAccuracyType', 'Unverifiable')
        out['_gap'] = 'position datum unknown: no Wgs84Coordinates written, the platform cannot index this point'
    return out


# --------------------------------------------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------------------------------------------
def well_record(well: dict, ctx: Context) -> dict:
    disp = well.get('disposal') or {}
    surf = well.get('surface') or {}
    data: Dict[str, Any] = {**_common(ctx), 'FacilityName': well.get('display') or well['id'],
                            'FacilityID': disp.get('api_number') or well['id'],
                            'FacilityTypeID': ctx.ref('reference-data--FacilityType', 'Well'),
                            'OperatingEnvironmentID': ctx.ref('reference-data--OperatingEnvironment', 'Onshore'),
                            'NameAliases': [{'AliasName': well['id'], 'AliasNameTypeID': ctx.ref('reference-data--AliasNameType', 'UniqueIdentifier')}],
                            'ExtensionProperties': {'gea': {'well_id': well['id'], 'kind': well.get('kind'), 'role': disp.get('role')}}}
    if disp.get('api_number'):
        data['NameAliases'].append({'AliasName': disp['api_number'], 'AliasNameTypeID': ctx.ref('reference-data--AliasNameType', 'RegulatoryIdentifier')})
    if disp.get('uic_number'):
        data['NameAliases'].append({'AliasName': disp['uic_number'], 'AliasNameTypeID': ctx.ref('reference-data--AliasNameType', 'PermitNumber')})
    if ctx.operator_org_id:
        data['CurrentOperatorID'] = ctx.operator_org_id
        data['DataSourceOrganisationID'] = ctx.operator_org_id
    gaps = []
    if surf.get('lat') is not None and surf.get('lon') is not None:
        sl = spatial_location(surf['lat'], surf['lon'], surf.get('datum'), ctx, well.get('added_utc'))
        if sl.pop('_gap', None):
            gaps.append(f"well {well['id']}: position datum unknown - no WGS 84 coordinates written")
        data['SpatialLocation'] = sl
    else:
        gaps.append(f"well {well['id']}: no surface position declared (set-well --lat --lon --datum)")
    if well.get('station_md_ft') is not None:
        data['VerticalMeasurements'] = [{'VerticalMeasurementID': 'GaugeStation', 'VerticalMeasurement': float(well['station_md_ft']),
                                         'VerticalMeasurementTypeID': ctx.ref('reference-data--VerticalMeasurementType', 'ArbitraryPoint'),
                                         'VerticalMeasurementPathID': ctx.ref('reference-data--VerticalMeasurementPath', 'MeasuredDepth'),
                                         'VerticalMeasurementUnitOfMeasureID': ctx.ref('reference-data--UnitOfMeasure', 'ft'),
                                         'VerticalMeasurementDescription': 'the downhole gauge station, measured depth'}]
    return {'id': _sk('well', well['id']), 'kind': KINDS['well'], **ctx.envelope(), 'data': data, '_gaps': gaps}


def wellbore_record(well: dict, ctx: Context) -> dict:
    data: Dict[str, Any] = {**_common(ctx), 'FacilityName': (well.get('display') or well['id']) + ' wellbore',
                            'FacilityID': (well.get('disposal') or {}).get('api_number') or well['id'],
                            'FacilityTypeID': ctx.ref('reference-data--FacilityType', 'Wellbore'),
                            'WellID': _sk('well', well['id']),
                            'TrajectoryTypeID': ctx.ref('reference-data--WellboreTrajectoryType', 'Unknown'),
                            'ExtensionProperties': {'gea': {'well_id': well['id']}}}
    surf = well.get('surface') or {}
    if surf.get('lat') is not None and surf.get('lon') is not None:
        sl = spatial_location(surf['lat'], surf['lon'], surf.get('datum'), ctx, well.get('added_utc')); sl.pop('_gap', None)
        data['SpatialLocation'] = sl
    return {'id': _sk('wellbore', well['id']), 'kind': KINDS['wellbore'], **ctx.envelope(), 'data': data, '_gaps': []}


def dataset_record(src_path: str, rel_in_export: str, ctx: Context, name: Optional[str] = None, description: str = '') -> dict:
    ext = os.path.splitext(src_path)[1].lower()
    size = os.path.getsize(src_path)
    h = hashlib.sha256()
    with open(src_path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    enc = ctx.ref('reference-data--EncodingFormatType', ENCODING.get(ext, 'application%2Foctet-stream'))
    data = {**_common(ctx), 'Name': name or os.path.basename(src_path), 'Description': description, 'TotalSize': str(size),
            'EncodingFormatTypeID': enc,
            'DatasetProperties': {'FileSourceInfo': {'FileSource': rel_in_export, 'Name': os.path.basename(src_path), 'FileSize': str(size),
                                                     'EncodingFormatTypeID': enc, 'Checksum': h.hexdigest(), 'ChecksumAlgorithm': 'SHA-256'}}}
    return {'id': _sk('file', hashlib.md5(rel_in_export.encode()).hexdigest()[:12]), 'kind': KINDS['file'], **ctx.envelope(), 'data': data, '_gaps': []}


def welllog_record(well: dict, stream, dataset_id: str, ctx: Context) -> dict:
    """The historian record as a WellLog in the time domain: ZeroTime is the record's start, the reference curve
    is elapsed time, a curve per channel with its unit."""
    import numpy as np
    idx = np.asarray(stream.index, dtype=float)
    dt = float(np.median(np.diff(idx))) if idx.size > 1 else None
    curves = [{'CurveID': 'TIME', 'Mnemonic': 'TIME', 'CurveUnit': ctx.ref('reference-data--UnitOfMeasure', 's'), 'NumberOfColumns': 1,
               'TopDepth': float(idx[0]) if idx.size else 0.0, 'BaseDepth': float(idx[-1]) if idx.size else 0.0, 'IsProcessed': False}]
    for name, ch in stream.channels.items():
        u = (ch.unit or '').lower()
        curves.append({'CurveID': name, 'Mnemonic': name, 'CurveUnit': ctx.ref('reference-data--UnitOfMeasure', UNITS.get(u, u or 'unitless')),
                       'NumberOfColumns': 1, 'TopDepth': float(idx[0]) if idx.size else 0.0, 'BaseDepth': float(idx[-1]) if idx.size else 0.0,
                       'IsProcessed': 'clean' in name.lower(), 'NullValue': True})
    gaps = []
    zero = stream.meta.get('start_time') if getattr(stream, 'meta', None) else None
    if not zero:
        gaps.append(f"well {well['id']}: the record carries no absolute start time; ZeroTime is not written and the log is in elapsed seconds only")
    data: Dict[str, Any] = {**_common(ctx), 'Name': f"{well.get('display') or well['id']} - downhole gauge record", 'Description': 'historian export as held by the site',
                            'WellboreID': _sk('wellbore', well['id']), 'Datasets': [dataset_id],
                            'WellLogTypeID': ctx.ref('reference-data--LogType', 'Raw'),
                            'SamplingDomainTypeID': ctx.ref('reference-data--WellLogSamplingDomainType', 'Time' if stream.index_kind == 'time_s' else 'Depth'),
                            'ReferenceCurveID': 'TIME' if stream.index_kind == 'time_s' else 'DEPTH',
                            'SamplingStart': float(idx[0]) if idx.size else 0.0, 'SamplingStop': float(idx[-1]) if idx.size else 0.0,
                            'IsRegular': bool(dt is not None and idx.size > 2 and float(np.std(np.diff(idx))) < 1e-6 * max(dt, 1e-9)),
                            'Curves': curves, 'IsDiscoverable': True, 'ExtensionProperties': {'gea': {'well_id': well['id'], 'samples': int(idx.size)}}}
    if dt is not None:
        data['SamplingInterval'] = dt
    if zero:
        try:                                         # the platform's examples write UTC with a Z; the readers here give +00:00
            from .permits import parse_date
            zd = parse_date(str(zero))
            data['ZeroTime'] = zd.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if zd else str(zero)
        except Exception:
            data['ZeroTime'] = str(zero)
    if stream.index_kind != 'time_s':
        curves[0].update({'CurveID': 'DEPTH', 'Mnemonic': 'DEPTH', 'CurveUnit': ctx.ref('reference-data--UnitOfMeasure', 'ft')})
    return {'id': _sk('welllog', well['id']), 'kind': KINDS['welllog'], **ctx.envelope(), 'data': data, '_gaps': gaps}


def station_record(station: dict, dataset_ids: Sequence[str], ctx: Context) -> dict:
    data: Dict[str, Any] = {**_common(ctx), 'Name': f"Seismic station {station.get('display') or station['id']}",
                            'Description': f"{station.get('kind', 'single')} seismic station; records as held by the site; band {station.get('band_hz')} Hz",
                            'Datasets': list(dataset_ids), 'IsDiscoverable': True,
                            'ExtensionProperties': {'gea': {'station_id': station['id'], 'kind': station.get('kind'), 'band_hz': station.get('band_hz'),
                                                            'note': 'a generic component: the seismic trace schema was not verified in the writing of this export'}}}
    gaps = []
    if station.get('lat') is not None and station.get('lon') is not None:
        sl = spatial_location(station['lat'], station['lon'], station.get('datum'), ctx, station.get('added_utc'))
        if sl.pop('_gap', None):
            gaps.append(f"station {station['id']}: position datum unknown - no WGS 84 coordinates written")
        data['SpatialPoint'] = sl
    else:
        gaps.append(f"station {station['id']}: no position")
    return {'id': _sk('station', station['id']), 'kind': KINDS['wpc_generic'], **ctx.envelope(), 'data': data, '_gaps': gaps}


# --------------------------------------------------------------------------------------------------------------
# the manifest
# --------------------------------------------------------------------------------------------------------------
def build_manifest(site: dict, wells: Sequence[dict], streams: Dict[str, Any], well_files: Dict[str, str], stations: Sequence[dict],
                   station_files: Dict[str, List[str]], ctx: Context, out_dir: str) -> dict:
    """One Manifest for one site. Files are copied under out_dir/files/ and referenced by that relative path;
    every record is written with its gaps collected; the manifest says whether it is loadable."""
    files_dir = os.path.join(out_dir, 'files')
    os.makedirs(files_dir, exist_ok=True)
    master: List[dict] = []; wpcs: List[dict] = []; datasets: List[dict] = []; gaps: List[str] = []
    for w in wells:
        wr = well_record(w, ctx); gaps += wr.pop('_gaps'); master.append(wr)
        br = wellbore_record(w, ctx); gaps += br.pop('_gaps'); master.append(br)
        src = well_files.get(w['id'])
        if src and os.path.isfile(src):
            rel = f"files/wells/{w['id']}/{os.path.basename(src)}"
            os.makedirs(os.path.dirname(os.path.join(out_dir, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(out_dir, rel))
            ds = dataset_record(src, rel, ctx, description=f"historian file of well {w['id']}"); gaps += ds.pop('_gaps'); datasets.append(ds)
            if w['id'] in streams:
                lr = welllog_record(w, streams[w['id']], ds['id'], ctx); gaps += lr.pop('_gaps'); wpcs.append(lr)
        else:
            gaps.append(f"well {w['id']}: no file to export ({w.get('kind')} well)")
    for st in stations:
        ids = []
        for src in station_files.get(st['id'], []):
            if not os.path.isfile(src):
                continue
            rel = f"files/seismic/{st['id']}/{os.path.basename(src)}"
            os.makedirs(os.path.dirname(os.path.join(out_dir, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(out_dir, rel))
            ds = dataset_record(src, rel, ctx, description=f"record of seismic station {st['id']}"); gaps += ds.pop('_gaps'); datasets.append(ds); ids.append(ds['id'])
        sr = station_record(st, ids, ctx); gaps += sr.pop('_gaps'); wpcs.append(sr)
    wp = {'id': _sk('wp', site['id']), 'kind': KINDS['wp'], **ctx.envelope(),
          'data': {**_common(ctx), 'Name': f"GEA site {site.get('display') or site['id']}", 'Description': (site.get('note') or '') or f"the site as held by {ctx.source}",
                   'Components': [c['id'] for c in wpcs], 'IsDiscoverable': True,
                   'ExtensionProperties': {'gea': {'site_id': site['id'], 'client': site.get('client'), 'exported_utc': _now()}}}}
    missing = ctx.missing()
    manifest = {'kind': MANIFEST_KIND, 'ReferenceData': [], 'MasterData': master,
                'Data': {'WorkProduct': wp, 'WorkProductComponents': wpcs, 'Datasets': datasets}}
    with open(os.path.join(out_dir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=1)
    summary = {'protocol': 'osdu.export/1', 'site': site['id'], 'out_dir': os.path.abspath(out_dir), 'manifest': os.path.join(os.path.abspath(out_dir), 'manifest.json'),
               'kind': MANIFEST_KIND, 'counts': {'master_data': len(master), 'work_product_components': len(wpcs), 'datasets': len(datasets),
                                                 'wells': len(wells), 'stations': len(stations)},
               'loadable': not missing, 'missing_declarations': missing, 'gaps': gaps,
               'status': 'LOADABLE' if not missing else 'NOT LOADABLE',
               'basis': ('the OSDU well-known schemas as published in the data-definitions examples: Manifest 1.0.0, master-data Well and Wellbore 1.0.0, '
                         'work-product-component WellLog 1.1.0 and GenericWorkProductComponent 1.0.0, dataset File.Generic 1.0.0; surrogate keys for '
                         'every record this export makes; reference-data ids in the platform\'s form against the declared partition'),
               'not_a_measurement': ['a WGS 84 position for a point whose datum is unknown', 'a loadable manifest without the operator\'s partition, ACL and legal tag',
                                     'a seismic trace component: a kind not read here is not a kind written here']}
    with open(os.path.join(out_dir, 'export_summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=1)
    return summary


def validate(manifest: dict) -> dict:
    """The structural checks the platform's own loader makes first: every record has id, kind, acl, legal and data;
    every reference a work-product component makes resolves to a dataset in the same manifest; every wellbore's
    well is in MasterData; the work product lists every component."""
    problems = []
    ids = set()
    recs = list(manifest.get('MasterData', [])) + list(manifest['Data'].get('WorkProductComponents', [])) + list(manifest['Data'].get('Datasets', [])) + [manifest['Data']['WorkProduct']]
    for r in recs:
        for k in ('id', 'kind', 'acl', 'legal', 'data'):
            if k not in r:
                problems.append(f"{r.get('id', '?')}: no {k}")
        if r.get('kind', '').count(':') != 3 or not r.get('kind', '').startswith('osdu:wks:'):
            problems.append(f"{r.get('id')}: kind {r.get('kind')!r} is not osdu:wks:<group>--<Type>:<version>")
        ids.add(r.get('id'))
    ds_ids = {d['id'] for d in manifest['Data'].get('Datasets', [])}
    for c in manifest['Data'].get('WorkProductComponents', []):
        for d in c['data'].get('Datasets', []):
            if d not in ds_ids:
                problems.append(f"{c['id']}: dataset {d} not in this manifest")
    for m in manifest.get('MasterData', []):
        if m['kind'] == KINDS['wellbore'] and m['data'].get('WellID') not in ids:
            problems.append(f"{m['id']}: well {m['data'].get('WellID')} not in this manifest")
    comps = set(manifest['Data']['WorkProduct']['data'].get('Components', []))
    for c in manifest['Data'].get('WorkProductComponents', []):
        if c['id'] not in comps:
            problems.append(f"work product does not list {c['id']}")
    return {'ok': not problems, 'problems': problems, 'records': len(recs)}


# --------------------------------------------------------------------------------------------------------------
# the self-test
# --------------------------------------------------------------------------------------------------------------
def selftest(tmp: Optional[str] = None) -> dict:
    import tempfile
    import numpy as np
    from .ports import LiveStream, StreamChannel
    d = tmp or tempfile.mkdtemp()
    os.makedirs(d, exist_ok=True)
    src = os.path.join(d, 'hist.csv')
    with open(src, 'w') as f:
        f.write('timestamp,P_psi,T_degF\n' + ''.join(f'2026-03-01T00:{i:02d}:00Z,{1500 + i},{150 + i / 10}\n' for i in range(60)))
    stream = LiveStream(name='hist', source_format='csv', index_kind='time_s', index=np.arange(60) * 60.0,
                        channels={'P_psi': StreamChannel('P_psi', 'psi', np.arange(60) + 1500.0), 'T_degF': StreamChannel('T_degF', 'degF', np.arange(60) / 10 + 150)},
                        meta={'start_time': '2026-03-01T00:00:00Z'})
    wells = [{'id': 'W-1', 'display': 'Well 1', 'kind': 'file', 'station_md_ft': 9800.0, 'added_utc': '2026-03-01T00:00:00Z',
              'surface': {'lat': 31.96, 'lon': -102.24, 'datum': 'NAD27'}, 'disposal': {'api_number': '42-329-12345', 'uic_number': '123456', 'role': 'disposal'}},
             {'id': 'W-2', 'display': 'Well 2', 'kind': 'file', 'surface': {'lat': 31.90, 'lon': -102.30, 'datum': 'UNKNOWN'}},
             {'id': 'W-3', 'display': 'Well 3', 'kind': 'catalog'}]
    ms = os.path.join(d, 'rec.mseed'); open(ms, 'wb').write(b'\x00' * 512)
    stations = [{'id': 'S-1', 'display': 'Node 1', 'kind': 'single', 'lat': 31.95, 'lon': -102.25, 'datum': 'WGS84', 'band_hz': [2, 20], 'files': ['rec.mseed']}]
    full = Context('opendes', ['data.default.owners@opendes.example.com'], ['data.default.viewers@opendes.example.com'], ['opendes-public-usa'],
                   operator_org_id='opendes:master-data--Organisation:AcmeOperating:')
    out = os.path.join(d, 'osdu_full')
    s_full = build_manifest({'id': 'site-1', 'display': 'Site 1', 'client': 'Acme'}, wells, {'W-1': stream}, {'W-1': src, 'W-2': src}, stations, {'S-1': [ms]}, full, out)
    man = json.load(open(os.path.join(out, 'manifest.json'), encoding='utf-8'))
    v = validate(man)
    s_bare = build_manifest({'id': 'site-1', 'display': 'Site 1'}, wells[:1], {'W-1': stream}, {'W-1': src}, [], {}, Context(), os.path.join(d, 'osdu_bare'))
    w1 = next(m for m in man['MasterData'] if m['id'] == 'surrogate-key:well-W-1')
    w2 = next(m for m in man['MasterData'] if m['id'] == 'surrogate-key:well-W-2')
    log = next(c for c in man['Data']['WorkProductComponents'] if c['kind'] == KINDS['welllog'])
    ds = man['Data']['Datasets'][0]
    sl = w1['data']['SpatialLocation']
    checks = {
        'manifest_kind': man['kind'] == MANIFEST_KIND and set(man) == {'kind', 'ReferenceData', 'MasterData', 'Data'},
        'validates': v['ok'] and v['records'] == 12,        # 6 master-data, 2 components, 3 datasets, 1 work product
        'well_record': w1['kind'] == KINDS['well'] and w1['data']['FacilityID'] == '42-329-12345' and any(a['AliasName'] == '123456' for a in w1['data']['NameAliases'])
                       and w1['data']['VerticalMeasurements'][0]['VerticalMeasurement'] == 9800.0 and w1['acl']['owners'] == full.acl_owners
                       and w1['legal']['legaltags'] == ['opendes-public-usa'],
        'both_coordinates': sl['AsIngestedCoordinates']['CoordinateReferenceSystemID'].endswith('EPSG::4267:')
                            and sl['AsIngestedCoordinates']['features'][0]['geometry']['coordinates'] == [-102.24, 31.96]
                            and abs(sl['Wgs84Coordinates']['features'][0]['geometry']['coordinates'][1] - 31.96) < 0.01
                            and sl['Wgs84Coordinates']['features'][0]['geometry']['coordinates'] != [-102.24, 31.96]
                            and 'NAD27 to WGS 84' in sl['AppliedOperations'][0] and 'shift' in sl['AppliedOperations'][0],
        'unknown_datum_no_wgs84': 'Wgs84Coordinates' not in w2['data']['SpatialLocation'] and any('W-2' in g and 'datum unknown' in g for g in s_full['gaps']),
        'wellbore_links_well': any(m['kind'] == KINDS['wellbore'] and m['data']['WellID'] == w1['id'] for m in man['MasterData']),
        'welllog_time_domain': log['data']['SamplingDomainTypeID'].endswith(':Time:') and log['data']['ZeroTime'] == '2026-03-01T00:00:00Z' and log['data']['SamplingInterval'] == 60.0
                               and [c['Mnemonic'] for c in log['data']['Curves']] == ['TIME', 'P_psi', 'T_degF'] and log['data']['Curves'][1]['CurveUnit'].endswith(':psi:')
                               and log['data']['Datasets'] == [ds['id']] and log['data']['WellboreID'] == 'surrogate-key:wellbore-W-1' and log['data']['IsRegular'],
        'dataset_file': ds['kind'] == KINDS['file'] and ds['data']['DatasetProperties']['FileSourceInfo']['Checksum'] == hashlib.sha256(open(src, 'rb').read()).hexdigest()
                        and ds['data']['DatasetProperties']['FileSourceInfo']['FileSource'] == 'files/wells/W-1/hist.csv' and os.path.isfile(os.path.join(out, 'files', 'wells', 'W-1', 'hist.csv'))
                        and ds['data']['EncodingFormatTypeID'].endswith('text%2Fcsv:'),
        'station_generic': any(c['kind'] == KINDS['wpc_generic'] and c['data']['Datasets'] and 'SpatialPoint' in c['data'] for c in man['Data']['WorkProductComponents']),
        'catalog_well_gap': any('W-3' in g and 'no file' in g for g in s_full['gaps']),
        'bare_not_loadable': s_bare['status'] == 'NOT LOADABLE' and len(s_bare['missing_declarations']) == 4 and s_full['status'] == 'LOADABLE',
    }
    return {'label': 'SIMULATION_SELF_TEST', 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks, 'summary': s_full, 'validate': v, 'bare': s_bare}


def report_text(s: dict) -> str:
    c = s['counts']
    lines = [f"OSDU export: site {s['site']}  [{s['status']}]  {s['kind']}",
             f"  {c['master_data']} master-data record(s) ({c['wells']} well(s) as Well + Wellbore), {c['work_product_components']} work-product component(s), "
             f"{c['datasets']} dataset(s); {c['stations']} seismic station(s)",
             f"  manifest: {s['manifest']}"]
    if s['missing_declarations']:
        lines.append('  NOT LOADABLE until the operator declares: ' + '; '.join(s['missing_declarations']))
    if s['gaps']:
        lines.append(f"  gaps ({len(s['gaps'])}):"); lines += [f"    - {g}" for g in s['gaps']]
    return '\n'.join(lines)
