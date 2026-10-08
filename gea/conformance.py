# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""conformance - the certificate register and the accuracy statement in the standards' own words.

Until now the drift report printed a certificate's accuracy beside the measured bias and said "inside" or
"OUTSIDE". That is a statement of conformity, and ISO/IEC 17025:2017 says what one has to carry: the results
it applies to, the specification it is judged against, and the decision rule that was applied (7.8.6.2) -
and the rule has to take the measurement uncertainty into account (7.8.6.1). "Inside" with no uncertainty
and no rule is the simple-acceptance rule applied silently. This module names the rule and applies it in the
open.

Two things, to their published definitions:

- the decision rules of ILAC-G8:09/2019. A tolerance limit TL (the specification), a measured value y with
  its expanded uncertainty U (coverage factor k, coverage probability), and a guard band w. Simple
  acceptance: w = 0, the acceptance limit is the tolerance limit, PASS or FAIL, the uncertainty not taken
  into account - which the standard allows only when the customer agreed to it. Binary with a guard band:
  w = U, PASS below TL - U, FAIL above it. Non-binary with a guard band, the rule this program applies
  unless told otherwise: PASS below TL - U; CONDITIONAL PASS from TL - U to TL; CONDITIONAL FAIL from TL
  to TL + U; FAIL above TL + U - with the probability of a false accept at the acceptance limit at most
  2.5 % when U is the 95 % expanded uncertainty;
- the content a calibration certificate has to carry, from ISO/IEC 17025:2017 7.8.2.1 (the general items:
  a unique identification, the laboratory, the item and its identification, the dates, the results with
  their units, who authorised it) and 7.8.4.1 (a calibration certificate in addition: the measurement
  uncertainty, the conditions the calibration was made under, a statement of how the measurements are
  metrologically traceable, the results before and after any adjustment, and - where a statement of
  conformity is made - the decision rule). The register holds what the operator recorded from the paper;
  each item is CARRIED, NOT CARRIED (the register has the field and it is empty), or NOT RECORDED (the
  register has no such field). A certificate's PDF is not read by this program: a clause is carried when
  a person filed it.

What this module will not call a measurement:

- a conformity decision without an uncertainty: without U the only rule is simple acceptance, and the
  statement says so and says that the uncertainty was not taken into account;
- a coverage probability the certificate did not state: k is recorded as filed, and a k that was not filed
  is a gap, not 2;
- the content of a certificate the operator did not transcribe: NOT CARRIED is the register's answer, not
  the paper's.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

RULES = ('simple-acceptance', 'binary-guard-band', 'non-binary-guard-band')
DEFAULT_RULE = 'non-binary-guard-band'
RULE_TEXT = {
    'simple-acceptance': ('ILAC-G8:09/2019 simple acceptance: the acceptance limit is the tolerance limit (w = 0); PASS when the measured value '
                          'is inside the tolerance, FAIL when it is outside; the measurement uncertainty is not taken into account'),
    'binary-guard-band': ('ILAC-G8:09/2019 binary with a guard band: w = U, the acceptance limit is TL - U; PASS when the measured value is below '
                          'the acceptance limit, FAIL otherwise'),
    'non-binary-guard-band': ('ILAC-G8:09/2019 non-binary with a guard band: w = U; PASS below TL - U; CONDITIONAL PASS from TL - U to TL; '
                              'CONDITIONAL FAIL from TL to TL + U; FAIL above TL + U; the probability of a false accept at the acceptance '
                              'limit is at most 2.5 % when U is the 95 % expanded uncertainty'),
}

# ISO/IEC 17025:2017 - what a calibration certificate carries. (clause, register field, what the clause asks for)
CERTIFICATE_CLAUSES: List[tuple] = [
    ('7.8.2.1 d', 'certificate_id', 'a unique identification of the certificate'),
    ('7.8.2.1 b', 'lab', 'the name and address of the laboratory'),
    ('7.8.2.1 g', 'serial', 'the identification of the item calibrated (the instrument serial)'),
    ('7.8.2.1 i', 'calibrated_utc', 'the date(s) the calibration was performed'),
    ('7.8.2.1 j', 'issued_utc', 'the date of issue'),
    ('7.8.2.1 m', 'accuracy_pct_fs', 'the results, with the units of measurement (the stated accuracy and full scale)'),
    ('7.8.2.1 f', 'method', 'the method used'),
    ('7.8.2.1 o', 'authorised_by', 'the person(s) authorising the certificate'),
    ('7.8.4.1 a', 'uncertainty', 'the measurement uncertainty of the result, in the same unit or relative to it'),
    ('7.8.4.1 a', 'uncertainty_k', 'the coverage factor (and probability) the expanded uncertainty is stated at'),
    ('7.8.4.1 b', 'conditions', 'the conditions (e.g. environmental) under which the calibration was made that influence the results'),
    ('7.8.4.1 c', 'traceability', 'a statement identifying how the measurements are metrologically traceable'),
    ('7.8.4.1 d', 'adjustment', 'the results before and after adjustment or repair, if available'),
    ('7.8.4.1 e / 7.8.6', 'decision_rule', 'where a statement of conformity is made, the decision rule applied'),
]
REQUIRED_FOR_COMPLETE = ('certificate_id', 'lab', 'serial', 'issued_utc', 'accuracy_pct_fs', 'uncertainty', 'uncertainty_k', 'traceability')


