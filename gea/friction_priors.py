# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""friction_priors - the measured friction and strength the catalogue holds, as the envelope a slip statement discloses.

A slip-tendency number needs a friction coefficient, and the one everyone uses (0.6, Byerlee) is a law from
crystalline rock at crustal stress - not a measurement on the fault in question. This module reads the
laboratory measurements the catalogue carries on natural fault and sediment samples - ODP Costa Rica friction
ladders (0.21-0.88 across 15-90 MPa and 19-212 C), IODP Hikurangi peak and steady-state friction per
lithology, the Tohoku (JFAST) fault-zone material at slow-slip rates, Nankai shear strength over effective
stress, Sumatra cohesion, Barbados vane shear over overburden, and the KTB pilot hole's compressive strength
of crystalline cores - and states them as what they are: the range of measured coefficients on real samples,
each with its provenance, none of them the Permian's. A slip-tendency statement built on 0.6 prints this
envelope beside it so the reader sees how far a measured coefficient can sit from the law.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from .profile_catalog import CATALOG

ENTRIES = ('costa_rica_odp_friction_envelope', 'hikurangi_u1520_friction_insitu', 'jfast_c0019_slow_slip_events', 'nankai_megasplay_shear_strength',
           'sumatra_362_cohesion', 'barbados_110_consolidation', 'ktb_vb_rockmech_compress')
BYERLEE = 0.6


def _vals(st, name: str) -> np.ndarray:
    c = st.channels.get(name)
    if c is None:
        return np.array([])
    v = np.asarray(c.values, dtype=float)
    return v[~np.isnan(v)]


def _stat(v: np.ndarray) -> Optional[dict]:
    if v.size == 0:
        return None
    return {'n': int(v.size), 'min': round(float(v.min()), 4), 'median': round(float(np.median(v)), 4), 'max': round(float(v.max()), 4)}


def measured() -> List[dict]:
    """Per entry: the friction coefficient (or the strength the entry measures), the conditions, the provenance."""
    out: List[dict] = []
    for name in ENTRIES:
        e = CATALOG.get(name)
        if e is None:
            continue
        st = e.stream()
        prov = e.provenance
        row = {'entry': name, 'kind': prov.get('kind', ''), 'source': str(prov.get('source_database', ''))[:90], 'url': str(prov.get('source_url', '')),
               'license': str(prov.get('license', ''))[:60], 'material': '', 'mu': None, 'strength': None, 'conditions': ''}
        if name == 'costa_rica_odp_friction_envelope':
            row['material'] = 'ODP 170/205 Costa Rica margin sediments (Ikari et al. 2013/2014)'
            row['mu'] = _stat(_vals(st, 'Coeff (of friction)'))
            p, t = _vals(st, 'P (effective normal stress)'), _vals(st, 'T:temp')
            row['conditions'] = f'effective normal stress {p.min():g}-{p.max():g} MPa, {t.min():g}-{t.max():g} C'
        elif name == 'hikurangi_u1520_friction_insitu':
            row['material'] = 'IODP U1520 Hikurangi input sediments, six lithologies (Eijsink & Ikari 2022)'
            pk, ss = _vals(st, 'µ (peak)'), _vals(st, 'µ (steady-state)')
            row['mu'] = _stat(np.concatenate([pk, ss]))
            row['mu_peak'] = _stat(pk); row['mu_steady'] = _stat(ss)
            ab = _vals(st, 'a-b')
            row['a_minus_b'] = _stat(ab)
            sn = _vals(st, 'SigmaN (eff)')
            row['conditions'] = f'in-situ effective normal stress {sn.min() / 1000:g}-{sn.max() / 1000:g} MPa; a-b {ab.min():+.4f} to {ab.max():+.4f} (velocity weakening where negative)'
        elif name == 'jfast_c0019_slow_slip_events':
            row['material'] = 'IODP 343 JFAST Tohoku plate-boundary fault zone (Ikari et al. 2015)'
            tau = _vals(st, 'Shear str max'); mu_inc = _vals(st, 'µ (increase)')
            row['strength'] = {'what': 'peak shear strength during slow slip events', 'unit': 'kPa', **(_stat(tau) or {})}
            row['mu_change_during_sse'] = _stat(mu_inc)
            row['conditions'] = 'plate-rate shearing; five of seven experiments produced slow slip events'
        elif name == 'nankai_megasplay_shear_strength':
            row['material'] = 'IODP 315/316 Nankai megasplay slope sediments (Ikari et al. 2011)'
            tau, sv = _vals(st, 'Tau'), _vals(st, 'SigmaV ef')
            n = min(tau.size, sv.size)
            mu = tau[:n] / np.where(sv[:n] > 0, sv[:n], np.nan)
            row['mu'] = _stat(mu[~np.isnan(mu)])
            row['mu_how'] = 'shear strength / effective vertical stress, per sample'
            row['conditions'] = f'effective vertical stress {sv.min():g}-{sv.max():g} kPa'
        elif name == 'sumatra_362_cohesion':
            row['material'] = 'IODP 362 Sumatra input sediments (Stanislowski & Ikari 2025)'
            c = _vals(st, 'C'); cc = _vals(st, 'C coef')
            row['strength'] = {'what': 'cohesion', 'unit': 'kPa', **(_stat(c) or {})}
            row['cohesion_coefficient'] = _stat(cc)
            row['conditions'] = 'effective stresses 10.7-14.0 MPa; intact and powdered samples'
        elif name == 'barbados_110_consolidation':
            row['material'] = 'ODP 110 Barbados accretionary prism sediments (Taylor & Leonard 1990)'
            vane = _vals(st, 'Shear str (vane)')
            ob = next((_vals(st, k) for k in st.channels if k.startswith('Overb press')), np.array([0.0]))
            row['strength'] = {'what': 'vane shear strength', 'unit': 'kPa', **(_stat(vane) or {})}
            row['conditions'] = f'effective overburden {ob.min():g}-{ob.max():g} kPa'
        elif name == 'ktb_vb_rockmech_compress':
            row['material'] = 'KTB pilot hole crystalline cores, 190-3,832 m (gneisses, amphibolites)'
            cs = _vals(st, 'COMPRESSIVE_STRENGTH'); em = _vals(st, 'E_MODUL')
            row['strength'] = {'what': 'uniaxial compressive strength', 'unit': 'MPa', **(_stat(cs) or {})}
            row['youngs_modulus_GPa'] = _stat(em)
            row['conditions'] = 'laboratory, cores by depth; foliated rocks anisotropic'
        out.append(row)
    return out


