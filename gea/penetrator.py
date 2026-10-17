"""The wellhead penetrator of an ESP well: its installation record, and the drive, motor and site against its ratings.

The penetrator carries the pump's power conductors through the tubing hanger and the wellhead while holding the
well's pressure. Its maker rates it - voltage, current, frequency, phases, pressure, hazardous area, ambient
temperature, altitude - and the installation is a job with its own measurements. This module keeps both:

- the ratings, from the maker's documents (`reference/penetrator_ratings.json`: each figure with the document,
  its revision and the section it is printed in) or from the operator's own fleet entry with its datasheet;
- the installation record per well, as the operator declares it: the penetrator, the date, the installer and
  whether the maker certified them, the motor (permanent magnet or induction), the gauge distance measured on the
  job, the insulation readings of the three conductors and the test voltage, the void pressure test (pressure,
  minutes, held);
- the ESP drive and motor declared for the well (the drive's maximum output voltage and current and its frequency
  limits, the motor's nameplate) and the site (the wellhead's hazardous-area classification, the ambient range);
- the check: each declared figure against the matching rating, one line each - WITHIN, OUTSIDE, NOT RATED (the
  maker's document prints no figure), NOT DECLARED (the site has not given one), NOT STATED (the document does not
  say). The well's elevation, when declared, is the altitude.

Nothing here is measured from a stream: these are declarations and the arithmetic between them. An acceptance
figure for the insulation readings is the site's own (with its source); the program does not supply one.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

REF = os.path.join(os.path.dirname(__file__), 'reference', 'penetrator_ratings.json')
MOTOR_TYPES = ('PMM', 'induction')
PHASES = ('A', 'B', 'C')
AREAS = ('unclassified', 'Class I, Division 1', 'Class I, Division 2', 'Class I, Zone 0', 'Class I, Zone 1', 'Class I, Zone 2')
DIVISION_GROUPS = ('A', 'B', 'C', 'D')                  # A is the most demanding; D (propane-like) the least
ZONE_GROUPS = ('IIC', 'IIB', 'IIA')                     # IIC the most demanding
TEMP_CLASSES = ('T1', 'T2', 'T3', 'T4', 'T5', 'T6')    # T6 the lowest surface temperature
UNITS = {'gauge distance': 'in', 'insulation': 'megohm', 'void test': 'psi', 'drive output voltage': 'V', 'drive output current': 'A',
         'motor nameplate voltage': 'V', 'motor nameplate current': 'A', 'drive frequency': 'Hz', 'ambient temperature': 'F', 'altitude': 'm'}
_LOCK = threading.Lock()
_CAT = None


def _utc() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# -- the ratings ---------------------------------------------------------------------------------------
def catalogue() -> dict:
    global _CAT
    if _CAT is None:
        with open(REF, encoding='utf-8') as f:
            _CAT = json.load(f)
    return _CAT


def penetrators() -> List[dict]:
    return catalogue()['penetrators']


def pmm_note() -> dict:
    return catalogue()['pmm_safety']


def resolve(ws, pid: str) -> dict:
    """A penetrator by its catalogue id, or a fleet tool of the penetrator class by its fleet id (its ratings are its
    datasheet's)."""
    pid = (pid or '').strip()
    for p in penetrators():
        if p['id'] == pid:
            return dict(p, origin='catalogue')
    if ws is not None:
        from .tool_taxonomy import fleet_list
        for t in fleet_list(ws.path):
            if t['id'] == pid:
                if t['class_id'] != 'wellhead_penetrator':
                    raise ValueError(f'{pid} is a {t["class_name"]}, not a wellhead penetrator')
                r = dict(t.get('ratings') or {})
                for k in ('divisions', 'zones'):
                    if isinstance(r.get(k), str):
                        r[k] = [x.strip() for x in r[k].split('/') if x.strip()]
                if isinstance(r.get('freq_hz'), str) and '-' in r['freq_hz']:
                    lo, hi = r['freq_hz'].split('-', 1)
                    r['freq_hz'] = [float(lo), float(hi)]
                return {'id': t['id'], 'maker': t['maker'], 'model': t['model'], 'document': t['datasheet'], 'section': 'datasheet',
                        'intended_use': t.get('note') or '', 'pmm': r.pop('pmm', None), 'certified_installer_required': r.pop('certified_installer_required', None),
                        'ratings': r, 'origin': 'fleet'}
    raise KeyError(f'no penetrator {pid!r}: one of {", ".join(p["id"] for p in penetrators())}, or a fleet tool of the wellhead_penetrator class')


# -- the declarations ----------------------------------------------------------------------------------
def _path(ws) -> str:
    return os.path.join(ws.path, 'completion', 'esp.json')


def records(ws) -> Dict[str, dict]:
    p = _path(ws)
    if not os.path.isfile(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f).get('wells', {})


def _save(ws, wells: Dict[str, dict]) -> None:
    p = _path(ws)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump({'schema': 1, 'wells': wells}, f, indent=1)
    os.replace(p + '.tmp', p)


def _num(v, what: str, positive: bool = True) -> Optional[float]:
    if v in (None, ''):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f'{what} is not a number: {v!r}')
    if x != x or x in (float('inf'), float('-inf')) or (positive and x <= 0):
        raise ValueError(f'{what} must be a positive number, not {v!r}')
    return x


