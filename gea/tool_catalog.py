# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""tool_catalog - what this program knows how to measure with, and what it knows how to speak.

Two registers have lived in the code since the beginning and had no page: the tool library (the downhole and
surface tools, each with a mandatory source citation, its ratings and the telemetry interface it declares) and
the port registry (the formats and protocols that bring a tool's readings in). This module puts them side by
side as one record, and draws the line between them: a tool declares an interface, a port speaks one, and the
page says which tool each port can carry and which tool has no port in this build.

Every entry is a declaration with a citation; nothing here is a measurement. A tool whose vendor published no
numbers is PARAMETERS_USER_SUPPLIED and says so - its aging rate raises rather than inventing one. A port that
needs the site's own details (a register map, an endpoint, a point list) is DECLARED_SITE_DETAILS_REQUIRED and
names what it needs. The rating check is the one piece of arithmetic: a tool string hung in a well, each
station's tool against the temperature and pressure at that station, from the well's own profile when there is
one and from declared gradients when there is not.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .ports import PORT_REGISTRY
from .tool_library import (FULLY_SPECIFIED, PARAMETERS_USER_SUPPLIED, TOOL_LIBRARY, ToolString, drift_model_for,
                           rating_check)

# the live protocol ports: the module that speaks each, and what the site must supply before it runs
LIVE_PORTS: Dict[str, dict] = {
    'wits0': {'module': 'gea.wits0', 'transport': 'WITS Level 0 over TCP connect, TCP listen or serial',
              'needs': 'the sender (host and port, or the serial device) and the item map (four-digit code -> tag)',
              'rehearse': 'gea wits0-sim', 'speaks': 'the mud logger or EDR on the drill floor'},
    'witsml': {'module': 'gea.witsml', 'transport': 'WITSML 1.4.1.1 SOAP (read-only STORE interface)',
               'needs': "the store's url, the well/wellbore/log uids and the environment variable names holding the credentials",
               'rehearse': 'the in-package test store', 'speaks': 'a rig or operator WITSML 1.4 store'},
    'etp': {'module': 'gea.etp', 'transport': 'WITSML 2.x over Energistics ETP v1.2 (WebSocket, Avro)',
            'needs': 'the ETP endpoint and the channel URIs the store publishes',
            'rehearse': 'gea etp-sim', 'speaks': 'a newer rig or store that pushes rather than answers polls'},
    'opcua': {'module': 'gea.opcua_port', 'transport': 'OPC UA client (read and subscribe)',
              'needs': "the server endpoint, the security policy and the node ids", 'rehearse': 'a recording, replayed offline',
              'speaks': 'a pad PLC or SCADA server'},
    'mqtt': {'module': 'gea.mqtt_port', 'transport': 'MQTT 3.1.1 / 5.0 subscriber, including Sparkplug B',
             'needs': 'the broker, the topics and the tag map (topic/metric -> tag)', 'rehearse': 'a recording, replayed offline',
             'speaks': 'an edge gateway publishing to a broker'},
    'modbus_g6': {'module': 'gea.modbus', 'transport': 'Modbus TCP (read-only; pymodbus)',
                  'needs': "the host and the site's register map with its citation", 'rehearse': 'an in-process loopback server',
                  'speaks': 'a G6-class surface interface card on Ethernet'},
    'modbus_rtu': {'module': 'gea.modbus_rtu', 'transport': 'Modbus RTU on RS-485 (pyserial) or RTU over TCP',
                   'needs': "the serial device or converter, the unit id and the site's register map with its citation",
                   'rehearse': 'the stand-in slave (transport: sim)', 'speaks': 'the RS-485 drop most surface-readout panels are'},
    'dnp3': {'module': 'gea.dnp3', 'transport': 'DNP3 (IEEE 1815) master, read-only, over TCP',
             'needs': "the master and outstation addresses and the RTU's point list with its source",
             'rehearse': 'the stand-in outstation (transport: sim)', 'speaks': 'a pad RTU or pipeline SCADA'},
    'hartip': {'module': 'gea.hartip', 'transport': 'HART-IP (TCP 5094), read-only: commands 0, 3 and 9',
               'needs': "the gateway address and the variable map (pv, sv, tv, qv, loop_mA, dv:<code>) with the instrument's datasheet",
               'rehearse': 'the stand-in device (transport: sim)', 'speaks': 'the transmitter itself through a HART-IP gateway'},
    'piwebapi': {'module': 'gea.piwebapi', 'transport': 'AVEVA (OSIsoft) PI Web API over HTTPS, GET only',
                 'needs': "the base url, the tag list with its source and the environment variable names holding the credentials",
                 'rehearse': 'the stand-in server (base_url: sim)', 'speaks': "the operator's own PI Data Archive"},
    'seedlink': {'module': 'gea.seedlink', 'transport': 'SeedLink 3 / 4.0 (TCP 18000), read-only',
                 'needs': 'the server, the network and station codes and the channel selectors', 'rehearse': 'gea seedlink-sim',
                 'speaks': 'a seismic data centre or a digitiser at the pad'},
}
# which live port a tool's declared telemetry interface can be carried by, by the words the declaration uses
_INTERFACE_PORTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ('modbus rs485', ('modbus_rtu', 'modbus_g6')),
    ('modbus', ('modbus_g6', 'modbus_rtu')),
    ('4-20ma', ()),
    ('thermocouple input card', ()),
    ('fiber-optic interrogator', ()),
    ('vibrating-wire', ()),
    ('psk downhole telemetry', ('modbus_rtu', 'modbus_g6')),
)


