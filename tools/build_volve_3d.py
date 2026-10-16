# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""build_volve_3d - the shipped Volve excerpt the 3D tab opens on, rebuilt from its public sources.

    python tools/build_volve_3d.py --cache DIR        # fetch (or reuse DIR), verify, write gea/reference/volve_*

The Volve field data were released by Equinor ASA and the former Volve licence partners (ExxonMobil
Exploration and Production Norway AS, Bayerngas Norge AS) under Equinor's terms and conditions for the
licence to the Volve data. The files here are taken from public GitHub copies of that release, each at a
pinned commit, and hashed as fetched:

- the directional surveys as WITSML 1.4.1.1 trajectory objects, VERBATIM (gzip-compressed, byte for byte):
  15/9-F-4 (the definitive MWD trajectory, 87 stations to 3,510 m MD), 15/9-F-5 (the four section files,
  36 in to 8.5 in, Nov 2007 - Jul 2008), 15/9-F-10 (the 8.5 in section file, 479 stations to 5,300 m MD);
- the field's formation picks (Volve_well_picks_modified.csv: 408 picks on 34 wellbores with MD, TVD,
  TVDSS, two-way time, easting and northing), VERBATIM;
- five depth-converted interpreted horizons (Ty, Shetland Group, the base Cretaceous unconformity, Hugin top,
  Hugin base) as inline/crossline points every 12.5 m - SUBSAMPLED here to every fifth inline and crossline
  (62.5 m), no point moved, Z kept to the centimetre. The seabed file in the same copy carries two blocks,
  the second in two-way time, and is not used.

Nothing is interpolated, smoothed or re-gridded; the subsampling is the only change, and it is recorded.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / 'gea' / 'reference'
STEP = 4

SOURCES = {
    'traj_F-4': ('f0nzie/volve-drilling', '3698c43d4adc0ee11ffa8fd5f876fa961c9c846d',
                 'witsml/Norway-Statoil-NO 15_$47$_9-F-4/1/trajectory/1.xml'),
    'traj_F-5_36in': ('maribickpostanes/Interactive-Well-Trajectory-Plot', '6231dc346f6a6f38be21404005d930d7d3abbc89', '4.xml'),
    'traj_F-5_17.5in': ('maribickpostanes/Interactive-Well-Trajectory-Plot', '6231dc346f6a6f38be21404005d930d7d3abbc89', '1.xml'),
    'traj_F-5_12.25in': ('maribickpostanes/Interactive-Well-Trajectory-Plot', '6231dc346f6a6f38be21404005d930d7d3abbc89', '2.xml'),
    'traj_F-5_8.5in': ('maribickpostanes/Interactive-Well-Trajectory-Plot', '6231dc346f6a6f38be21404005d930d7d3abbc89', '3.xml'),
    'traj_F-10': ('viandika/volve_horizon_trajectory', '3f79910a685257159662426235934b089d61df05',
                  'witsml/Norway-StatoilHydro-15_$47$_9-F-10/trajectory/1.xml'),
    'well_picks': ('yohanesnuwara/MLGeo', '3b089e0e50fc69207b84616c6cb364b5c5ca2960', 'well_logs/Volve_well_picks_modified.csv'),
    'hz_Ty': ('viandika/volve_horizon_trajectory', '3f79910a685257159662426235934b089d61df05', 'horizon/Ty.csv'),
    'hz_SHETLAND': ('viandika/volve_horizon_trajectory', '3f79910a685257159662426235934b089d61df05', 'horizon/SHETLAND.csv'),
    'hz_BCU': ('viandika/volve_horizon_trajectory', '3f79910a685257159662426235934b089d61df05', 'horizon/BCU.csv'),
    'hz_Hugin_Fm_Top': ('viandika/volve_horizon_trajectory', '3f79910a685257159662426235934b089d61df05', 'horizon/Hugin_Fm_Top.csv'),
    'hz_Hugin_Base': ('viandika/volve_horizon_trajectory', '3f79910a685257159662426235934b089d61df05', 'horizon/Hugin_Base.csv'),
}
# horizon file -> (display name, the pick it is the surface of, the order from shallow to deep)
SURFACES = [('hz_Ty', 'Ty Fm. top', 'Ty Fm. Top'), ('hz_SHETLAND', 'Shetland Gp. top', 'SHETLAND GP. Top'),
            ('hz_BCU', 'Base Cretaceous unconformity (Draupne Fm. top)', 'Draupne Fm. Top'),
            ('hz_Hugin_Fm_Top', 'Hugin Fm. top', 'Hugin Fm. VOLVE Top'), ('hz_Hugin_Base', 'Hugin Fm. base', 'Hugin Fm. VOLVE Base')]