def _yes(v, what: str) -> Optional[bool]:
    if v in (None, ''):
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ('y', 'yes', 'true', '1'):
        return True
    if s in ('n', 'no', 'false', '0'):
        return False
    raise ValueError(f'{what} is yes or no, not {v!r}')


def _date(v, what: str) -> Optional[str]:
    if v in (None, ''):
        return None
    try:
        return datetime.fromisoformat(str(v).strip().replace('Z', '')).date().isoformat()
    except ValueError:
        raise ValueError(f'{what} is not a date (YYYY-MM-DD): {v!r}')


def _area_key(a: str) -> str:
    k = ' ' + a.lower().replace(',', ' ') + ' '
    for x, y in ((' cl ', ' class '), (' div ', ' division '), (' class 1 ', ' class i '), (' div1 ', ' division 1 '), (' div2 ', ' division 2 ')):
        k = k.replace(x, y)
    return re.sub(r'[^a-z0-9]', '', k)


def _well(ws, well_id: str) -> None:
    if not any(w['id'] == well_id for w in ws.wells()):
        raise KeyError(f'no well {well_id!r}')


def install_record(ws, well_id: str, penetrator_id: str, installed=None, installer: str = '', certified=None, certificate: str = '',
                   motor_type: str = '', gauge_distance_in=None, insulation: Optional[dict] = None, ir_test_volts=None,
                   ir_min_megohm=None, ir_min_source: str = '', void_test_psi=None, void_test_minutes=None, void_held=None,
                   note: str = '', actor: str = 'system', pen: Optional[dict] = None) -> dict:
    """Validate an installation record without writing it (the declarations import plans with this; it passes `pen`
    for a penetrator its own sheet adds to the fleet)."""
    _well(ws, well_id)
    pen = pen or resolve(ws, penetrator_id)
    mt = (motor_type or '').strip()
    if mt and mt.lower() not in [m.lower() for m in MOTOR_TYPES]:
        raise ValueError(f'the motor is one of {", ".join(MOTOR_TYPES)}, not {motor_type!r}')
    mt = next((m for m in MOTOR_TYPES if m.lower() == mt.lower()), None)
    ins = {}
    for ph, v in (insulation or {}).items():
        ph_ = str(ph).strip().upper()
        if ph_ not in PHASES:
            raise ValueError(f'insulation readings are per conductor A, B and C, not {ph!r}')
        x = _num(v, f'the insulation reading of conductor {ph_} (megohm)', positive=False)
        if x is not None:
            if x < 0:
                raise ValueError(f'the insulation reading of conductor {ph_} cannot be negative')
            ins[ph_] = x
    irmin = _num(ir_min_megohm, 'the insulation acceptance minimum (megohm)')
    if irmin is not None and not (ir_min_source or '').strip():
        raise ValueError('an insulation acceptance minimum needs its source (the operator\'s standard, the ESP vendor\'s procedure)')
    vh = _yes(void_held, 'whether the void test held')
    rec = {'well_id': well_id, 'penetrator': pen['id'], 'penetrator_origin': pen['origin'], 'installed': _date(installed, 'the installation date'),
           'installer': (installer or '').strip(), 'installer_certified': _yes(certified, 'whether the installer is certified by the maker'),
           'certificate': (certificate or '').strip(), 'motor_type': mt,
           'gauge_distance_in': _num(gauge_distance_in, 'the gauge distance (in)'),
           'insulation_megohm': ins, 'ir_test_volts': _num(ir_test_volts, 'the insulation test voltage'),
           'ir_min_megohm': irmin, 'ir_min_source': (ir_min_source or '').strip() if irmin is not None else '',
           'void_test_psi': _num(void_test_psi, 'the void test pressure (psi)'), 'void_test_minutes': _num(void_test_minutes, 'the void test duration (minutes)'),
           'void_held': vh, 'note': (note or '').strip(), 'declared_by': actor, 'declared_utc': _utc()}
    return rec