def envelope() -> dict:
    """The union of the measured friction coefficients, against Byerlee's 0.6."""
    rows = measured()
    mus = [r['mu'] for r in rows if r.get('mu')]
    if not mus:
        return {'n_entries': 0}
    lo = min(m['min'] for m in mus); hi = max(m['max'] for m in mus)
    return {'n_entries': len(mus), 'n_samples': sum(m['n'] for m in mus), 'min': lo, 'max': hi, 'byerlee': BYERLEE,
            'entries': [r['entry'] for r in rows if r.get('mu')],
            'statement': (f"measured friction coefficients on natural samples in this program's catalogue run from {lo:g} to {hi:g} "
                          f"({sum(m['n'] for m in mus)} measurements, {len(mus)} datasets; none from the Permian Basin); Byerlee's law gives {BYERLEE:g}. "
                          f"A slip-tendency statement that assumes {BYERLEE:g} is one point in that range, and the reader should know it.")}


def text() -> str:
    out = ['Measured friction and strength in the catalogue (each with its provenance; none from the site):']
    for r in measured():
        out.append(f"  {r['entry']}: {r['material']}")
        if r.get('mu'):
            m = r['mu']; out.append(f"    friction coefficient {m['min']:g}-{m['max']:g} (median {m['median']:g}, n={m['n']})" + (f"; {r['mu_how']}" if r.get('mu_how') else ''))
        if r.get('strength'):
            sg = r['strength']; out.append(f"    {sg['what']} {sg.get('min', '?'):g}-{sg.get('max', '?'):g} {sg['unit']} (n={sg.get('n', 0)})")
        if r.get('a_minus_b'):
            ab = r['a_minus_b']; out.append(f"    a-b {ab['min']:+.4f} to {ab['max']:+.4f} (n={ab['n']})")
        out.append(f"    conditions: {r['conditions']}")
        out.append(f"    source: {r['source']} [{r['license']}]")
    env = envelope()
    out.append('')
    out.append(env.get('statement', 'no friction coefficients found'))
    return '\n'.join(out)