def tools() -> List[dict]:
    """Every tool in the library as the page shows it: what it is, what it measures, what it is rated for, what it
    speaks, whether its numbers are published or the site's, its citation, and its published aging rate if it has one."""
    out = []
    for name, t in sorted(TOOL_LIBRARY.items()):
        row = {'name': name, 'tool_class': t.tool_class, 'measures': list(t.measures), 'telemetry_interface': t.telemetry_interface,
               'spec_status': t.spec_status, 'temp_rating_C': t.temp_rating_C, 'pressure_rating_psi': t.pressure_rating_psi,
               'source': t.source, 'notes': t.notes, 'params': dict(t.params), 'aging_rate_pct_fs_yr': None, 'aging_note': '',
               'ports': ports_for_interface(t.telemetry_interface)}
        if t.gauge_spec is not None:
            row['gauge_spec'] = {'full_scale_psi': t.gauge_spec.full_scale_psi, 'accuracy_pct_fs': t.gauge_spec.accuracy_pct_fs,
                                 'max_temp_C': t.gauge_spec.max_temp_C, 'source': t.gauge_spec.source}
        try:
            row['aging_rate_pct_fs_yr'] = round(drift_model_for(t)(25.0, 1000.0), 6)
            row['aging_note'] = "the datasheet's published drift rate"
        except ValueError as e:
            row['aging_note'] = str(e)
        out.append(row)
    return out


def ports_for_interface(interface: str) -> List[str]:
    """The live ports that can carry a declared telemetry interface, by the words the declaration uses."""
    s = (interface or '').lower()
    hits: List[str] = []
    for word, names in _INTERFACE_PORTS:
        if word in s:
            for n in names:
                if n not in hits:
                    hits.append(n)
    return hits


def ports() -> dict:
    """The two kinds of port: the file formats this program reads, and the live protocols it speaks."""
    files = []
    for name, spec in sorted(PORT_REGISTRY.items()):
        files.append({'name': name, 'transport': spec.transport, 'status': spec.status, 'detail': spec.detail,
                      'live': name in LIVE_PORTS})
    live = []
    for name, d in sorted(LIVE_PORTS.items()):
        live.append({'name': name, **d, 'in_registry': name in PORT_REGISTRY,
                     'registry_status': (PORT_REGISTRY[name].status if name in PORT_REGISTRY else '')})
    return {'files': files, 'live': live}


def unmatched() -> List[dict]:
    """The tools whose declared interface no live port in this build speaks - named, not hidden."""
    return [{'tool': t['name'], 'telemetry_interface': t['telemetry_interface']} for t in tools() if not t['ports']]


def catalog() -> dict:
    ts, ps = tools(), ports()
    return {'tools': ts, 'ports': ps, 'unmatched': unmatched(),
            'counts': {'tools': len(ts), 'fully_specified': sum(1 for t in ts if t['spec_status'] == FULLY_SPECIFIED),
                       'user_supplied': sum(1 for t in ts if t['spec_status'] == PARAMETERS_USER_SUPPLIED),
                       'file_ports': len(ps['files']), 'live_ports': len(ps['live'])},
            'statement': ('Every tool and every port here is a declaration with its source, not a measurement. A tool whose vendor '
                          'published no numbers carries none and says so; a port that needs the site\'s own details names them.')}