def drive_record(ws, well_id: str, maker: str = '', model: str = '', max_volts=None, max_amps=None, freq_min_hz=None, freq_max_hz=None,
                 phases=None, motor_volts=None, motor_amps=None, area: str = '', gas_group: str = '', temp_class: str = '',
                 ambient_min_f=None, ambient_max_f=None, source: str = '', actor: str = 'system') -> dict:
    """Validate the well's ESP drive, motor nameplate and site conditions without writing them."""
    _well(ws, well_id)
    if not (source or '').strip():
        raise ValueError('the drive and motor figures need their source (the drive\'s datasheet, the motor nameplate, the site\'s area classification drawing)')
    ar = (area or '').strip()
    if ar:
        hit = next((a for a in AREAS if _area_key(a) == _area_key(ar)), None)
        if not hit:
            raise ValueError(f'the area classification is one of: {"; ".join(AREAS)} - not {area!r}')
        ar = hit
    gg = (gas_group or '').strip().upper()
    if gg and gg not in DIVISION_GROUPS + ZONE_GROUPS:
        raise ValueError(f'the gas group is one of {", ".join(DIVISION_GROUPS + ZONE_GROUPS)}, not {gas_group!r}')
    tc = (temp_class or '').strip().upper()
    if tc and tc not in TEMP_CLASSES:
        raise ValueError(f'the temperature class is one of {", ".join(TEMP_CLASSES)}, not {temp_class!r}')
    fmin, fmax = _num(freq_min_hz, 'the drive\'s minimum frequency (Hz)'), _num(freq_max_hz, 'the drive\'s maximum frequency (Hz)')
    if fmin is not None and fmax is not None and fmax <= fmin:
        raise ValueError('the drive\'s maximum frequency must be above its minimum')
    amin, amax = _num(ambient_min_f, 'the ambient minimum (F)', positive=False), _num(ambient_max_f, 'the ambient maximum (F)', positive=False)
    if amin is not None and amax is not None and amax <= amin:
        raise ValueError('the ambient maximum must be above the minimum')
    ph = _num(phases, 'the number of phases')
    return {'well_id': well_id, 'maker': (maker or '').strip(), 'model': (model or '').strip(), 'max_volts': _num(max_volts, 'the drive\'s maximum output voltage'),
            'max_amps': _num(max_amps, 'the drive\'s maximum output current'), 'freq_min_hz': fmin, 'freq_max_hz': fmax,
            'phases': int(ph) if ph is not None else None, 'motor_volts': _num(motor_volts, 'the motor nameplate voltage'),
            'motor_amps': _num(motor_amps, 'the motor nameplate current'), 'area': ar, 'gas_group': gg, 'temp_class': tc,
            'ambient_min_f': amin, 'ambient_max_f': amax, 'source': source.strip(), 'declared_by': actor, 'declared_utc': _utc()}


def _put(ws, well_id: str, key: str, rec: dict, actor: str) -> dict:
    with _LOCK:
        w = records(ws)
        cur = w.setdefault(well_id, {})
        old = cur.get(key)
        cur[key] = rec
        _save(ws, w)
    ws.audit(actor, f'penetrator.{key}.' + ('replace' if old else 'declare'), {'well': well_id, **{k: rec.get(k) for k in ('penetrator', 'model', 'installed')} })
    return rec


