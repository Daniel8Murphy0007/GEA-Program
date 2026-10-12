# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""catalog_register - what every shipped catalogue entry is for, and what reads it.

The catalogue ships 58 public entries with provenance sidecars. A sidecar says where an entry came from; it does
not say what in this program reads it. This register does: for each entry, the bands, commands and reports that
take it (USED BY), or the plain statement that nothing does yet (CARRIED, NO BAND). The gate holds the register
to the code - an entry a module names by its stem must be registered as used, an entry registered as used must
be named somewhere - and reads every entry through its own reader so a carried file is at least a readable one.

The measured-friction entries feed `friction_priors` (the envelope a slip-tendency statement discloses its
coefficient against); the Volve core feeds `rock_inventory.core_check` (the published sandstone range checked
against core-derived bulk density); every LAS entry runs through `gea survey --catalog`; the rest are stated.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

from .profile_catalog import CATALOG, CatalogEntry

# entry -> the places in this program that read it, in the user's terms
USES: Dict[str, List[str]] = {
    'volve_f12_f14_production_excerpt': ['gea quickstart (the Volve catalogue well)', 'gea dashboard --catalog-well', 'the drift report, well tests, alarms and SLA on the sample reports',
                                         'the Volve well-test validation samples'],
    'ktb_hb_complog_6020_excerpt': ['gea survey --demo (the bundled strata survey)', 'the KTB lithology validation and the rock inventory model card', 'the accuracy statement, blind harness, inverse engine'],
    'ktb_hb_bhgm_density': ['the ktb_hb assembly (gea wells): density'],
    'ktb_hb_hlog246_temperature': ['the ktb_hb assembly (gea wells): temperature'],
    'ktb_hb_tvd_0_9080_excerpt': ['the ktb_hb assembly (gea wells): the trajectory (CatalogEntry.survey)'],
    'ktb_hb_rockmech_compress': ['the ktb_hb assembly (gea wells): the strength attachment (strata_join)'],
    'dsdp_504b_physical_properties': ['the odp_504b assembly (gea wells): density'],
    'dsdp_504b_sound_velocity': ['the odp_504b assembly (gea wells): the measured Vp'],
    'odp_504b_dike_elastic_moduli': ['the odp_504b assembly (gea wells): the elastics attachment'],
    'odp_504b_leg137_borehole_fluids': ['the odp_504b assembly (gea wells): the fluids attachment'],
    'odp_1027c_cork_temperature': ['the site_1027 assembly (gea wells): the CORK temperature record'],
    'odp_1027b_thermal_conductivity': ['the site_1027 assembly (gea wells): the thermal-conductivity attachment'],
    'iodp_u1324_pore_pressure': ['the u1324 assembly (gea wells): pore pressure and overburden'],
    'gom_308_t2p_insitu': ['the u1324 assembly (gea wells): in-situ temperature'],
    'volve_15_9_19_sr_excerpt': ['gea gamma (lithology from GR)', 'the las2 port end-to-end (profile_catalog)', 'gea survey --catalog'],
    'ktb_vb_vlog251_temperature': ['gea gamma (lithology from GR)'],
    'l07_01_nl_excerpt': ['gea gamma (lithology from GR)', 'gea survey --catalog'],
    # v0.25.0: the entries that now have a use
    'kennetcook_2_p129_excerpt': ['gea survey --catalog (a real Nova Scotia log through the strata survey)'],
    'collingwood_1_28_ks_complete': ['gea survey --catalog (a real Kansas log through the strata survey)'],
    'scorpio_e1_sa_excerpt': ['gea survey --catalog (a real South Australian log through the strata survey)'],
    'university_6_17_no1_tx_excerpt': ['gea survey --catalog (a real Texas log through the strata survey)'],
    'volve_15_9_19a_core_excerpt': ['rock_inventory.core_check: the published sandstone density range against core-derived bulk density (model card evaluation)'],
    'costa_rica_odp_friction_envelope': ['friction_priors: measured friction coefficient envelope (slip tendency discloses its coefficient against it)'],
    'hikurangi_u1520_friction_insitu': ['friction_priors: peak and steady-state friction per lithology, a-b'],
    'jfast_c0019_slow_slip_events': ['friction_priors: the Tohoku fault-zone friction at slow-slip rates'],
    'nankai_megasplay_shear_strength': ['friction_priors: shear strength over effective stress'],
    'sumatra_362_cohesion': ['friction_priors: cohesion of intact and powdered samples'],
    'ktb_vb_rockmech_compress': ['friction_priors: compressive strength of crystalline basement cores (the strength end of the envelope)'],
    'barbados_110_consolidation': ['friction_priors: vane shear strength over effective overburden of accretionary sediments'],
}
NO_BAND_REASON = ('a science-hole dataset gathered for the simulator\'s priors; no band of a gauge-monitoring and disposal-well program reads it. '
                  'Carried with its provenance; dropping it from the wheel is the owner\'s call (gea catalog --unused).')


