# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""correlation - Part 4 of the subsurface surveying tool: WELL-TO-WELL
CORRELATION (v1.81.0), with the honest finding stated up front.

THE FINDING (2026-08-29, and re-verified on every run): with 29 globally
scattered sites, the library currently supports ZERO cross-site depth-frame
correlations - every candidate pair (CaCO3 Barbados x Walvis, thermal
conductivity Hikurangi x Prydz, friction coefficient Barbados x Costa Rica)
has overlapping depth RANGES but too few coinciding SAMPLES (max 3 common
bins against a floor of 5). The engine reports that refusal census instead
of manufacturing correlations, and the machinery stands ready for the data
that will change the answer (co-located suites, twin holes, denser columns).

WHERE THE REAL STRUCTURE LIVES - THE TIME FRAME: four sites carry archive
age models, and one (the ACEX Lomonosov Ridge age-depth model, 0-55,904 ka)
is the library's MASTER CHRONOLOGY, whose window overlaps every other
age-bearing site (Bengal 0.5-193.5 ka; EPICA 611-799 ka; Fram 12,050-17,370
ka). Time is the Earth Model's fourth axis, and this module registers it.

DISTANCE HONESTY (doctrine): a statistical correlation between sites more
than CONTINUITY_KM apart is NEVER presented as geological continuity -
the continuity_claim field says 'NONE' with the distance printed. Only
twin holes (< CONTINUITY_KM) may claim physical continuity, and none
currently qualify with shared properties.
"""

from __future__ import annotations

import collections
import math
import re
import statistics
from typing import Dict, List

from .earth_model import EarthModel, haversine_km

CONTINUITY_KM = 5.0        # beyond this, correlation is statistics, not strata
MIN_COMMON_BINS = 5        # below this, the pair REFUSES (disclosed)
TARGET_BINS = 8            # default overlap resolution (disclosed)

_AGE_WORD = re.compile(r'\bage\b', re.IGNORECASE)


def _family(prop: str) -> str:
    return prop.split(' [')[0].split(' (')[0].strip().lower()


def time_frame(em: EarthModel = None) -> Dict:
    """The library's fourth axis: every archive age model, its window, the
    pairwise epoch overlaps, and the master chronology (the site whose
    window overlaps the most others)."""
    em = em or EarthModel()
    ages = []
    for s in em.sites.values():
        for r in s.records:
            if _AGE_WORD.search(r.property) and 'ka' in (r.unit or '').lower():
                vals = [v for v in r.values if v == v]
                if vals:
                    ages.append({'site': s.key, 'entry': r.entry,
                                 'property': r.property, 'unit': r.unit,
                                 'age_min_ka': min(vals), 'age_max_ka': max(vals)})
    overlaps = []
    for i in range(len(ages)):
        for j in range(i + 1, len(ages)):
            a, b = ages[i], ages[j]
            if a['site'] == b['site']:
                continue
            lo = max(a['age_min_ka'], b['age_min_ka'])
            hi = min(a['age_max_ka'], b['age_max_ka'])
            if hi > lo:
                overlaps.append({'a': a['site'], 'b': b['site'],
                                 'epoch_overlap_ka': (lo, hi),
                                 'span_ka': round(hi - lo, 3)})
    counts = collections.Counter()
    for o in overlaps:
        counts[o['a']] += 1
        counts[o['b']] += 1
    master = counts.most_common(1)[0] if counts else (None, 0)
    return {'age_bearing_records': ages, 'n_sites': len({a['site'] for a in ages}),
            'epoch_overlaps': overlaps,
            'master_chronology': {'site': master[0], 'overlaps_others': master[1]}}


def depth_frame_pairs(em: EarthModel = None, target_bins: int = TARGET_BINS,
                      min_common: int = MIN_COMMON_BINS) -> Dict:
    """Every cross-site same-property pair with overlapping z_ref: correlated
    where the samples support it, REFUSED with counts where they do not -
    and today they do not, anywhere. Nothing is interpolated between
    archives; bins carry only real samples."""
    em = em or EarthModel()
    recs = []
    for s in em.sites.values():
        for r in s.records:
            if r.z_ref_m and r.values:
                pts = [(z, v) for z, v in zip(r.z_ref_m, r.values) if v == v]
                if len(pts) >= 5:
                    recs.append((s, r, pts))
    fams = collections.defaultdict(list)
    for s, r, pts in recs:
        fams[_family(r.property)].append((s, r, pts))
    ok, refused = [], []
    for fname, group in fams.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                sa, ra, pa = group[i]
                sb, rb, pb = group[j]
                if sa.key == sb.key:
                    continue
                lo = max(min(z for z, _ in pa), min(z for z, _ in pb))
                hi = min(max(z for z, _ in pa), max(z for z, _ in pb))
                if hi <= lo:
                    continue
                w = (hi - lo) / target_bins
                def _bins(pts):
                    b = collections.defaultdict(list)
                    for z, v in pts:
                        if lo <= z <= hi:
                            b[int((z - lo) // w)].append(v)
                    return {k: sum(v) / len(v) for k, v in b.items()}
                ba, bb = _bins(pa), _bins(pb)
                common = sorted(set(ba) & set(bb))
                dist = haversine_km(sa.latitude, sa.longitude,
                                    sb.latitude, sb.longitude)
                base = {'property_family': fname, 'a': sa.key, 'b': sb.key,
                        'entries': (ra.entry, rb.entry),
                        'z_overlap_m': round(hi - lo, 1),
                        'common_bins': len(common), 'target_bins': target_bins,
                        'distance_km': round(dist, 1),
                        'continuity_claim': (
                            'TWIN_HOLE_ELIGIBLE' if dist < CONTINUITY_KM else
                            'NONE - statistical comparison only (%.0f km '
                            'exceeds the %.0f km continuity threshold)'
                            % (dist, CONTINUITY_KM))}
                if len(common) < min_common:
                    base['status'] = 'REFUSED_THIN_DATA (min %d)' % min_common
                    refused.append(base)
                    continue
                xa = [ba[k] for k in common]
                xb = [bb[k] for k in common]
                ma, mb = statistics.mean(xa), statistics.mean(xb)
                den = math.sqrt(sum((x - ma) ** 2 for x in xa)
                                * (sum((y - mb) ** 2 for y in xb)))
                base['status'] = 'OK'
                base['pearson_r'] = ((sum((x - ma) * (y - mb)
                                          for x, y in zip(xa, xb)) / den)
                                     if den else float('nan'))
                ok.append(base)
    return {'ok': ok, 'refused': refused, 'n_ok': len(ok),
            'n_refused': len(refused),
            'finding': ('the library currently supports %d cross-site '
                        'depth-frame correlations; %d candidate pairs '
                        'refused thin - the honest census, not a failure'
                        % (len(ok), len(refused)))}