def save_install(ws, actor: str, well_id: str, penetrator_id: str, **kw) -> dict:
    return _put(ws, well_id, 'install', install_record(ws, well_id, penetrator_id, actor=actor, **kw), actor)


def save_drive(ws, actor: str, well_id: str, **kw) -> dict:
    return _put(ws, well_id, 'drive', drive_record(ws, well_id, actor=actor, **kw), actor)


def remove(ws, actor: str, well_id: str, what: str) -> dict:
    if what not in ('install', 'drive'):
        raise ValueError('remove the install record or the drive')
    with _LOCK:
        w = records(ws)
        if not (w.get(well_id) or {}).get(what):
            raise KeyError(f'no {what} record for {well_id}')
        gone = w[well_id].pop(what)
        if not w[well_id]:
            w.pop(well_id)
        _save(ws, w)
    ws.audit(actor, f'penetrator.{what}.remove', {'well': well_id})
    return {'removed': gone}


# -- the check -----------------------------------------------------------------------------------------
def _row(item: str, declared, rated, status: str, why: str = '') -> dict:
    return {'item': item, 'declared': declared, 'rated': rated, 'status': status, 'why': why}


def _le(item: str, v, lim, unit: str, what_lim: str) -> dict:
    if lim is None:
        return _row(item, v, None, 'NOT RATED', f'the document prints no {what_lim}')
    if v is None:
        return _row(item, None, lim, 'NOT DECLARED', f'declare it to check against the {what_lim} ({lim:g} {unit})')
    return _row(item, v, lim, 'WITHIN' if v <= lim else 'OUTSIDE',
                f'{v:g} {unit} against the {what_lim} of {lim:g} {unit}' + ('' if v <= lim else f': {v - lim:g} {unit} over'))


def _area_row(area: str, r: dict) -> dict:
    divs, zones = r.get('divisions') or [], r.get('zones') or []
    rated = '; '.join(divs + zones) or None
    if not area:
        return _row('hazardous area', None, rated, 'NOT DECLARED', "declare the wellhead's area classification (the site's classification drawing)")
    if area == 'unclassified':
        return _row('hazardous area', area, rated, 'WITHIN', 'an unclassified location needs no hazardous-area rating')
    if not rated:
        return _row('hazardous area', area, None, 'NOT RATED', 'the document prints no hazardous-area rating')
    ok = {'Class I, Division 1': ('Class I, Division 1',), 'Class I, Division 2': ('Class I, Division 1', 'Class I, Division 2'),
          'Class I, Zone 0': ('Class I, Zone 0',), 'Class I, Zone 1': ('Class I, Zone 0', 'Class I, Zone 1'),
          'Class I, Zone 2': ('Class I, Zone 0', 'Class I, Zone 1', 'Class I, Zone 2')}[area]
    good = any(x in ok for x in divs + zones)
    return _row('hazardous area', area, rated, 'WITHIN' if good else 'OUTSIDE',
                'rated for it' if good else f'not rated for {area}')


def _group_row(gg: str, r: dict) -> Optional[dict]:
    if not gg:
        return None
    if gg in DIVISION_GROUPS:
        lim = r.get('division_group')
        if not lim:
            return _row('gas group', gg, None, 'NOT RATED', 'the document prints no division gas group')
        good = DIVISION_GROUPS.index(gg) >= DIVISION_GROUPS.index(lim)
    else:
        lim = r.get('zone_group')
        if not lim:
            return _row('gas group', gg, None, 'NOT RATED', 'the document prints no zone gas group')
        good = ZONE_GROUPS.index(gg) >= ZONE_GROUPS.index(lim)
    return _row('gas group', gg, lim, 'WITHIN' if good else 'OUTSIDE', f'rated group {lim}' + ('' if good else f': group {gg} is more demanding'))


def _temp_row(tc: str, r: dict) -> Optional[dict]:
    if not tc:
        return None
    lim = r.get('temp_class')
    if not lim:
        return _row('temperature class', tc, None, 'NOT RATED', 'the document prints no temperature class')
    good = TEMP_CLASSES.index(lim) >= TEMP_CLASSES.index(tc)
    return _row('temperature class', tc, lim, 'WITHIN' if good else 'OUTSIDE', f'rated {lim}' + ('' if good else f': the site requires {tc}'))