def url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{sha}/" + urllib.request.quote(path)


def fetch(key: str, cache: Path) -> bytes:
    repo, sha, path = SOURCES[key]
    p = cache / f'{key}.src'
    if not p.exists():
        with urllib.request.urlopen(url(repo, sha, path), timeout=300) as r:
            p.write_bytes(r.read())
    return p.read_bytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    a = ap.parse_args()
    cache = Path(a.cache); cache.mkdir(parents=True, exist_ok=True)
    prov = {'entry': 'volve_3d', 'kind': 'REAL_FIELD_EXCERPT_FOR_THE_3D_VIEW',
            'field': 'Volve, Norwegian North Sea (blocks 15/9), operated by Statoil/Equinor, decommissioned 2016',
            'publisher': 'Equinor ASA, with the former Volve licence partners ExxonMobil Exploration and Production Norway AS and Bayerngas Norge AS',
            'license': "Equinor's 'Terms and conditions for licence to data - Volve' (2020): use, copy and adapt for non-commercial and commercial "
                       "purposes; Equinor and the former partners attributed and a notice referring to the terms included when shared; the licensed "
                       "material may not be sold; no use that suggests endorsement. (The 2018 release carried CC BY-NC-SA 4.0; Equinor replaced it.)",
            'license_url': 'https://cdn.equinor.com/files/h61q9gi9/global/de6532f6134b9a953f6c41bac47a0c055a3712d3.pdf?equinor-hrs-terms-and-conditions-for-licence-to-data-volve.pdf=',
            'attribution': 'Contains data from the Volve field released by Equinor ASA and the former Volve licence partners ExxonMobil Exploration and '
                           'Production Norway AS and Bayerngas Norge AS.',
            'access': "The release itself (Equinor's data village) was not reachable from the build machine; the files were taken from public "
                      "GitHub copies, each at a pinned commit, hashed as fetched. Byte identity with the original release is not claimed; the checks "
                      "below are what tie the copies to the field and to each other.",
            'crs': 'ED50 / UTM zone 31N (International 1924 ellipsoid), metres - as delivered; depths below mean sea level, positive down',
            'checks': [
                "the slot eastings and northings of 15/9-F-1 C and 15/9-F-11 B, unprojected on ED50 zone 31N, land 0.02 m and 0.09 m from the "
                "latitudes and longitudes in those wells' own LAS headers (and about 148 m away if read as WGS84) - the frame is ED50 zone 31N",
                "the minimum-curvature path computed from each trajectory file's measured depths, inclinations and azimuths reproduces the TVD and "
                "displacements written in the file to under a centimetre (15/9-F-4, -F-5, -F-10)",
                "the paths land on Equinor's own formation picks, which carry eastings, northings and depths, to under 0.3 m",
                "the horizons are in depth below sea level (they tie to the picks' TVDSS, not to their two-way times): median miss 2 to 6 m by "
                "surface at the 23-28 wells picked for each, larger at a few wells the interpretation did not honour (listed per surface in the "
                "scene)",
            ],
            'findings': [
                "15/9-F-4's definitive survey (Baker Hughes, gyro, 2016) says true north; Equinor's picks on F-4 agree with it to 0.1 m read as grid "
                "azimuths and miss by up to 14 m with the 0.95 degree convergence applied. The scene reads it as grid and says so.",
                "15/9-F-4's 2016 survey stamps every station with one time (a resurvey): it is not a record of drilling and is not replayed.",
                "The seabed horizon in the same copy is two blocks, the second in two-way time; it is not used.",
                "No copy of the real-time drilling logs from the wells' drilling (2007-2009) was found under an open licence; the 2016 logs that are "
                "copied are plug-and-abandonment work with the string near surface. The Volve replay therefore follows the survey stations' own times.",
            ],
            'sources': {}, 'outputs': {}}
    for key in SOURCES:
        b = fetch(key, cache)
        repo, sha, path = SOURCES[key]
        prov['sources'][key] = {'repository': f'https://github.com/{repo}', 'commit': sha, 'path': path, 'url': url(repo, sha, path),
                                'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
    outs = {}
    for key in [k for k in SOURCES if k.startswith('traj_')]:
        name = f'volve_{key}.xml.gz'
        raw = fetch(key, cache)
        data = gzip.compress(raw, compresslevel=9, mtime=0)
        (REF / name).write_bytes(data)
        outs[name] = {'from': key, 'how': 'verbatim, gzip-compressed', 'sha256_uncompressed': hashlib.sha256(raw).hexdigest()}
    picks = fetch('well_picks', cache)
    (REF / 'volve_well_picks.csv').write_bytes(picks)
    outs['volve_well_picks.csv'] = {'from': 'well_picks', 'how': 'verbatim'}
    surf = []
    for key, name, pick in SURFACES:
        rows = list(csv.reader(io.StringIO(fetch(key, cache).decode('utf-8'))))
        hdr, body = rows[0], rows[1:]
        assert hdr == ['IL', 'XL', 'X', 'Y', 'Z'], hdr
        n_in = 0
        il, xl, x, y, z = [], [], [], [], []
        for r in body:
            try:
                fi, fj = float(r[0]), float(r[1])
            except (ValueError, IndexError):
                continue                                    # a block header line in the delivered file, not a point
            if fi != int(fi) or fj != int(fj):
                raise ValueError(f'{key}: a non-integer inline/crossline {r[:2]}')
            n_in += 1
            i, j = int(fi), int(fj)
            if i % STEP or j % STEP:
                continue
            il.append(i); xl.append(j); x.append(round(float(r[2]), 2)); y.append(round(float(r[3]), 2)); z.append(round(float(r[4]), 2))
        surf.append({'id': key[3:], 'name': name, 'pick': pick, 'n_source_points': n_in, 'n_points': len(il), 'step': STEP,
                     'il': il, 'xl': xl, 'x': x, 'y': y, 'z': z})
    blob = json.dumps({'schema': 1, 'z': 'depth below mean sea level, metres, positive down (the surfaces as delivered)',
                       'subsample': f'every {STEP}th inline and crossline of the 12.5 m bin grid (62.5 m); no point moved, X and Y to the '
                                    'centimetre, Z to the centimetre', 'surfaces': surf}, separators=(',', ':')).encode('utf-8')
    (REF / 'volve_surfaces.json.gz').write_bytes(gzip.compress(blob, compresslevel=9, mtime=0))
    outs['volve_surfaces.json.gz'] = {'from': [k for k, _, _ in SURFACES], 'how': f'subsampled to every {STEP}th inline and crossline',
                                      'points': {s['id']: [s['n_source_points'], s['n_points']] for s in surf}}
    for k, v in outs.items():
        v['sha256'] = hashlib.sha256((REF / k).read_bytes()).hexdigest()
    prov['outputs'] = outs
    pp = REF / 'volve_3d.provenance.json'
    pp.write_text(json.dumps(prov, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({k: v['sha256'][:12] for k, v in outs.items()}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
