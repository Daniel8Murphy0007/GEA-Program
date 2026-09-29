# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""survey_cmd - THE ONE-COMMAND USER PATH (Daniel's build order,
2026-09-08: "build the survey command").

    gea survey mywell.las
    gea survey --demo

A person with a LAS file and zero knowledge of this repository's history
gets one readable answer: what the ground under this well looks like, how
sure the tool is, and what it refused to guess. The chain is nothing new -
it is the SAME engine the acceptance suite already gates (LAS 2.0 reader,
K2 forward kernel, family-prior strata estimation, cited WGS84 reference
QC) - walked end-to-end behind a single door.

HONESTY CONTRACT (unchanged from every layer below):
  * every estimate carries n, spread, and support - or it is refused;
  * every exclusion (nulls, washouts) is counted and printed;
  * every number names its provenance;
  * the prior family is an ASSUMPTION and the report says so;
  * nothing is invented for missing channels - the tool says what it
    cannot do with this file and what channel would unlock it.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional, Tuple

from .ports import read_las
from .profile_catalog import CATALOG
from .forward_model import predict_delta_g_mgal, implied_density_gcc
from .inverse_engine import (PRIOR_FAMILIES, WASHOUT_RHO_GCC,
                                  _site_native_pairs, _conditional_from_pairs)
from .gravity_reference import site_reference_gravity

DEMO_ENTRY = 'ktb_hb_complog_6020_excerpt'

# channel-name synonyms (case-insensitive prefix match on the mnemonic)
_SYNONYMS = {
    'density': ('RHOB', 'RHOZ', 'DEN', 'RHO'),
    'sonic': ('DTCO', 'DT', 'AC'),
    'gravity': ('GRAV', 'GRV'),
    'gamma': ('SGR', 'GR', 'HSGR'),
}


def _find_channel(channels: Dict, kind: str) -> Optional[str]:
    for key in channels:
        mnem = key.split()[0].split('(')[0].strip().upper()
        for syn in _SYNONYMS[kind]:
            if mnem == syn or mnem.startswith(syn):
                return key
    return None


def _floats(channels, key) -> List[float]:
    return [float(v) for v in channels[key].values]