def _altitude_m(ws, well_id: str):
    try:
        w = ws.well(well_id)
    except Exception:
        return None, ''
    el = (w.get('surface') or {}).get('elevation') or {}
    if el.get('ft') is None:
        return None, ''
    return round(float(el['ft']) * 0.3048, 1), f"the well's declared {el.get('datum')} elevation, {float(el['ft']):g} ft ({el.get('source')})"


def check(ws, well_id: str) -> dict:
    """The well's installation record and its drive, motor and site against the penetrator's ratings, a line each."""
    _well(ws, well_id)
    rec = records(ws).get(well_id) or {}
    ins, drv = rec.get('install'), rec.get('drive')
    out = {'well_id': well_id, 'install': ins, 'drive': drv, 'penetrator': None, 'rows': [], 'status': 'NOT DECLARED', 'notes': []}
    if not ins:
        out['notes'].append('no installation record: declare the penetrator installed in this well')
        return out
    try:
        pen = resolve(ws, ins['penetrator'])
    except (KeyError, ValueError) as e:
        out['notes'].append(str(e.args[0] if e.args else e))
        return out
    out['penetrator'] = pen
    r = pen.get('ratings') or {}
    rows = out['rows']
    # the job
    need_cert = pen.get('certified_installer_required')
    c = ins.get('installer_certified')
    if need_cert:
        rows.append(_row('certified installer', ins.get('installer') or None, 'required by the maker',
                         'NOT DECLARED' if c is None else ('WITHIN' if c else 'OUTSIDE'),
                         {None: 'declare whether the installer holds the maker\'s certification', True: 'certified' + (f" ({ins['certificate']})" if ins.get('certificate') else ''),
                          False: "the maker's document requires its certified installer"}[c]))
    else:
        rows.append(_row('certified installer', ins.get('installer') or None, None, 'NOT STATED' if c is None else 'RECORDED',
                         'the document does not state a certification requirement'))
    mt = ins.get('motor_type')
    if mt == 'PMM':
        rows.append(_row('motor', 'PMM', 'for PMM' if pen.get('pmm') else None, 'WITHIN' if pen.get('pmm') else 'NOT STATED',
                         'the document is for installation with a permanent magnet motor' if pen.get('pmm')
                         else 'the document does not state use with a permanent magnet motor: confirm with the maker'))
    elif mt:
        rows.append(_row('motor', mt, None, 'RECORDED', ''))
    else:
        rows.append(_row('motor', None, None, 'NOT DECLARED', 'declare the motor: PMM or induction'))
    gd = ins.get('gauge_distance_in')
    rows.append(_row('gauge distance', gd, None, 'RECORDED' if gd is not None else 'NOT DECLARED',
                     'measured on the job for this hanger and adaptor' if gd is not None else 'record the gauge distance measured on the job'))
    irs = ins.get('insulation_megohm') or {}
    if len(irs) < 3:
        rows.append(_row('insulation', irs or None, None, 'NOT DECLARED', 'record the reading of each of the three conductors (A, B, C) and the test voltage'))
    else:
        lo = min(irs.values())
        if ins.get('ir_min_megohm') is not None:
            m = ins['ir_min_megohm']
            rows.append(_row('insulation', irs, m, 'WITHIN' if lo >= m else 'OUTSIDE',
                             f'lowest {lo:g} megohm against the minimum of {m:g} megohm ({ins["ir_min_source"]})'))
        else:
            rows.append(_row('insulation', irs, None, 'RECORDED', f'lowest {lo:g} megohm' + (f' at {ins["ir_test_volts"]:g} V' if ins.get('ir_test_volts') else '')
                             + '; no acceptance minimum declared'))
    vp, vh = ins.get('void_test_psi'), ins.get('void_held')
    if vp is None or vh is None:
        rows.append(_row('void test', vp, r.get('working_psi_api'), 'NOT DECLARED', 'record the void test pressure, its minutes and whether it held'))
    elif not vh:
        rows.append(_row('void test', vp, r.get('working_psi_api'), 'OUTSIDE', 'the void did not hold its test'))
    elif r.get('working_psi_api') is not None and vp > r['working_psi_api']:
        rows.append(_row('void test', vp, r['working_psi_api'], 'OUTSIDE', f'{vp:g} psi is above the rated working pressure of {r["working_psi_api"]:g} psi'))
    else:
        rows.append(_row('void test', vp, r.get('working_psi_api'), 'WITHIN', f'held at {vp:g} psi' + (f' for {ins["void_test_minutes"]:g} min' if ins.get('void_test_minutes') else '')
                         + (f', at or below the rated working pressure of {r["working_psi_api"]:g} psi' if r.get('working_psi_api') is not None else '')))
    # the drive, the motor, the site
    if not drv:
        out['notes'].append("no drive declared: declare the ESP drive's output limits, the motor nameplate and the site's area classification")
    d = drv or {}
    rows.append(_le('drive output voltage', d.get('max_volts'), r.get('max_volts'), 'V', 'rated maximum voltage'))
    row_ = _le('drive output current', d.get('max_amps'), r.get('max_amps_fla'), 'A', 'rated full-load current')
    if row_['status'] == 'OUTSIDE':
        row_['why'] += f": the drive can deliver more than the penetrator carries - set its current limit at or below {r['max_amps_fla']:g} A"
    rows.append(row_)
    rows.append(_le('motor nameplate voltage', d.get('motor_volts'), r.get('max_volts'), 'V', 'rated maximum voltage'))
    rows.append(_le('motor nameplate current', d.get('motor_amps'), r.get('max_amps_fla'), 'A', 'rated full-load current'))
    fr = r.get('freq_hz')
    if not fr:
        rows.append(_row('drive frequency', [d.get('freq_min_hz'), d.get('freq_max_hz')], None, 'NOT RATED', 'the document prints no frequency range'))
    elif d.get('freq_min_hz') is None or d.get('freq_max_hz') is None:
        rows.append(_row('drive frequency', None, fr, 'NOT DECLARED', f"declare the drive's frequency limits to check against {fr[0]:g}-{fr[1]:g} Hz"))
    else:
        good = d['freq_min_hz'] >= fr[0] and d['freq_max_hz'] <= fr[1]
        rows.append(_row('drive frequency', [d['freq_min_hz'], d['freq_max_hz']], fr, 'WITHIN' if good else 'OUTSIDE',
                         f"{d['freq_min_hz']:g}-{d['freq_max_hz']:g} Hz against the rated {fr[0]:g}-{fr[1]:g} Hz"
                         + ('' if good else ": set the drive's limits inside the rated range")))
    if r.get('phases') is None:
        rows.append(_row('phases', d.get('phases'), None, 'NOT RATED', 'the document prints no phase count'))
    elif d.get('phases') is None:
        rows.append(_row('phases', None, r['phases'], 'NOT DECLARED', ''))
    else:
        rows.append(_row('phases', d['phases'], r['phases'], 'WITHIN' if d['phases'] == r['phases'] else 'OUTSIDE', ''))
    rows.append(_area_row(d.get('area') or '', r))
    for x in (_group_row(d.get('gas_group') or '', r), _temp_row(d.get('temp_class') or '', r)):
        if x:
            rows.append(x)
    amb = r.get('ambient_f')
    if not amb:
        rows.append(_row('ambient temperature', [d.get('ambient_min_f'), d.get('ambient_max_f')], None, 'NOT RATED', 'the document prints no ambient range'))
    elif d.get('ambient_min_f') is None or d.get('ambient_max_f') is None:
        rows.append(_row('ambient temperature', None, amb, 'NOT DECLARED', f"declare the site's ambient range to check against {amb[0]:g} to {amb[1]:g} F"))
    else:
        good = d['ambient_min_f'] >= amb[0] and d['ambient_max_f'] <= amb[1]
        rows.append(_row('ambient temperature', [d['ambient_min_f'], d['ambient_max_f']], amb, 'WITHIN' if good else 'OUTSIDE',
                         f"{d['ambient_min_f']:g} to {d['ambient_max_f']:g} F against the rated {amb[0]:g} to {amb[1]:g} F"))
    alt, why = _altitude_m(ws, well_id)
    if r.get('altitude_max_m') is None:
        rows.append(_row('altitude', alt, None, 'NOT RATED', 'the document prints no altitude limit'))
    elif alt is None:
        rows.append(_row('altitude', None, r['altitude_max_m'], 'NOT DECLARED', "declare the well's reference elevation"))
    else:
        rows.append(_row('altitude', alt, r['altitude_max_m'], 'WITHIN' if alt <= r['altitude_max_m'] else 'OUTSIDE',
                         f'{alt:g} m from {why}, against the rated {r["altitude_max_m"]:g} m'))
    for x in rows:
        x['unit'] = UNITS.get(x['item'], '')
    st = [x['status'] for x in rows]
    out['status'] = 'OUTSIDE' if 'OUTSIDE' in st else ('WITHIN' if all(x in ('WITHIN', 'RECORDED') for x in st) else 'INCOMPLETE')
    if mt == 'PMM':
        out['notes'].append('permanent magnet motor: treat the cable, the penetrator and the junction box as live (see the PMM safety note)')
    return out