def check_string(stations: List[Tuple[float, str]], profile=None, deviation=None, surface_temp_F: float = 75.0,
                 temp_gradient_F_per_ft: float = 0.018, surface_pressure_psi: float = 14.7,
                 pressure_gradient_psi_per_ft: float = 0.465, name: str = 'toolstring') -> dict:
    """A tool string against a well: each station's tool at that station's conditions. Unknown tool names are
    named, not guessed; the conditions' basis is stated with the answer."""
    bad = [n for _, n in stations if n not in TOOL_LIBRARY]
    if bad:
        raise ValueError(f"not in the tool library: {', '.join(sorted(set(bad)))} (gea tools --list)")
    if not stations:
        raise ValueError('a tool string needs at least one station: (measured depth in feet, tool name)')
    ts = ToolString(stations=[(float(md), n) for md, n in stations], name=name)
    rows = rating_check(ts, profile=profile, deviation=deviation, surface_temp_F=surface_temp_F,
                        temp_gradient_F_per_ft=temp_gradient_F_per_ft, surface_pressure_psi=surface_pressure_psi,
                        pressure_gradient_psi_per_ft=pressure_gradient_psi_per_ft)
    over = [r for r in rows if not r['ok']]
    basis = ('the well profile' if profile is not None else
             f'declared gradients: {surface_temp_F:g} F + {temp_gradient_F_per_ft:g} F/ft, {surface_pressure_psi:g} psi + {pressure_gradient_psi_per_ft:g} psi/ft')
    return {'name': name, 'stations': rows, 'n_over_rating': len(over), 'basis': basis,
            'deviation': 'measured depth converted to true vertical depth by the survey' if deviation is not None else 'measured depth taken as true vertical depth (no survey given)',
            'lines': [f"{len(rows)} station(s) checked against {basis}; {len(over)} over a rating."] +
                     [f"{r['tool']} at {r['md_ft']:,.0f} ft: {r['station_temp_C']:.1f} C, {r['station_pressure_psi']:,.0f} psi"
                      + (f" - OVER its {r['temp_rating_C']:g} C rating" if r['over_temp_rating'] else '')
                      + (f" - OVER its {r['pressure_rating_psi']:,.0f} psi rating" if r['over_pressure_rating'] else '')
                      + ('' if not r['ok'] else ' - within its ratings') for r in rows] +
                     ['A rating is the vendor\'s, and the conditions are the well\'s or the declaration\'s; this check is arithmetic on both, not a prediction of failure.']}


def text() -> str:
    c = catalog()
    out = [f"tools: {c['counts']['tools']} ({c['counts']['fully_specified']} with published numbers, {c['counts']['user_supplied']} parameters user-supplied)"]
    for t in c['tools']:
        out.append(f"  {t['name']:<30s} {t['tool_class']:<24s} {t['spec_status']}")
        out.append(f"      measures {', '.join(t['measures']) or '(none - it is an interface)'}; speaks {t['telemetry_interface']}")
        if t['temp_rating_C'] or t['pressure_rating_psi']:
            out.append(f"      rated {t['temp_rating_C'] or '?'} C, {t['pressure_rating_psi'] or '?'} psi")
        out.append(f"      aging: {('%.4f %%FS/yr - ' % t['aging_rate_pct_fs_yr']) + t['aging_note'] if t['aging_rate_pct_fs_yr'] is not None else t['aging_note']}")
        out.append(f"      ports: {', '.join(t['ports']) or 'none in this build'}")
        out.append(f"      source: {t['source'][:160]}")
    out.append('')
    out.append(f"file ports: {c['counts']['file_ports']}")
    for p in c['ports']['files']:
        out.append(f"  {p['name']:<16s} {p['status']:<32s} {p['transport']}")
    out.append('')
    out.append(f"live ports: {c['counts']['live_ports']}")
    for p in c['ports']['live']:
        out.append(f"  {p['name']:<12s} {p['transport']}")
        out.append(f"      speaks to {p['speaks']}; needs {p['needs']}; rehearse with {p['rehearse']}")
    if c['unmatched']:
        out.append('')
        out.append('tools whose declared interface no live port in this build speaks:')
        for u in c['unmatched']:
            out.append(f"  {u['tool']}: {u['telemetry_interface']}")
    out.append('')
    out.append(c['statement'])
    return '\n'.join(out)