def run_survey(path: Optional[str] = None, demo: bool = False,
               family: str = 'continental_crystalline',
               lat: Optional[float] = None,
               elev: Optional[float] = None) -> Tuple[str, Dict]:
    """Run the end-to-end survey chain; returns (report_text, machine_dict)."""
    L: List[str] = []
    out: Dict = {'refusals': [], 'exclusions': {}}
    w = L.append

    if demo:
        st = CATALOG[DEMO_ENTRY].stream()
        src_label = ('bundled demo: %s (KTB main hole composite-log '
                     'excerpt, GFZ public archive)' % DEMO_ENTRY)
    else:
        if not path:
            raise SystemExit('survey: give a LAS file, or use --demo')
        st = read_las(path)
        src_label = '%s (LAS 2.0, read by the las2 port)' % path

    ch = st.channels
    depth = [float(v) for v in st.index]
    keys = {k: _find_channel(ch, k) for k in _SYNONYMS}
    out['source'] = src_label
    out['channels_found'] = {k: v for k, v in keys.items() if v}
    out['n_stations'] = len(depth)

    w('GEA SURVEY - one well, one honest answer')
    w('=' * 60)
    w('source: %s' % src_label)
    w('stations: %d   depth %.1f - %.1f m' % (len(depth),
                                              min(depth), max(depth)))
    w('channels recognized: %s' % (', '.join(
        '%s -> %s' % (k, v) for k, v in keys.items() if v) or 'NONE'))
    w('')

    fam = PRIOR_FAMILIES.get(family)
    if fam is None:
        raise SystemExit('survey: unknown prior family %r (choose from %s)'
                         % (family, sorted(PRIOR_FAMILIES)))

    # ---- density column: measured, or implied from gravity ---------------
    rho: Optional[List[float]] = None
    rho_provenance = ''
    if keys['density']:
        rho = _floats(ch, keys['density'])
        rho_provenance = ('measured density channel %s' % keys['density'])
    elif keys['gravity']:
        grav = _floats(ch, keys['gravity'])
        rho = [float('nan')] * len(depth)
        for i in range(1, len(depth)):
            dz = depth[i] - depth[i - 1]
            if dz > 0:
                rho[i] = implied_density_gcc(grav[i] - grav[i - 1], dz)
        rho_provenance = ('density IMPLIED from interstation gravity via the '
                          'K2 GEA kernel (forward_model)')
    else:
        out['refusals'].append(
            'no density (RHOB) and no gravity (GRAV) channel - the tool '
            'cannot characterize strata from this file; add either channel '
            'to unlock the chain')
        w('REFUSED: %s' % out['refusals'][-1])
        out['report'] = '\n'.join(L)
        return '\n'.join(L), out

    # washout / null exclusion (disclosed)
    keep = [i for i, r in enumerate(rho)
            if r == r and r > WASHOUT_RHO_GCC]
    n_excluded = len(depth) - len(keep)
    out['exclusions']['washout_or_null_stations'] = n_excluded
    w('density column: %s' % rho_provenance)
    w('  %d stations kept; %d excluded (nulls / washouts RHOB <= %.1f '
      'g/cc) - hole artifacts, not rock' % (len(keep), n_excluded,
                                            WASHOUT_RHO_GCC))
    rho_k = [rho[i] for i in keep]
    if len(rho_k) < 5:
        out['refusals'].append('fewer than 5 usable stations - refusing to '
                               'summarize strata on this little support')
        w('REFUSED: %s' % out['refusals'][-1])
        out['report'] = '\n'.join(L)
        return '\n'.join(L), out

    w('  density %.2f - %.2f g/cc (mean %.2f, n=%d)'
      % (min(rho_k), max(rho_k), statistics.mean(rho_k), len(rho_k)))
    out['density_gcc'] = {'min': min(rho_k), 'max': max(rho_k),
                          'mean': statistics.mean(rho_k), 'n': len(rho_k)}
    w('')

    # ---- forward gravity signature (K2 GEA kernel) ----------------------
    dzs = [depth[keep[j]] - depth[keep[j - 1]] for j in range(1, len(keep))]
    dz_med = statistics.median(dzs) if dzs else 0.0
    if dz_med > 0:
        dgs = [predict_delta_g_mgal(r, dz_med) for r in rho_k]
        w('predicted interstation gravity signature (K2 GEA kernel, '
          'standard constants): %.4f to %.4f mGal per %.2f m station'
          % (min(dgs), max(dgs), dz_med))
        out['k2_dg_mgal'] = {'min': min(dgs), 'max': max(dgs),
                             'dz_m': dz_med}
        w('')

    # ---- Vp estimate via the chosen prior family -------------------------
    pairs, washouts = (None, 0)
    if fam['kind'] == 'site_pairs':
        pairs, washouts = _site_native_pairs(fam['source'])
    if pairs:
        ests = [_conditional_from_pairs(pairs, r) for r in rho_k]
        oks = [e for e in ests if e.get('status') == 'OK'
               and e.get('estimate') and not e.get('extrapolation')]
        n_extrap = sum(1 for e in ests if e.get('extrapolation'))
        if oks:
            vals = [e['estimate'] for e in oks]
            stds = [e['std'] for e in oks if e.get('std')]
            w('Vp (compressional velocity) estimate:')
            w('  %d - %d m/s (mean %d, typical 1-sigma %d, n=%d '
              'in-support stations; %d extrapolating stations WITHHELD)'
              % (min(vals), max(vals), statistics.mean(vals),
                 statistics.mean(stds) if stds else 0, len(oks), n_extrap))
            w('  ASSUMPTION (printed, not hidden): prior family = %s - %s'
              % (family, fam['note']))
            out['vp_m_s'] = {'min': min(vals), 'max': max(vals),
                             'mean': statistics.mean(vals), 'n': len(oks),
                             'withheld_extrapolating': n_extrap,
                             'prior_family': family}
        else:
            out['refusals'].append('all stations outside the prior '
                                   'family support - no Vp estimate rather '
                                   'than an extrapolated one')
            w('REFUSED: %s' % out['refusals'][-1])
        # honesty cross-check where the file carries its own sonic
        if keys['sonic']:
            son = _floats(ch, keys['sonic'])
            meas = [1e6 / son[i] for i in keep if son[i] > 0]
            if meas and oks:
                mm = statistics.mean(meas)
                pm = statistics.mean(vals)
                w('  CROSS-CHECK: this file carries its own sonic (%s) - '
                  'measured mean Vp %d m/s vs estimated %d (%+.1f%%); the '
                  'tool grades itself when the data allows'
                  % (keys['sonic'], mm, pm, (pm - mm) / mm * 100))
                out['vp_cross_check_pct'] = (pm - mm) / mm * 100
    w('')

    # ---- cited external reference QC ------------------------------------
    if lat is not None and elev is not None:
        ref = site_reference_gravity(lat, elev)
        w('site reference gravity (CITED external standard - WGS84 '
          'Somigliana + free-air, NGA TR8350.2): %.5f m/s2 at %.4f N, '
          '%.0f m' % (ref['reference_gravity_ms2'], lat, elev))
        out['site_reference'] = ref
    else:
        w('site reference QC: skipped (give --lat and --elev to check '
          'this survey against the cited WGS84 reference)')
    w('')

    # ---- rock candidates (K4 geological landmark family) -----------------
    from .rock_inventory import classify_density
    votes = {}
    for r in rho_k:
        for h in classify_density(r)['candidates'][:3]:
            votes[h['name']] = votes.get(h['name'], 0) + 1
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])[:3]
    if ranked:
        w('rock candidates (K4 landmark family, density-only, RANKED not '
          'certain):')
        w('  ' + ', '.join('%s (%d/%d stations)' % (n, c, len(rho_k))
                           for n, c in ranked))
        w('  HONESTY: density ranges overlap - these are candidates, not '
          'an identification; density-degenerate twins (e.g. '
          'amphibolite/basalt) are indistinguishable by design')
        out['rock_candidates'] = ranked
    else:
        w('rock candidates: none - the density column is outside the K4 '
          'inventory (stated, not guessed)')
        out['rock_candidates'] = []
    if keys['sonic']:
        from .rock_inventory import classify_joint
        son2 = _floats(ch, keys['sonic'])
        jvotes = {}
        for i in keep:
            if son2[i] > 0:
                for h in classify_joint(rho[i], 1e6 / son2[i])['candidates'][:2]:
                    jvotes[h['name']] = jvotes.get(h['name'], 0) + 1
        jranked = sorted(jvotes.items(), key=lambda kv: -kv[1])[:3]
        if jranked:
            w('  TWO-CHANNEL (density + sonic) shortlist - the sharper one: '
              + ', '.join('%s (%d)' % (n, c) for n, c in jranked))
            w('  the second channel splits the density-degenerate twins '
              '(amphibolite/basalt Vp tiers are disjoint)')
            out['rock_candidates_joint'] = jranked
    w('')

    # ---- what the tool will NOT do ---------------------------------------
    w('what this tool refused to guess:')
    w('  - a SINGLE confident rock name: density-only identification is '
      'a ranked shortlist with disclosed overlap, never a certainty')
    if not keys['gamma']:
        w('  - lithology proxies from gamma: no gamma channel in this file')
    for r_ in out['refusals']:
        w('  - %s' % r_)
    w('')
    w('every number above carries its provenance; the assumptions are '
      'printed where they act. This report is honest or it is nothing.')
    out['report'] = '\n'.join(L)
    return '\n'.join(L), out