def page(ws) -> dict:
    """The Completion tab: the penetrators the program knows, the PMM safety note, and every well's check."""
    recs = records(ws) if ws is not None else {}
    wells = ws.wells() if ws is not None else []
    out = []
    for w in wells:
        if w['id'] in recs:
            c = check(ws, w['id'])
        else:
            c = {'well_id': w['id'], 'install': None, 'drive': None, 'penetrator': None, 'rows': [], 'status': 'NOT DECLARED',
                 'notes': ['no installation record: an ESP well declares its penetrator here']}
        c['display'] = w.get('display') or w['id']
        out.append(c)
    fleet = []
    if ws is not None:
        from .tool_taxonomy import fleet_list
        fleet = [t for t in fleet_list(ws.path) if t['class_id'] == 'wellhead_penetrator']
    return {'penetrators': penetrators(), 'fleet': fleet, 'how': catalogue()['how'], 'pmm_safety': pmm_note(), 'wells': out,
            'areas': list(AREAS), 'motor_types': list(MOTOR_TYPES)}


def _fmt(v) -> str:
    if v is None:
        return '-'
    if isinstance(v, dict):
        return ', '.join(f'{k} {x:g}' for k, x in v.items())
    if isinstance(v, list):
        return '-'.join('?' if x is None else f'{x:g}' for x in v)
    if isinstance(v, float):
        return f'{v:g}'
    return str(v)