def _present(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    return True


# --------------------------------------------------------------------------------------------------------------
# the decision rule
# --------------------------------------------------------------------------------------------------------------
def decide(y: float, tl: float, U: Optional[float], rule: str = DEFAULT_RULE, k: Optional[float] = 2.0,
           probability: Optional[float] = 0.95, unit: str = '', what: str = '', spec: str = '') -> dict:
    """A measured value against a tolerance limit under a named decision rule.

    y is the measured value (an absolute deviation; the tolerance is symmetric about zero), tl the tolerance
    limit, U the expanded uncertainty of y at coverage factor k. The outcome is in the rule's words, and the
    statement carries what 7.8.6.2 asks for: the result it applies to, the specification, and the rule."""
    if rule not in RULES:
        raise ValueError(f'decision rule {rule!r} is not one of {RULES}')
    y = abs(float(y)); tl = float(tl)
    out: Dict[str, Any] = {'protocol': 'conformance.decide/1', 'rule': rule, 'rule_text': RULE_TEXT[rule], 'y': round(y, 6), 'tolerance_limit': tl,
                           'U': (round(float(U), 6) if U is not None else None), 'k': k, 'probability': probability, 'unit': unit,
                           'applies_to': what, 'specification': spec}
    if U is None or rule == 'simple-acceptance':
        if U is None and rule != 'simple-acceptance':
            out['rule'] = 'simple-acceptance'; out['rule_text'] = RULE_TEXT['simple-acceptance']
            out['note'] = 'no uncertainty was available for the measured value, so the guard-band rule could not be applied; simple acceptance is applied and says so'
        out['acceptance_limit'] = tl; out['guard_band'] = 0.0
        out['outcome'] = 'PASS' if y <= tl else 'FAIL'
    elif rule == 'binary-guard-band':
        w = float(U); al = tl - w
        out['acceptance_limit'] = round(al, 6); out['guard_band'] = round(w, 6)
        out['outcome'] = 'PASS' if y <= al else 'FAIL'
    else:
        w = float(U)
        out['acceptance_limit'] = round(tl - w, 6); out['guard_band'] = round(w, 6)
        out['outcome'] = ('PASS' if y <= tl - w else 'CONDITIONAL PASS' if y <= tl else 'CONDITIONAL FAIL' if y <= tl + w else 'FAIL')
    u = f' {unit}' if unit else ''
    out['statement'] = (f"{what or 'the measured value'}: {out['outcome']} - measured {y:g}{u}"
                        + (f" with expanded uncertainty U = {float(U):g}{u} (k = {k:g}" + (f", {probability:.0%}" if probability else '') + ')' if U is not None else ' (no uncertainty available)')
                        + f" against {spec or 'the specification'} with tolerance limit {tl:g}{u}; decision rule: {out['rule']} (ILAC-G8:09/2019)"
                        + (f"; acceptance limit {out['acceptance_limit']:g}{u}" if out['guard_band'] else ''))
    out['basis'] = 'ISO/IEC 17025:2017 7.8.6: the decision rule is named with the statement, the results it applies to and the specification'
    return out


def expanded_uncertainty(sigma: Optional[float], n: Optional[int], k: float = 2.0, floor: float = 0.0) -> Optional[dict]:
    """The expanded uncertainty of a mean: U = k * sigma / sqrt(n), with an optional floor on the standard
    uncertainty (a reading resolution, say). None when the record cannot say."""
    if sigma is None or n is None or n <= 0:
        return None
    import math
    u = max(float(sigma) / math.sqrt(float(n)), float(floor))
    return {'u': round(u, 6), 'U': round(k * u, 6), 'k': k, 'probability': 0.95 if abs(k - 2.0) < 1e-9 else None, 'n': int(n), 'sigma': float(sigma),
            'basis': 'the standard uncertainty of a mean, sigma / sqrt(n), expanded by k'}


# --------------------------------------------------------------------------------------------------------------
# the certificate against 7.8
# --------------------------------------------------------------------------------------------------------------
def certificate_conformance(entry: dict) -> dict:
    """One register entry against the items ISO/IEC 17025:2017 7.8.2.1 and 7.8.4.1 ask a calibration
    certificate to carry. CARRIED when the register holds it, NOT CARRIED when the field is there and empty,
    NOT RECORDED when the register predates the field (an entry filed before v0.15.0)."""
    rows = []
    for clause, field, text in CERTIFICATE_CLAUSES:
        if field == 'accuracy_pct_fs':
            has = _present(entry.get('accuracy_pct_fs')) and _present(entry.get('full_scale'))
            val = (f"±{entry.get('accuracy_pct_fs')} % FS of {entry.get('full_scale')} {entry.get('unit') or ''}".strip() if has else None)
            standing = 'CARRIED' if has else ('NOT CARRIED' if 'accuracy_pct_fs' in entry else 'NOT RECORDED')
        elif field == 'uncertainty_k':
            has = _present(entry.get('uncertainty_k'))
            val = (f"k = {entry.get('uncertainty_k')}" + (f" ({float(entry['uncertainty_probability']):.0%})" if _present(entry.get('uncertainty_probability')) else '')) if has else None
            standing = 'CARRIED' if has else ('NOT CARRIED' if 'uncertainty_k' in entry else 'NOT RECORDED')
        elif field == 'uncertainty':
            has = _present(entry.get('uncertainty'))
            val = (f"U = {entry.get('uncertainty')} {entry.get('uncertainty_unit') or entry.get('unit') or ''}".strip()) if has else None
            standing = 'CARRIED' if has else ('NOT CARRIED' if 'uncertainty' in entry else 'NOT RECORDED')
        elif field == 'adjustment':
            v = entry.get('adjustment')
            has = _present(v)
            val = str(v) if has else None
            standing = 'CARRIED' if has else ('NOT CARRIED' if 'adjustment' in entry else 'NOT RECORDED')
        else:
            v = entry.get(field)
            has = _present(v)
            val = str(v) if has else None
            standing = 'CARRIED' if has else ('NOT CARRIED' if field in entry else 'NOT RECORDED')
        rows.append({'clause': clause, 'item': text, 'field': field, 'standing': standing, 'value': val})
    missing = [r for r in rows if r['field'] in REQUIRED_FOR_COMPLETE and r['standing'] != 'CARRIED']
    by = {r['field']: r['standing'] for r in rows}
    return {'protocol': 'conformance.certificate/1', 'certificate_id': entry.get('certificate_id'), 'serial': entry.get('serial'), 'tag_id': entry.get('tag_id'),
            'status': 'COMPLETE' if not missing else 'INCOMPLETE', 'rows': rows,
            'carried': sum(1 for r in rows if r['standing'] == 'CARRIED'), 'n_items': len(rows),
            'missing': [f"{r['clause']}: {r['item']}" for r in missing],
            'uncertainty_stated': by.get('uncertainty') == 'CARRIED' and by.get('uncertainty_k') == 'CARRIED',
            'traceability_stated': by.get('traceability') == 'CARRIED',
            'basis': ('ISO/IEC 17025:2017 7.8.2.1 and 7.8.4.1, item by item, against what the operator recorded from the certificate; '
                      'COMPLETE needs the identification, the laboratory, the serial, the dates, the result, the uncertainty with its coverage '
                      'factor, and the traceability statement'),
            'not_a_measurement': ['the paper itself: the register holds what a person transcribed, and NOT CARRIED is the register\'s answer',
                                  'a coverage factor the certificate did not state']}


def register_conformance(entries: List[dict]) -> dict:
    rows = [certificate_conformance(e) for e in entries]
    return {'protocol': 'conformance.register/1', 'n': len(rows), 'complete': sum(1 for r in rows if r['status'] == 'COMPLETE'),
            'incomplete': [r['certificate_id'] for r in rows if r['status'] != 'COMPLETE'], 'rows': rows}


def report_lines(c: dict) -> List[str]:
    lines = [f"certificate {c.get('certificate_id')} (serial {c.get('serial')}, tag {c.get('tag_id')}): {c['status']} - {c['carried']} of {c['n_items']} items carried"]
    for r in c['rows']:
        lines.append(f"   {r['clause']:<14} {r['standing']:<12} {r['item']}" + (f" -> {r['value']}" if r['value'] else ''))
    return lines


def selftest() -> dict:
    full = {'certificate_id': 'C-1', 'tag_id': 'P1', 'serial': 'S1', 'lab': 'Acme Cal Lab, 1 Main St', 'issued_utc': '2026-01-10T00:00:00Z',
            'calibrated_utc': '2026-01-09T00:00:00Z', 'valid_until_utc': '2027-01-10T00:00:00Z', 'accuracy_pct_fs': 0.1, 'full_scale': 10000.0, 'unit': 'psi',
            'method': 'deadweight tester comparison', 'authorised_by': 'J. Doe', 'uncertainty': 2.5, 'uncertainty_unit': 'psi', 'uncertainty_k': 2.0,
            'uncertainty_probability': 0.95, 'conditions': '23 ± 1 °C', 'traceability': 'NIST via DWT serial 1234', 'adjustment': 'as found / as left recorded',
            'decision_rule': 'ILAC-G8 non-binary guard band'}
    thin = {'certificate_id': 'C-2', 'tag_id': 'P2', 'serial': 'S2', 'lab': 'Lab', 'issued_utc': '2026-01-10T00:00:00Z', 'valid_until_utc': '2027-01-10T00:00:00Z',
            'accuracy_pct_fs': 0.1, 'full_scale': 10000.0, 'unit': 'psi', 'uncertainty': None, 'uncertainty_k': None, 'traceability': '', 'conditions': '',
            'method': '', 'authorised_by': '', 'calibrated_utc': '', 'adjustment': '', 'decision_rule': ''}
    old = {'certificate_id': 'C-0', 'tag_id': 'P0', 'serial': 'S0', 'lab': 'Lab', 'issued_utc': '2025-01-10T00:00:00Z', 'valid_until_utc': '2026-01-10T00:00:00Z',
           'accuracy_pct_fs': 0.1, 'full_scale': 10000.0, 'unit': 'psi'}
    cf, ct, co = certificate_conformance(full), certificate_conformance(thin), certificate_conformance(old)
    d1 = decide(4.0, 10.0, 2.0, what='bias of P1', spec='±0.1 % FS = ±10 psi', unit='psi')
    d2 = decide(9.0, 10.0, 2.0, what='bias', spec='spec', unit='psi')
    d3 = decide(11.0, 10.0, 2.0, what='bias', spec='spec', unit='psi')
    d4 = decide(13.0, 10.0, 2.0, what='bias', spec='spec', unit='psi')
    d5 = decide(9.0, 10.0, None, what='bias', spec='spec', unit='psi')
    d6 = decide(9.0, 10.0, 2.0, rule='binary-guard-band')
    d7 = decide(-9.5, 10.0, 2.0, rule='simple-acceptance')
    eu = expanded_uncertainty(3.0, 36)
    checks = {
        'full_complete': cf['status'] == 'COMPLETE' and cf['carried'] == cf['n_items'] and cf['uncertainty_stated'] and cf['traceability_stated'],
        'thin_incomplete': ct['status'] == 'INCOMPLETE' and any('7.8.4.1 c' in m for m in ct['missing']) and any('7.8.4.1 a' in m for m in ct['missing'])
                           and all(r['standing'] in ('CARRIED', 'NOT CARRIED') for r in ct['rows']),
        'old_not_recorded': co['status'] == 'INCOMPLETE' and sum(1 for r in co['rows'] if r['standing'] == 'NOT RECORDED') >= 6,
        'outcomes': (d1['outcome'], d2['outcome'], d3['outcome'], d4['outcome']) == ('PASS', 'CONDITIONAL PASS', 'CONDITIONAL FAIL', 'FAIL')
                    and d1['acceptance_limit'] == 8.0 and d1['guard_band'] == 2.0,
        'no_uncertainty_is_simple_acceptance': d5['rule'] == 'simple-acceptance' and d5['outcome'] == 'PASS' and 'not taken into account' in d5['rule_text'] and 'note' in d5,
        'binary': d6['outcome'] == 'FAIL' and d6['acceptance_limit'] == 8.0,
        'simple_abs': d7['outcome'] == 'PASS' and d7['y'] == 9.5,
        'statement_carries_7_8_6_2': all(x in d1['statement'] for x in ('bias of P1', '±0.1 % FS', 'decision rule: non-binary-guard-band', 'k = 2', 'U = 2')),
        'expanded': eu is not None and abs(eu['u'] - 0.5) < 1e-9 and abs(eu['U'] - 1.0) < 1e-9 and eu['probability'] == 0.95 and expanded_uncertainty(None, 10) is None,
    }
    return {'label': 'SELF_TEST', 'status': 'OK' if all(checks.values()) else 'FAILED', 'checks': checks, 'full': cf, 'thin': ct, 'decisions': [d1, d2, d3, d4, d5]}
