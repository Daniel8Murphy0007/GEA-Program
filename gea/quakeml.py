# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""quakeml - the site's own events in the catalogue format the regulator's tools already read.

The association leg (v0.10.0) ends with a list of events the site's stations located together: an origin
time, a position with its misfit region, a declared depth, the picks that made it and their residuals, and
the standing of each against the catalogue the operator handed in. Until now that list was a JSON file of
this program's own shape and a section of the SRA packet. A regulator's catalogue tool, a university
network's review desk and every seismological package read one exchange format for exactly this content:
QuakeML 1.2, the Basic Event Description. This module writes it.

Built to the published schema (QuakeML-BED-1.2.xsd), record by record:

- one `event` per associated event, with its `origin`, one `pick` per station that made it, and one
  `arrival` per pick on the origin, each with a resource identifier of the schema's own pattern;
- the origin's `quality` carries the counts and the RMS the association computed; its `originUncertainty`
  carries the misfit region as a horizontal uncertainty; its `depthType` says "operator assigned", because the
  depth was declared, not located;
- `evaluationMode` automatic and `evaluationStatus` preliminary on every origin and pick: nothing here has
  been reviewed by a person, and the file says so in the schema's own words;
- the catalogue's standing - AGREES, DIFFERS with both positions, NOT IN CATALOGUE - as a comment on the
  event, with the catalogue's own event id where there is one.

Units as the schema states them: depth and horizontal uncertainty in metres, latitude and longitude and
their uncertainties in degrees, epicentral distance and azimuth in degrees, times and residuals in seconds.

What this module will not write:

- a `magnitude`: the association does not estimate one, so the file carries none and a comment says why;
- the catalogue's events as this site's: they are the catalogue's to publish; this file names them in
  comments and carries only what the site's own stations located;
- an event under `reviewed` or `final`: a person has not looked at it, and the file does not say one has;
- a trace or waveform: QuakeML describes events, and the records stay where they are.
"""

from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

NS_Q = 'http://quakeml.org/xmlns/quakeml/1.2'
NS_BED = 'http://quakeml.org/xmlns/bed/1.2'
SCHEMA = 'QuakeML-BED-1.2'
# the schema's ResourceIdentifier: (smi|quakeml):<authority, 3+ chars>/<path>
RID_PATTERN = re.compile(r"^(smi|quakeml):[\w\d][\w\d\-\.\*\(\)_~']{2,}/[\w\d\-\.\*\(\)_~'][\w\d\-\.\*\(\)\+\?_~'=,;#/&]*$")
AUTHORITY = 'local'
EVALUATION_MODE = ('manual', 'automatic')
EVALUATION_STATUS = ('preliminary', 'confirmed', 'reviewed', 'final', 'rejected')
DEPTH_TYPES = ('from location', 'from moment tensor inversion', 'from modeling of broad-band P waveforms', 'constrained by depth phases',
               'constrained by direct phases', 'constrained by depth and direct phases', 'operator assigned', 'other')
KM_PER_DEG = 111.195          # mean great-circle kilometres per degree, used only to state uncertainties in the schema's degrees

ET.register_namespace('q', NS_Q)
ET.register_namespace('', NS_BED)


def _b(tag: str) -> str:
    return '{%s}%s' % (NS_BED, tag)


def _clean(s: str) -> str:
    """A path segment the identifier pattern accepts: anything else becomes '_'."""
    return re.sub(r"[^\w\d\-\.\*\(\)_~']", '_', str(s)) or '_'


def rid(*parts: str, authority: str = AUTHORITY) -> str:
    """smi:<authority>/gea/<part>/<part>...: the schema's own pattern, built from pieces that are cleaned to it."""
    auth = _clean(authority)
    if len(auth) < 3:
        auth = (auth + '___')[:3]
    return 'smi:' + auth + '/gea/' + '/'.join(_clean(p) for p in parts)


