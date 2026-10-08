# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""ppdm - the well named the way the operator's master data names it.

Every report this program writes names a well by the display name the operator typed and, since v0.9.0, by
the API and UIC numbers declared on it. A buyer's data team, a regulator's well file and the operator's own
master data name it differently and more carefully: by the US Well Number (the API number, whose standard
the API transferred to the PPDM Association in 2010; PPDM's 2013 standard is its successor and requires
every wellbore to be identified), decomposed into the state, the county, the unique well, the directional
sidetrack and the event sequence; and by the components of PPDM's "What is a Well": the Well, its one Well
Origin, each Wellbore, the Wellbore Segments, the Wellbore Contact Intervals and Completions, the Wellhead
Streams, and the Well Set that keeps them together across the well's life.

This module does two things and refuses a third:

- it reads a US Well Number in any of its forms (10, 12 or 14 digits, with or without dashes), names each
  part and what it means, says which component of the well it identifies (ten digits the Well Origin, twelve
  the Wellbore, fourteen the event), and names every way it is not a valid number;
- it writes the well's identity as the components the site can name from what the operator declared and
  what the program holds: the Well and its Origin at the declared surface position, the Wellbore when the
  sidetrack code was given, the Wellhead Stream as the channels measured at the wellhead, the gauge station
  as a measured depth along the wellbore, the Well Set as the site, and the aliases (the API number, the UIC
  permit, the operator's name for it) each typed;
- it will not assume a sidetrack code that was not given (a ten-digit number identifies the Well Origin and
  nothing below it; "00" is the original hole only when the regulator assigned it), will not invent a contact
  interval or a completion (those are the permit's and the completion report's facts, not held here), and
  will not call a county code valid on the strength of its shape alone - the county list is the standard's
  booklet, not this file, and the file says so.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# the state and offshore pseudo-state codes of the US Well Number (API D12A; PPDM US Well Number Standard).
# These are not the FIPS codes.
STATE_CODES: Dict[str, str] = {
    '01': 'Alabama', '02': 'Arizona', '03': 'Arkansas', '04': 'California', '05': 'Colorado', '06': 'Connecticut', '07': 'Delaware',
    '08': 'District of Columbia', '09': 'Florida', '10': 'Georgia', '11': 'Idaho', '12': 'Illinois', '13': 'Indiana', '14': 'Iowa',
    '15': 'Kansas', '16': 'Kentucky', '17': 'Louisiana', '18': 'Maine', '19': 'Maryland', '20': 'Massachusetts', '21': 'Michigan',
    '22': 'Minnesota', '23': 'Mississippi', '24': 'Missouri', '25': 'Montana', '26': 'Nebraska', '27': 'Nevada', '28': 'New Hampshire',
    '29': 'New Jersey', '30': 'New Mexico', '31': 'New York', '32': 'North Carolina', '33': 'North Dakota', '34': 'Ohio', '35': 'Oklahoma',
    '36': 'Oregon', '37': 'Pennsylvania', '38': 'Rhode Island', '39': 'South Carolina', '40': 'South Dakota', '41': 'Tennessee',
    '42': 'Texas', '43': 'Utah', '44': 'Vermont', '45': 'Virginia', '46': 'Washington', '47': 'West Virginia', '48': 'Wisconsin',
    '49': 'Wyoming', '50': 'Alaska', '51': 'Hawaii',
    '55': 'Alaska Offshore', '56': 'Pacific Coast Offshore', '60': 'Northern Gulf of Mexico', '61': 'Atlantic Coast Offshore',
}
RESERVED_STATE_CODES = ('52', '53', '54')
# the unique-well ranges the standard names
UNIQUE_RANGES = (
    (1, 20000, 'historical', 'mostly wells drilled before 1967'),
    (20001, 60000, 'current', 'assigned by the state regulator at permitting'),
    (60001, 95000, 'reserved', 'assigned by a data vendor to wells the regulator did not number'),
    (95001, 99999, 'exempt', 'proprietary numbers not assigned by a regulator or a data vendor'),
)
FORMS = {10: 'API-10 (US Well Number, Well Origin)', 12: 'API-12 (Wellbore)', 14: 'API-14 (event)'}
COMPONENTS = {
    'well': 'a permitted or drilled hole meant to exchange fluids between a subsurface reservoir and the surface, or to measure rock properties',
    'well_origin': 'the location on the surface of the earth or sea bed where the drill bit is planned to or does penetrate the earth',
    'wellbore': 'a path of drilled footage from the Well Origin to a terminating point',
    'wellbore_segment': 'a unique drilled interval; every point in a Well is in one and only one Wellbore Segment',
    'wellbore_contact_interval': 'a physical section of a Wellbore allowing fluid flow through its wall',
    'wellbore_completion': 'one or more contact intervals that work together; a physical configuration, not an activity',
    'wellhead_stream': 'a fluid flow through a conduit set by the installed wellhead configuration',
    'well_set': 'a grouping that keeps a Well and its components together across its life cycle',
}


# --------------------------------------------------------------------------------------------------------------
# the US Well Number
# --------------------------------------------------------------------------------------------------------------
def parse_well_number(text: Any) -> dict:
    """A US Well Number in any of its written forms, taken apart and named.

    Ten digits identify the Well Origin (state, county, unique well); twelve add the directional sidetrack
    and identify a Wellbore; fourteen add the event sequence. Dashes, spaces and dots between the parts are
    accepted and ignored. Anything else is named as a problem, not repaired."""
    raw = '' if text is None else str(text).strip()
    out: Dict[str, Any] = {'protocol': 'ppdm.well_number/1', 'input': raw, 'status': 'INVALID', 'problems': [], 'notes': []}
    if not raw:
        out['problems'].append('no number given')
        return out
    if not re.fullmatch(r"[0-9][0-9 .\-]*[0-9]", raw) and not re.fullmatch(r'[0-9]+', raw):
        out['problems'].append('a US Well Number is digits, optionally separated by dashes; this has other characters')
    digits = re.sub(r'[^0-9]', '', raw)
    out['digits'] = digits
    n = len(digits)
    if n not in FORMS:
        out['problems'].append(f'{n} digit(s): a US Well Number has 10 (well origin), 12 (wellbore) or 14 (event) digits')
        return out
    out['form'] = FORMS[n]
    state, county, unique = digits[0:2], digits[2:5], digits[5:10]
    out['state'] = {'code': state, 'name': STATE_CODES.get(state)}
    if state in STATE_CODES:
        pass
    elif state in RESERVED_STATE_CODES:
        out['problems'].append(f'state code {state} is reserved for future states and names no state')
    else:
        out['problems'].append(f'state code {state} is not a state or offshore code of the standard')
    out['county'] = {'code': county, 'basis': "three digits; the standard's state and county code booklet says which county - it is not held here"}
    if county == '000':
        out['notes'].append('county code 000: the county is not given')
    u = int(unique)
    rng = next(((lo, hi, name, why) for lo, hi, name, why in UNIQUE_RANGES if lo <= u <= hi), None)
    out['unique_well'] = {'code': unique, 'range': rng[2] if rng else None, 'basis': rng[3] if rng else None}
    if u == 0:
        out['problems'].append('unique well number 00000 identifies no well')
    out['identifies'] = 'well_origin'
    if n >= 12:
        st = digits[10:12]
        out['sidetrack'] = {'code': st, 'meaning': 'the original hole' if st == '00' else f'directional sidetrack {int(st)}'}
        out['identifies'] = 'wellbore'
    if n == 14:
        ev = digits[12:14]
        out['event'] = {'code': ev, 'meaning': 'the original operation' if ev == '00' else f'event sequence {int(ev)} (a re-entry, recompletion or deepening)'}
        out['identifies'] = 'event'
    out['api10'] = f'{state}-{county}-{unique}'
    out['api12'] = f"{out['api10']}-{digits[10:12]}" if n >= 12 else None
    out['api14'] = f"{out['api12']}-{digits[12:14]}" if n == 14 else None
    out['status'] = 'VALID' if not out['problems'] else 'INVALID'
    out['basis'] = ('the US Well Number standard (PPDM, successor to API Bulletin D12A): SS-CCC-UUUUU-SS-EE; ten digits name the Well Origin, '
                    'twelve a Wellbore, fourteen an event')
    out['not_a_measurement'] = ['the county name: the standard\'s booklet carries it, this file does not',
                                'a sidetrack code that was not given: ten digits name the origin and nothing below it']
    return out


# --------------------------------------------------------------------------------------------------------------
# the well's identity as "What is a Well" components
# --------------------------------------------------------------------------------------------------------------
def well_identity(well: dict, site: Optional[dict] = None, channels: Optional[List[str]] = None) -> dict:
    """The components of PPDM's "What is a Well" the site can name for this well, from what the operator
    declared (set-well) and what the program holds; every component it cannot name is a gap by name."""
    disp = well.get('disposal') or {}
    surf = well.get('surface') or {}
    wid = well['id']
    api = parse_well_number(disp.get('api_number')) if disp.get('api_number') else None
    comps: Dict[str, Any] = {}
    gaps: List[str] = []
    aliases: List[dict] = [{'name': well.get('display') or wid, 'type': 'operator name', 'basis': 'the name the operator gave the record'},
                           {'name': wid, 'type': 'program identifier', 'basis': "this program's record id"}]
    # the Well and its one Origin
    comps['well'] = {'identifier': api['api10'] if api and api['status'] == 'VALID' else None, 'identifier_type': 'US Well Number (10 digits)',
                     'definition': COMPONENTS['well']}
    if api is None:
        gaps.append(f'{wid}: no US Well Number declared (set-well --api); the Well and its Origin carry no standard identifier')
    elif api['status'] != 'VALID':
        gaps.append(f"{wid}: the declared number {api['input']!r} is not a valid US Well Number: " + '; '.join(api['problems']))
    else:
        aliases.append({'name': api['api10'], 'type': 'US Well Number', 'basis': f"{api['state']['name'] or 'state ' + api['state']['code']}, county {api['county']['code']}, "
                                                                                  f"unique well {api['unique_well']['code']} ({api['unique_well']['range']})"})
    wo: Dict[str, Any] = {'definition': COMPONENTS['well_origin'], 'identifier': comps['well']['identifier']}
    if surf.get('lat') is not None and surf.get('lon') is not None:
        wo['position'] = {'lat': surf['lat'], 'lon': surf['lon'], 'datum': surf.get('datum') or 'unknown',
                          'basis': 'the surface position the operator declared (set-well --lat --lon --datum)'}
    else:
        wo['position'] = None
        gaps.append(f'{wid}: no surface position declared; the Well Origin has no position')
    comps['well_origin'] = wo
    # the Wellbore: only when the sidetrack code was given
    if api and api['status'] == 'VALID' and api.get('api12'):
        comps['wellbore'] = {'identifier': api['api12'], 'identifier_type': 'US Well Number (12 digits)', 'sidetrack': api['sidetrack'],
                             'definition': COMPONENTS['wellbore']}
        aliases.append({'name': api['api12'], 'type': 'US Well Number, wellbore', 'basis': api['sidetrack']['meaning']})
    else:
        comps['wellbore'] = {'identifier': None, 'definition': COMPONENTS['wellbore'],
                             'detail': 'not identified: the sidetrack code was not given; 00 is the original hole only when the regulator assigned it'}
        if api and api['status'] == 'VALID':
            gaps.append(f"{wid}: the Wellbore is not identified - the number has ten digits, the sidetrack code was not given")
    if api and api.get('api14'):
        comps['event'] = {'identifier': api['api14'], 'event': api['event']}
    # the downhole gauge: a measured depth along the wellbore, not a component of its own
    if well.get('station_md_ft') is not None:
        comps['gauge_station'] = {'measured_depth_ft': float(well['station_md_ft']), 'along': 'the wellbore',
                                  'basis': 'a downhole component of the Well at a measured depth; not a Wellbore Segment or a Contact Interval'}
    # the wellhead stream: what the channels measure at the wellhead
    chans = list(channels or [])
    named = {k: disp.get(k) for k in ('channel_pressure', 'channel_rate', 'channel_bhp') if disp.get(k)}
    if chans or named:
        comps['wellhead_stream'] = {'definition': COMPONENTS['wellhead_stream'], 'channels': chans, 'named': named,
                                    'direction': 'into the ground' if disp.get('role') == 'disposal' else 'not declared',
                                    'basis': 'the channels recorded at the wellhead; the stream is what enters or leaves the ground there'}
    else:
        comps['wellhead_stream'] = None
        gaps.append(f'{wid}: no channels held; no Wellhead Stream can be named')
    # what the permit and the completion report hold, and this program does not
    comps['wellbore_contact_interval'] = {'definition': COMPONENTS['wellbore_contact_interval'], 'identifier': None,
                                          'detail': 'the injection interval is a permit fact; not held here'}
    comps['wellbore_completion'] = {'definition': COMPONENTS['wellbore_completion'], 'identifier': None,
                                    'detail': 'the completion report\'s fact; not held here'}
    gaps.append(f'{wid}: the Contact Interval and the Completion are the permit\'s and the completion report\'s facts; not held here')
    # the UIC permit as an alias; the depth tier as a declaration
    if disp.get('uic_number'):
        aliases.append({'name': disp['uic_number'], 'type': 'UIC permit number', 'basis': 'the operator\'s declaration (set-well --uic)'})
    if disp.get('depth_tier'):
        comps['depth_tier'] = {'tier': disp['depth_tier'], 'basis': 'declared against the named formation base (set-well --depth-tier)'}
    # the Well Set: the site
    if site:
        comps['well_set'] = {'identifier': site.get('id'), 'name': site.get('display') or site.get('name') or site.get('id'),
                             'definition': COMPONENTS['well_set'], 'basis': 'the site the well belongs to in this workspace'}
    return {'protocol': 'ppdm.well_identity/1', 'well_id': wid, 'status': 'IDENTIFIED' if comps['well']['identifier'] else 'UNIDENTIFIED',
            'us_well_number': api, 'components': comps, 'aliases': aliases, 'gaps': gaps,
            'basis': 'PPDM "What is a Well" component definitions and the US Well Number standard; every component named from a declaration or a held record',
            'not_a_measurement': ['a sidetrack code that was not given', 'a contact interval or a completion: the permit\'s and the completion report\'s facts',
                                  'a county name: the standard\'s booklet carries it']}


def identity_lines(ident: dict) -> List[str]:
    """The identity as the lines a report prints."""
    c = ident['components']
    api = ident.get('us_well_number')
    lines = [f"Well {ident['well_id']}: " + (f"US Well Number {c['well']['identifier']}" if c['well']['identifier'] else 'no US Well Number declared')]
    if api and api.get('status') == 'VALID':
        lines.append(f"   state {api['state']['code']} ({api['state']['name']}), county {api['county']['code']}, unique well {api['unique_well']['code']} "
                     f"({api['unique_well']['range']}); identifies the {api['identifies'].replace('_', ' ')}")
    wo = c.get('well_origin') or {}
    if wo.get('position'):
        p = wo['position']
        lines.append(f"   Well Origin at {p['lat']}, {p['lon']} on {p['datum']}")
    wb = c.get('wellbore') or {}
    lines.append(f"   Wellbore {wb['identifier']} ({wb['sidetrack']['meaning']})" if wb.get('identifier') else '   Wellbore: not identified (no sidetrack code given)')
    if c.get('gauge_station'):
        lines.append(f"   gauge station at {c['gauge_station']['measured_depth_ft']:g} ft MD along the wellbore")
    ws = c.get('wellhead_stream')
    if ws:
        lines.append(f"   Wellhead Stream: {len(ws['channels'])} channel(s)" + (f", {', '.join(f'{k}={v}' for k, v in ws['named'].items())}" if ws['named'] else '')
                     + f"; direction {ws['direction']}")
    if c.get('well_set'):
        lines.append(f"   Well Set: {c['well_set']['name']} ({c['well_set']['identifier']})")
    lines.append('   aliases: ' + '; '.join(f"{a['name']} [{a['type']}]" for a in ident['aliases']))
    for g in ident['gaps']:
        lines.append(f'   gap: {g}')
    return lines


def report_text(ident: dict) -> str:
    return '\n'.join(identity_lines(ident))


# --------------------------------------------------------------------------------------------------------------
# the self-test
# --------------------------------------------------------------------------------------------------------------
def selftest() -> dict:
    """Numbers of every form and every fault, and a labelled well through the identity."""
    a10 = parse_well_number('42-329-39123')
    a12 = parse_well_number('423293912301')
    a14 = parse_well_number('42-329-39123-01-02')
    bad_state = parse_well_number('53-001-00001')
    unk_state = parse_well_number('99-001-00001')
    short = parse_well_number('42-329-391')
    chars = parse_well_number('42-329-3912A')
    zero = parse_well_number('42-329-00000')
    offshore = parse_well_number('60-123-40001')
    well = {'id': 'swd-1', 'display': 'Pad 3 SWD 1', 'kind': 'file', 'station_md_ft': 6800.0,
            'surface': {'lat': 31.95, 'lon': -102.25, 'datum': 'NAD27'},
            'disposal': {'role': 'disposal', 'api_number': '42-329-39123', 'uic_number': 'UIC-12345', 'depth_tier': 'deep',
                         'channel_pressure': 'WHP', 'channel_rate': 'RATE'}}
    ident = well_identity(well, {'id': 'Pad-3', 'display': 'Pad 3'}, channels=['WHP', 'RATE', 'TEMP'])
    well12 = {**well, 'disposal': {**well['disposal'], 'api_number': '42-329-39123-00'}}
    ident12 = well_identity(well12, {'id': 'Pad-3', 'display': 'Pad 3'}, channels=['WHP'])
    bare = well_identity({'id': 'w0', 'kind': 'file'}, None, channels=[])
    checks = {
        'api10': a10['status'] == 'VALID' and a10['identifies'] == 'well_origin' and a10['state']['name'] == 'Texas' and a10['api10'] == '42-329-39123'
                 and a10['api12'] is None and a10['unique_well']['range'] == 'current',
        'api12': a12['status'] == 'VALID' and a12['identifies'] == 'wellbore' and a12['sidetrack']['code'] == '01' and a12['api12'] == '42-329-39123-01'
                 and 'sidetrack 1' in a12['sidetrack']['meaning'],
        'api14': a14['status'] == 'VALID' and a14['identifies'] == 'event' and a14['event']['code'] == '02' and a14['api14'] == '42-329-39123-01-02',
        'reserved_state': bad_state['status'] == 'INVALID' and any('reserved' in p for p in bad_state['problems']),
        'unknown_state': unk_state['status'] == 'INVALID' and any('not a state' in p for p in unk_state['problems']),
        'wrong_length': short['status'] == 'INVALID' and any('digit' in p for p in short['problems']),
        'other_characters': chars['status'] == 'INVALID' and any('other characters' in p for p in chars['problems']),
        'zero_unique': zero['status'] == 'INVALID' and any('00000' in p for p in zero['problems']),
        'offshore': offshore['status'] == 'VALID' and offshore['state']['name'] == 'Northern Gulf of Mexico',
        'identity_10': ident['status'] == 'IDENTIFIED' and ident['components']['well']['identifier'] == '42-329-39123'
                       and ident['components']['wellbore']['identifier'] is None and any('sidetrack code was not given' in g for g in ident['gaps'])
                       and ident['components']['well_origin']['position']['datum'] == 'NAD27'
                       and ident['components']['wellhead_stream']['named'] == {'channel_pressure': 'WHP', 'channel_rate': 'RATE'}
                       and ident['components']['wellhead_stream']['direction'] == 'into the ground'
                       and ident['components']['gauge_station']['measured_depth_ft'] == 6800.0
                       and ident['components']['well_set']['identifier'] == 'Pad-3'
                       and {a['type'] for a in ident['aliases']} == {'operator name', 'program identifier', 'US Well Number', 'UIC permit number'}
                       and ident['components']['wellbore_contact_interval']['identifier'] is None,
        'identity_12': ident12['components']['wellbore']['identifier'] == '42-329-39123-00' and not any('sidetrack code was not given' in g for g in ident12['gaps'])
                       and any(a['type'] == 'US Well Number, wellbore' for a in ident12['aliases']),
        'bare_well': bare['status'] == 'UNIDENTIFIED' and any('no US Well Number' in g for g in bare['gaps']) and any('no surface position' in g for g in bare['gaps'])
                     and bare['components']['wellhead_stream'] is None,
        'lines': 'US Well Number 42-329-39123' in report_text(ident) and 'Wellbore: not identified' in report_text(ident) and 'Pad 3' in report_text(ident),
    }
    return {'label': 'SELF_TEST', 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks, 'identity': ident}