def _source_text() -> str:
    """Every module of the package except this register and the gate, as one string."""
    pkg = Path(__file__).parent
    parts = []
    for p in sorted(pkg.glob('*.py')):
        if p.name in ('catalog_register.py', 'acceptance_tests.py'):
            continue
        parts.append(p.read_text(encoding='utf-8', errors='replace'))
    for p in sorted((pkg.parent / 'tools').glob('*.py')) if (pkg.parent / 'tools').is_dir() else []:
        parts.append(p.read_text(encoding='utf-8', errors='replace'))
    return '\n'.join(parts)


def public() -> Dict[str, CatalogEntry]:
    """The shipped tier only. Operator-tier entries (a private folder on one machine, never in the wheel) are not registered,
    listed, audited or named by this module: their presence is counted and nothing more."""
    return {n: e for n, e in CATALOG.items() if e.provenance.get('tier', 'public') == 'public'}


def n_operator() -> int:
    return sum(1 for e in CATALOG.values() if e.provenance.get('tier', 'public') != 'public')


def referenced_in_code() -> Dict[str, int]:
    """Public entry stems named in the package source (outside this register and the gate)."""
    src = _source_text()
    return {name: len(re.findall(re.escape(name), src)) for name in public() if re.search(re.escape(name), src)}


def register() -> List[dict]:
    rows = []
    for name, e in sorted(public().items()):
        prov = e.provenance
        uses = USES.get(name, [])
        rows.append({'entry': name, 'kind': prov.get('kind', ''), 'tier': prov.get('tier', 'public'), 'file': e.las_path.name,
                     'bytes': os.path.getsize(e.las_path), 'source': str(prov.get('source_database', ''))[:80], 'license': str(prov.get('license', ''))[:40],
                     'used_by': uses, 'status': 'USED' if uses else 'CARRIED, NO BAND', 'why': '' if uses else NO_BAND_REASON})
    return rows


def check_entry(e: CatalogEntry) -> dict:
    """Read one entry through its own reader; the shape or the error."""
    try:
        if e.las_path.suffix.lower() == '.csv':
            from .profile_catalog import _csv_kind
            if _csv_kind(e.las_path) == 'survey':
                sv = e.survey()
                return {'entry': e.name, 'ok': True, 'shape': f'deviation survey, {len(sv.md_ft)} stations'}
        st = e.stream()
        return {'entry': e.name, 'ok': True, 'shape': f'{st.index_kind}, {len(st.index)} rows, {len(st.channels)} channels'}
    except Exception as ex:                                      # noqa: BLE001 - the point is to name it
        return {'entry': e.name, 'ok': False, 'shape': f'{type(ex).__name__}: {ex}'}


def check_all() -> List[dict]:
    return [check_entry(e) for _, e in sorted(public().items())]


def audit() -> dict:
    """The register against the code: registered-as-used but never named; named in code but not registered."""
    ref = referenced_in_code()
    used = {n for n, u in USES.items() if u}
    # an entry whose only use is this leg's own modules is named in those modules; check that too
    unregistered = sorted(n for n in ref if n not in used)
    # a LAS entry is reachable by `gea survey --catalog <entry>` without any module naming it
    unnamed = sorted(n for n in used if n not in ref and CATALOG[n].las_path.suffix.lower() != '.las')
    pub = public()
    return {'referenced': ref, 'unregistered': unregistered, 'unnamed': unnamed, 'n_used': len(used), 'n_carried': len(pub) - len(used), 'n_total': len(pub),
            'n_operator': n_operator()}


def text(rows: List[dict], unused_only: bool = False) -> str:
    out = []
    n_used = sum(1 for r in rows if r['status'] == 'USED')
    out.append(f"catalogue: {len(rows)} entries, {n_used} used, {len(rows) - n_used} carried with no band"
               + (f"; operator tier: {n_operator()} entries present on this machine (private; not listed here)" if n_operator() else ''))
    w = max(len(r['entry']) for r in rows)
    for r in rows:
        if unused_only and r['status'] == 'USED':
            continue
        out.append(f"  {r['entry']:{w}s}  {r['status']:<17s} {r['kind']}")
        for u in r['used_by']:
            out.append(f"  {'':{w}s}    - {u}")
    return '\n'.join(out)