def _iso_z(t: Any) -> str:
    """An xs:dateTime in UTC with a Z, from an ISO string the program wrote or an epoch."""
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(float(t), tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
    s = str(t).strip().replace(' ', 'T')
    if s.endswith('Z'):
        s = s[:-1]
    s = re.sub(r'[+-]\d\d:?\d\d$', '', s)
    return s + 'Z'


def _num(el: ET.Element, tag: str, v: Any, nd: int = 6) -> ET.Element:
    e = ET.SubElement(el, _b(tag))
    e.text = str(int(v)) if isinstance(v, bool) or (isinstance(v, int) and not isinstance(v, bool)) else f'{float(v):.{nd}f}'.rstrip('0').rstrip('.')
    if e.text in ('', '-0'):
        e.text = '0'
    return e


def _text(el: ET.Element, tag: str, v: Any) -> ET.Element:
    e = ET.SubElement(el, _b(tag))
    e.text = str(v)
    return e


def _quantity(el: ET.Element, tag: str, value: Any, uncertainty: Optional[float] = None, time: bool = False, nd: int = 6) -> ET.Element:
    q = ET.SubElement(el, _b(tag))
    if time:
        _text(q, 'value', _iso_z(value))
    else:
        _num(q, 'value', value, nd)
    if uncertainty is not None:
        _num(q, 'uncertainty', uncertainty, nd)
    return q


def _comment(el: ET.Element, text: str, cid: Optional[str] = None) -> ET.Element:
    c = ET.SubElement(el, _b('comment'))
    if cid:
        c.set('id', cid)
    _text(c, 'text', text)
    return c


def _creation(el: ET.Element, agency: str, author: str, when: str, version: str) -> ET.Element:
    c = ET.SubElement(el, _b('creationInfo'))
    _text(c, 'agencyID', agency[:64])
    _text(c, 'author', author[:128])
    _text(c, 'creationTime', when)
    _text(c, 'version', version[:64])
    return c


# --------------------------------------------------------------------------------------------------------------
# the stations' stream codes
# --------------------------------------------------------------------------------------------------------------
def stream_codes(station: dict) -> dict:
    """What goes into a waveformID: the FDSN codes of the record the station was picked on. From the station
    record's own codes when the association wrote them; otherwise the station id as the station code under
    the test network 'XX', and the gap is named rather than a code invented."""
    codes = station.get('stream') or {}
    net = (codes.get('network') or '').strip()
    sta = (codes.get('station') or '').strip()
    out = {'networkCode': (net or 'XX')[:8], 'stationCode': (sta or str(station.get('id') or 'STA'))[:8],
           'locationCode': (codes.get('location') or '')[:8], 'channelCode': (codes.get('channel') or '')[:8],
           'from_record': bool(net and sta)}
    return out


# --------------------------------------------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------------------------------------------
def build(assoc_doc: dict, site: dict, stations: Dict[str, dict], agency: str = '', author: str = '', version: str = '',
          generated_utc: Optional[str] = None, authority: str = AUTHORITY) -> dict:
    """The association document (what `workspace --action associate` wrote) as a QuakeML 1.2 tree.

    `stations` maps station id -> its record (lat, lon, datum, kind, optional stream codes). Returns the tree,
    the counts, and every gap the file names rather than fills."""
    from . import geodesy as G
    site_id = str(site.get('id') or 'site')
    site_name = str(site.get('display') or site.get('name') or site_id)
    agency = agency or site_name
    author = author or f'gea-program {version}'.strip()
    now = generated_utc or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    assoc = assoc_doc.get('association') or {}
    cmp = assoc_doc.get('catalogue') or {}
    standing = {r.get('event'): r for r in (cmp.get('rows') or [])}
    model = assoc.get('model') or {}
    gaps: List[str] = []

    root = ET.Element('{%s}quakeml' % NS_Q)
    ep = ET.SubElement(root, _b('eventParameters'))
    ep.set('publicID', rid(site_id, 'catalogue', now.replace(':', ''), authority=authority))
    _text(ep, 'description', f"Events located by the stations of site '{site_name}' under a declared model; automatic, preliminary, "
                             f"unreviewed. Magnitudes are not estimated. Written by gea-program {version}.")
    _comment(ep, 'basis: ' + str(assoc.get('basis') or 'one pick per station per event; a grid search under a declared model'),
             rid(site_id, 'comment', 'basis', authority=authority))
    for i, item in enumerate(assoc.get('not_a_measurement') or []):
        _comment(ep, 'not a measurement: ' + str(item), rid(site_id, 'comment', f'not-a-measurement-{i + 1}', authority=authority))
    if cmp.get('catalog'):
        c = cmp['catalog']
        _comment(ep, f"compared against the catalogue export {os.path.basename(str(c.get('path') or ''))} ({c.get('n_events')} event(s), "
                     f"exported {c.get('export_mtime_utc')}); the catalogue's events are the catalogue's to publish and are not written here",
                 rid(site_id, 'comment', 'catalogue', authority=authority))
    _creation(ep, agency, author, now, version)

    n_events = n_picks = n_arrivals = 0
    for ev in assoc.get('events') or []:
        eid = str(ev['id'])
        e = ET.SubElement(ep, _b('event'))
        e.set('publicID', rid(site_id, 'event', eid, authority=authority))
        oid = rid(site_id, 'origin', eid, authority=authority)
        # description: the schema's EventDescription with a free text and no type (none of the enumerated types fits)
        d = ET.SubElement(e, _b('description'))
        _text(d, 'text', f"located by {ev.get('n_stations')} station(s) of site '{site_name}'; RMS {ev.get('rms_s')} s")
        _text(e, 'preferredOriginID', oid)
        st_row = standing.get(eid)
        if st_row:
            if st_row.get('catalog_event'):
                _comment(e, f"catalogue: {st_row.get('standing')} - catalogue event {st_row.get('catalog_event')} at {st_row.get('catalog_time')}"
                            + (f", magnitude {st_row.get('catalog_magnitude')}" if st_row.get('catalog_magnitude') is not None else '')
                            + f"; {st_row.get('detail')}", rid(site_id, 'comment', eid, 'catalogue', authority=authority))
            else:
                _comment(e, f"catalogue: {st_row.get('standing')} - {st_row.get('detail')}", rid(site_id, 'comment', eid, 'catalogue', authority=authority))
        _comment(e, 'magnitude: ' + str(ev.get('magnitude_basis') or 'not estimated'), rid(site_id, 'comment', eid, 'magnitude', authority=authority))
        if ev.get('dropped'):
            _comment(e, 'picks dropped for their residual: ' + ', '.join(str(x) for x in ev['dropped']), rid(site_id, 'comment', eid, 'dropped', authority=authority))
        # type: left out. The association does not classify an event, and 'not reported' would be a statement about a report that was not made.
        _creation(e, agency, author, now, version)

        # -- the origin --------------------------------------------------------------------------------------------
        o = ET.SubElement(e, _b('origin'))
        o.set('publicID', oid)
        region = ev.get('region_km') or {}
        half_e = 0.5 * float(region.get('extent_east_km') or 0.0)
        half_n = 0.5 * float(region.get('extent_north_km') or 0.0)
        lat, lon = float(ev['lat']), float(ev['lon'])
        _quantity(o, 'time', ev['origin_utc'], time=True)
        _quantity(o, 'latitude', lat, round(half_n / KM_PER_DEG, 6) if half_n else None)
        _quantity(o, 'longitude', lon, round(half_e / (KM_PER_DEG * max(math.cos(math.radians(lat)), 1e-6)), 6) if half_e else None)
        _quantity(o, 'depth', float(ev.get('depth_km') or 0.0) * 1000.0, nd=1)
        _text(o, 'depthType', 'operator assigned' if (ev.get('depth_basis') or 'declared') == 'declared' else 'from location')
        _text(o, 'epicenterFixed', 'false')
        _text(o, 'timeFixed', 'false')
        m = ev.get('model') or model
        _text(o, 'methodID', rid('method', 'grid-search-straight-ray', authority=authority))
        _text(o, 'earthModelID', rid('earthmodel', f"vp-{m.get('vp_km_s')}-kms-flat-depth-{m.get('depth_km')}-km", authority=authority))
        # the picks and the arrivals: one per station that made the event
        picks = ev.get('picks') or {}
        az_list, dist_list = [], []
        arrivals = []
        for sid in sorted(picks):
            pk = picks[sid]
            srec = stations.get(sid) or {'id': sid}
            codes = stream_codes(srec)
            if not codes['from_record']:
                gaps.append(f"{eid}/{sid}: no network and station code on the record; written as {codes['networkCode']}.{codes['stationCode']}")
            pid = rid(site_id, 'pick', eid, sid, authority=authority)
            p = ET.SubElement(e, _b('pick'))
            p.set('publicID', pid)
            _quantity(p, 'time', pk['time'], float(m.get('pick_sigma_s')) if m.get('pick_sigma_s') is not None else None, time=True, nd=3)
            w = ET.SubElement(p, _b('waveformID'))
            for k in ('networkCode', 'stationCode', 'locationCode', 'channelCode'):
                if codes[k] or k in ('networkCode', 'stationCode'):
                    w.set(k, codes[k])
            _text(p, 'methodID', rid('method', 'sta-lta-onset', authority=authority))
            _text(p, 'phaseHint', 'P')
            _text(p, 'evaluationMode', 'automatic')
            _text(p, 'evaluationStatus', 'preliminary')
            if pk.get('snr') is not None:
                _comment(p, f"STA/LTA at onset: {pk.get('snr')}", rid(site_id, 'comment', eid, sid, 'snr', authority=authority))
            _creation(p, agency, author, now, version)
            n_picks += 1
            # the arrival: the station as seen from the epicentre
            if srec.get('lat') is not None and srec.get('lon') is not None:
                sp = G.to_wgs84(float(srec['lat']), float(srec['lon']), 0.0, G.datum_name(srec.get('datum') or 'WGS84'))
                g = G.geodesic_m(lat, lon, sp['lat'], sp['lon'])
                az = g.get('azimuth_deg'); dist_deg = (g['distance_m'] / 1000.0) / KM_PER_DEG if g.get('distance_m') is not None else None
            else:
                az = dist_deg = None
                gaps.append(f'{eid}/{sid}: the station has no position; its arrival carries no azimuth or distance')
            arrivals.append((sid, pid, pk, az, dist_deg))
            if az is not None:
                az_list.append(az)
            if dist_deg is not None:
                dist_list.append(dist_deg)
        for sid, pid, pk, az, dist_deg in arrivals:
            a = ET.SubElement(o, _b('arrival'))
            a.set('publicID', rid(site_id, 'arrival', eid, sid, authority=authority))
            _text(a, 'pickID', pid)
            _text(a, 'phase', 'P')
            if az is not None:
                _num(a, 'azimuth', az, 2)
            if dist_deg is not None:
                _num(a, 'distance', dist_deg, 5)
            if pk.get('residual_s') is not None:
                _num(a, 'timeResidual', pk['residual_s'], 3)
            _num(a, 'timeWeight', 1.0, 1)
            n_arrivals += 1
        # quality: what the association computed, and the geometry that follows from it
        q = ET.SubElement(o, _b('quality'))
        n_used = len(picks)
        _num(q, 'associatedPhaseCount', n_used + len(ev.get('dropped') or []))
        _num(q, 'usedPhaseCount', n_used)
        _num(q, 'associatedStationCount', n_used + len(ev.get('dropped') or []))
        _num(q, 'usedStationCount', n_used)
        if ev.get('rms_s') is not None:
            _num(q, 'standardError', ev['rms_s'], 3)
        if len(az_list) >= 2:
            s_az = sorted(a % 360.0 for a in az_list)
            gapsz = [(s_az[i + 1] - s_az[i]) for i in range(len(s_az) - 1)] + [360.0 - s_az[-1] + s_az[0]]
            _num(q, 'azimuthalGap', max(gapsz), 1)
        if dist_list:
            _num(q, 'minimumDistance', min(dist_list), 5)
            _num(q, 'maximumDistance', max(dist_list), 5)
            _num(q, 'medianDistance', sorted(dist_list)[len(dist_list) // 2], 5)
        # the misfit region as the schema's horizontal uncertainty: half the larger extent, in metres
        ou = ET.SubElement(o, _b('originUncertainty'))
        _num(ou, 'horizontalUncertainty', max(half_e, half_n) * 1000.0, 1)
        _num(ou, 'minHorizontalUncertainty', min(half_e, half_n) * 1000.0, 1)
        _num(ou, 'maxHorizontalUncertainty', max(half_e, half_n) * 1000.0, 1)
        _num(ou, 'azimuthMaxHorizontalUncertainty', 0.0 if half_n >= half_e else 90.0, 1)
        _text(ou, 'preferredDescription', 'uncertainty ellipse')
        _comment(o, f"misfit region: nodes within one pick uncertainty ({m.get('pick_sigma_s')} s) of the best; {region.get('extent_east_km')} km east-west by "
                    f"{region.get('extent_north_km')} km north-south, {region.get('nodes')} node(s)"
                    + ('; reaches the grid edge' if region.get('reaches_grid_edge') else ''), rid(site_id, 'comment', eid, 'region', authority=authority))
        _comment(o, 'model: ' + str(m.get('basis') or 'a straight ray at one velocity on a flat earth, the hypocentre at the declared depth'),
                 rid(site_id, 'comment', eid, 'model', authority=authority))
        _text(o, 'evaluationMode', 'automatic')
        _text(o, 'evaluationStatus', 'preliminary')
        _creation(o, agency, author, now, version)
        n_events += 1

    return {'tree': ET.ElementTree(root), 'n_events': n_events, 'n_picks': n_picks, 'n_arrivals': n_arrivals, 'gaps': gaps,
            'agency': agency, 'author': author, 'generated_utc': now,
            'refused': len(assoc.get('refused') or []), 'unused_picks': assoc.get('n_unused_picks')}


def write(tree: ET.ElementTree, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    ET.indent(tree, space=' ')
    tree.write(path, encoding='utf-8', xml_declaration=True)
    return path


# --------------------------------------------------------------------------------------------------------------
# validation: the first things a QuakeML reader checks, made here first
# --------------------------------------------------------------------------------------------------------------
_ALLOWED = {
    'eventParameters': {'comment', 'event', 'description', 'creationInfo'},
    'event': {'description', 'comment', 'focalMechanism', 'amplitude', 'magnitude', 'stationMagnitude', 'origin', 'pick',
              'preferredOriginID', 'preferredMagnitudeID', 'preferredFocalMechanismID', 'type', 'typeCertainty', 'creationInfo'},
    'origin': {'compositeTime', 'comment', 'originUncertainty', 'arrival', 'time', 'longitude', 'latitude', 'depth', 'depthType', 'timeFixed',
               'epicenterFixed', 'referenceSystemID', 'methodID', 'earthModelID', 'quality', 'type', 'region', 'evaluationMode',
               'evaluationStatus', 'creationInfo'},
    'pick': {'comment', 'time', 'waveformID', 'filterID', 'methodID', 'horizontalSlowness', 'backazimuth', 'slownessMethodID', 'onset',
             'phaseHint', 'polarity', 'evaluationMode', 'evaluationStatus', 'creationInfo'},
    'arrival': {'comment', 'pickID', 'phase', 'timeCorrection', 'azimuth', 'distance', 'takeoffAngle', 'timeResidual',
                'horizontalSlownessResidual', 'backazimuthResidual', 'timeWeight', 'horizontalSlownessWeight', 'backazimuthWeight',
                'earthModelID', 'creationInfo'},
    'quality': {'associatedPhaseCount', 'usedPhaseCount', 'associatedStationCount', 'usedStationCount', 'depthPhaseCount', 'standardError',
                'azimuthalGap', 'secondaryAzimuthalGap', 'groundTruthLevel', 'maximumDistance', 'minimumDistance', 'medianDistance'},
    'originUncertainty': {'horizontalUncertainty', 'minHorizontalUncertainty', 'maxHorizontalUncertainty', 'azimuthMaxHorizontalUncertainty',
                          'confidenceEllipsoid', 'preferredDescription', 'confidenceLevel'},
    'creationInfo': {'agencyID', 'agencyURI', 'author', 'authorURI', 'creationTime', 'version'},
}
_DATETIME = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$')


def validate(path_or_tree: Any) -> dict:
    """The structural checks a reader makes first: the namespaces, every publicID to the schema's pattern,
    every child element one the schema allows for its parent, the required children present, the
    enumerations, every arrival's pick in the same event, the preferred origin present, every dateTime a
    dateTime, every depth a number. Not a substitute for a schema validator - the first things it would
    say, said here, so the file does not leave the site to find them out."""
    tree = path_or_tree if isinstance(path_or_tree, ET.ElementTree) else ET.parse(path_or_tree)
    root = tree.getroot()
    problems: List[str] = []
    ids: Dict[str, int] = {}

    def local(el: ET.Element) -> str:
        return el.tag.split('}', 1)[1] if '}' in el.tag else el.tag

    def check_children(el: ET.Element, kind: str) -> None:
        for ch in el:
            if not ch.tag.startswith('{%s}' % NS_BED):
                problems.append(f'{kind}: child {ch.tag} is not in the BED namespace')
            elif local(ch) not in _ALLOWED[kind]:
                problems.append(f'{kind}: child <{local(ch)}> is not in the schema')

    def check_id(el: ET.Element, kind: str) -> None:
        pid = el.get('publicID')
        if not pid:
            problems.append(f'{kind}: no publicID')
        elif not RID_PATTERN.match(pid):
            problems.append(f'{kind}: publicID {pid!r} does not match the ResourceIdentifier pattern')
        else:
            ids[pid] = ids.get(pid, 0) + 1

    def check_quantity(el: Optional[ET.Element], kind: str, time: bool = False) -> None:
        if el is None:
            return
        v = el.find(_b('value'))
        if v is None or not (v.text or '').strip():
            problems.append(f'{kind}: no value'); return
        if time:
            if not _DATETIME.match(v.text.strip()):
                problems.append(f'{kind}: {v.text!r} is not an xs:dateTime in UTC')
        else:
            try:
                float(v.text)
            except ValueError:
                problems.append(f'{kind}: {v.text!r} is not a number')

    if root.tag != '{%s}quakeml' % NS_Q:
        problems.append(f'root is {root.tag}, not q:quakeml')
    ep = root.find(_b('eventParameters'))
    n_events = n_origins = n_picks = n_arrivals = 0
    if ep is None:
        problems.append('no eventParameters')
    else:
        check_id(ep, 'eventParameters'); check_children(ep, 'eventParameters')
        for ev in ep.findall(_b('event')):
            n_events += 1
            check_id(ev, 'event'); check_children(ev, 'event')
            origins = {o.get('publicID'): o for o in ev.findall(_b('origin'))}
            picks = {p.get('publicID') for p in ev.findall(_b('pick'))}
            pref = ev.find(_b('preferredOriginID'))
            if pref is None or pref.text not in origins:
                problems.append(f"event {ev.get('publicID')}: preferredOriginID missing or not an origin of this event")
            if ev.find(_b('magnitude')) is not None:
                problems.append(f"event {ev.get('publicID')}: carries a magnitude this program does not estimate")
            for o in origins.values():
                n_origins += 1
                check_id(o, 'origin'); check_children(o, 'origin')
                for tag in ('time', 'latitude', 'longitude'):
                    if o.find(_b(tag)) is None:
                        problems.append(f"origin {o.get('publicID')}: no {tag}")
                check_quantity(o.find(_b('time')), 'origin time', time=True)
                check_quantity(o.find(_b('latitude')), 'origin latitude'); check_quantity(o.find(_b('longitude')), 'origin longitude')
                check_quantity(o.find(_b('depth')), 'origin depth')
                for tag, allowed in (('depthType', DEPTH_TYPES), ('evaluationMode', EVALUATION_MODE), ('evaluationStatus', EVALUATION_STATUS)):
                    x = o.find(_b(tag))
                    if x is not None and x.text not in allowed:
                        problems.append(f"origin {o.get('publicID')}: {tag} {x.text!r} is not in the enumeration")
                q = o.find(_b('quality'))
                if q is not None:
                    check_children(q, 'quality')
                ou = o.find(_b('originUncertainty'))
                if ou is not None:
                    check_children(ou, 'originUncertainty')
                for a in o.findall(_b('arrival')):
                    n_arrivals += 1
                    check_id(a, 'arrival'); check_children(a, 'arrival')
                    pk = a.find(_b('pickID')); ph = a.find(_b('phase'))
                    if pk is None or pk.text not in picks:
                        problems.append(f"arrival {a.get('publicID')}: pickID missing or not a pick of this event")
                    if ph is None or not (ph.text or '').strip():
                        problems.append(f"arrival {a.get('publicID')}: no phase")
                for ci in o.findall(_b('creationInfo')):
                    check_children(ci, 'creationInfo')
            for p in ev.findall(_b('pick')):
                n_picks += 1
                check_id(p, 'pick'); check_children(p, 'pick')
                check_quantity(p.find(_b('time')), 'pick time', time=True)
                w = p.find(_b('waveformID'))
                if w is None or not w.get('networkCode') or not w.get('stationCode'):
                    problems.append(f"pick {p.get('publicID')}: waveformID missing or without networkCode and stationCode")
                elif any(len(w.get(k) or '') > 8 for k in ('networkCode', 'stationCode', 'locationCode', 'channelCode')):
                    problems.append(f"pick {p.get('publicID')}: a waveformID code is longer than 8 characters")
                for tag, allowed in (('evaluationMode', EVALUATION_MODE), ('evaluationStatus', EVALUATION_STATUS)):
                    x = p.find(_b(tag))
                    if x is not None and x.text not in allowed:
                        problems.append(f"pick {p.get('publicID')}: {tag} {x.text!r} is not in the enumeration")
    dup = [k for k, n in ids.items() if n > 1]
    if dup:
        problems.append(f'{len(dup)} publicID(s) used more than once: {dup[:3]}')
    return {'protocol': 'quakeml.validate/1', 'status': 'VALID_SHAPE' if not problems else 'INVALID', 'schema': SCHEMA,
            'events': n_events, 'origins': n_origins, 'picks': n_picks, 'arrivals': n_arrivals, 'ids': len(ids), 'problems': problems,
            'basis': 'the first checks a QuakeML reader makes: namespaces, identifiers, allowed children, required children, enumerations, references',
            'not_a_measurement': ['a full schema validation: this is the shape, read the way a reader reads it first']}


# --------------------------------------------------------------------------------------------------------------
# the self-test on the labelled scene
# --------------------------------------------------------------------------------------------------------------
def selftest(out_dir: Optional[str] = None) -> dict:
    """The association leg's own labelled scene, written as QuakeML and read back: two events, five picks and
    five arrivals each, one event AGREES with the catalogue and one DIFFERS, no magnitude anywhere, every
    identifier to the pattern, every arrival on a pick of its own event."""
    import tempfile
    from . import seismic_assoc as SA
    from . import __version__
    sc = SA.synthetic_scene()
    all_picks: List[SA.Pick] = []
    for sid, tr in sc['traces'].items():
        all_picks += SA.picks(tr, band=(2.0, 20.0))
    res = SA.associate(all_picks, sc['stations'], sc['vp'], sc['depth_km'], frame_info=sc['frame'])
    cmp = SA.compare_catalog(res, sc['catalog'])
    cmp['catalog'] = {'path': 'synthetic_catalogue.csv', 'export_mtime_utc': '2026-03-05T12:00:00Z', 'n_events': len(sc['catalog'])}
    doc = {'protocol': 'workspace.association/1', 'site': 'SYNTHETIC', 'association': res, 'catalogue': cmp}
    stations = {sid: {'id': sid, 'lat': s.lat, 'lon': s.lon, 'datum': s.datum, 'kind': s.kind,
                      'stream': {'network': sc['traces'][sid].network, 'station': sc['traces'][sid].station,
                                 'location': sc['traces'][sid].location, 'channel': sc['traces'][sid].channel}}
                for sid, s in sc['stations'].items()}
    b = build(doc, {'id': 'SYNTHETIC', 'name': 'SIMULATION_SELF_TEST site'}, stations, version=__version__,
              generated_utc='2026-03-05T12:00:00Z')
    d = out_dir or tempfile.mkdtemp(prefix='gea-quakeml-')
    path = write(b['tree'], os.path.join(d, 'SYNTHETIC_events.xml'))
    v = validate(path)
    back = ET.parse(path).getroot()
    text = open(path, encoding='utf-8').read()
    evs = back.findall('.//' + _b('event'))
    standings = [c.find(_b('text')).text for ev in evs for c in ev.findall(_b('comment')) if (c.find(_b('text')).text or '').startswith('catalogue:')]
    checks = {
        'two_events': v['events'] == 2 and res['n_events'] == 2,
        'five_picks_each': v['picks'] == 10 and v['arrivals'] == 10,
        'valid_shape': v['status'] == 'VALID_SHAPE',
        'no_magnitude': '<magnitude' not in text and all(ev.find(_b('magnitude')) is None for ev in evs),
        'ids_to_pattern': all(RID_PATTERN.match(x.get('publicID')) for x in back.iter() if x.get('publicID')),
        'depth_in_metres': all(abs(float(o.find(_b('depth')).find(_b('value')).text) - sc['depth_km'] * 1000.0) < 1e-6
                               for o in back.findall('.//' + _b('origin'))),
        'declared_depth_named': all(o.find(_b('depthType')).text == 'operator assigned' for o in back.findall('.//' + _b('origin'))),
        'automatic_preliminary': all(x.find(_b('evaluationMode')).text == 'automatic' and x.find(_b('evaluationStatus')).text == 'preliminary'
                                     for x in list(back.findall('.//' + _b('origin'))) + list(back.findall('.//' + _b('pick')))),
        'catalogue_standing_written': sum('AGREES' in s for s in standings) == 1 and sum('DIFFERS' in s for s in standings) == 1,
        'stream_codes_from_record': all(w.get('networkCode') == 'XX' and w.get('channelCode') == 'HHZ' for w in back.findall('.//' + _b('waveformID'))),
        'no_gaps': not b['gaps'],
        'namespaces': text.count('http://quakeml.org/xmlns/quakeml/1.2') == 1 and 'http://quakeml.org/xmlns/bed/1.2' in text,
    }
    return {'label': sc['label'], 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks, 'path': path,
            'validate': v, 'n_events': b['n_events'], 'n_picks': b['n_picks'], 'n_arrivals': b['n_arrivals'], 'gaps': b['gaps']}


def report_text(summary: dict) -> str:
    """What `gea workspace --action quakeml-export` prints."""
    v = summary.get('validate') or {}
    lines = [f"quakeml-export: {summary.get('status')} - {summary.get('n_events')} event(s), {summary.get('n_picks')} pick(s), "
             f"{summary.get('n_arrivals')} arrival(s) -> {summary.get('path')}",
             f"   schema {SCHEMA}; shape {v.get('status')} ({v.get('ids')} identifier(s)" + (f", {len(v.get('problems') or [])} problem(s)" if v.get('problems') else '') + ')',
             f"   agency {summary.get('agency')}; every origin and pick automatic and preliminary; no magnitude written",
             f"   not written: {summary.get('refused')} candidate set(s) the association did not accept; the catalogue's own events"]
    for g in summary.get('gaps') or []:
        lines.append(f'   gap: {g}')
    for p in (v.get('problems') or [])[:10]:
        lines.append(f'   problem: {p}')
    return '\n'.join(lines)