def text_check(c: dict) -> str:
    L = [f"{c.get('display') or c['well_id']}: {c['status']}"]
    if c.get('penetrator'):
        p = c['penetrator']
        L.append(f"  penetrator: {p['maker']} {p['model']} - {p['document']}, {p['section']}")
    for r in c['rows']:
        L.append(f"  {r['status']:<12} {r['item']:<24} {_fmt(r['declared']):<22} rated {_fmt(r['rated']):<18} {r['why']}")
    for n in c.get('notes') or []:
        L.append('  note: ' + n)
    return '\n'.join(L)


def text(p: dict) -> str:
    L = ['Wellhead penetrators the program knows (ratings as the maker\'s document prints them):']
    for x in p['penetrators']:
        r = x['ratings']
        bits = []
        if r.get('max_volts'):
            bits.append(f"{r['max_volts']:g} V max")
        if r.get('max_amps_fla'):
            bits.append(f"{r['max_amps_fla']:g} A FLA max")
        if r.get('freq_hz'):
            bits.append(f"{r['freq_hz'][0]:g}-{r['freq_hz'][1]:g} Hz")
        if r.get('working_psi_api'):
            bits.append(f"{r['working_psi_api']:g} psi API, {r.get('proof_test_psi', 0):g} psi proof")
        if r.get('divisions') or r.get('zones'):
            bits.append('; '.join((r.get('divisions') or []) + (r.get('zones') or [])))
        L.append(f"  {x['id']:<14} {x['model']}")
        L.append(f"  {'':<14} {', '.join(bits) or 'no ratings printed: get the datasheet'} [{x['document']}, {x['section']}]")
    for t in p.get('fleet') or []:
        L.append(f"  {t['id']:<14} fleet: {t['maker']} {t['model']} [{t['datasheet']}]")
    L.append('')
    L.append('Permanent magnet motors:')
    for s in p['pmm_safety']['text']:
        L.append('  ' + s)
    L.append('')
    for c in p['wells']:
        L.append(text_check(c))
    return '\n'.join(L)
