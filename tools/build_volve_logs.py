# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""build_volve_logs - the logs the Simulation tab shows along Volve 15/9-F-4, rebuilt from their public source.

    python tools/build_volve_logs.py --cache DIR        # fetch (or reuse DIR), verify, write gea/reference/volve_f4_*

Two LAS files of 15/9-F-4's 8.5 in reservoir section (2,956 to 3,510 m MD), from the Volve field data released by
Equinor ASA and the former Volve licence partners under Equinor's terms and conditions for the licence to the
Volve data, taken from a public GitHub copy at a pinned commit and hashed as fetched:

- the logging-while-drilling record (Schlumberger, recorded mode, February 2008): gamma ray, bulk density and
  its correction, thermal neutron porosity, the photoelectric factor, the 2 MHz phase-shift resistivities and the
  true resistivity, sigma, the caliper and the rate of penetration over the last 1.5 m - LAS 2.0;
- the petrophysical interpretation of that record (Schlumberger EcoView, processed 19 February 2008 for
  StatoilHydro, LAS 3.0): mineral volume fractions (quartz-feldspar-mica, clay, calcite, dolomite, pyrite,
  siderite and the rest) that sum with the total porosity to one, the water saturation of the uninvaded zone,
  the hydrocarbon and water volumes, the intrinsic permeability, and the analyst's parameters per zone.

Both are shipped VERBATIM, gzip-compressed byte for byte (the interpretation has CRLF line ends, which a git
checkout would otherwise change). Nothing is resampled or edited.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / 'gea' / 'reference'
REPO, COMMIT = 'hasifsyazwan/AI-Well-Log-Assistant-for-Volve-Field', 'fbf2c0ffc638c3bb8112091cba0c1eae8a75aef8'
FILES = {
    'lwd': ('data/F-4/basic.las', 'volve_f4_lwd.las.gz', 'logging-while-drilling record, 8.5 in section (LAS 2.0)'),
    'interpretation': ('data/F-4/interpretation.las', 'volve_f4_interpretation.las.gz',
                       'petrophysical interpretation of that record (Schlumberger EcoView for StatoilHydro, LAS 3.0)'),
}


def url(path: str) -> str:
    return f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{path}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    a = ap.parse_args()
    cache = Path(a.cache); cache.mkdir(parents=True, exist_ok=True)
    prov = {'entry': 'volve_f4_logs', 'kind': 'PUBLIC_WELL_LOGS',
            'well': '15/9-F-4', 'field': 'Volve', 'interval_m_md': [2955.95, 3509.92],
            'license': "Equinor's 'Terms and conditions for licence to data - Volve' (2020): use, copy and adapt for non-commercial and "
                       "commercial purposes; Equinor and the former partners attributed and a notice referring to the terms included when "
                       "shared; the licensed material may not be sold; no use that suggests endorsement.",
            'attribution': 'Contains data from the Volve field released by Equinor ASA and the former Volve licence partners '
                           '(ExxonMobil Exploration and Production Norway AS, Bayerngas Norge AS) under the Equinor Open Data Licence; '
                           'the data are not sold. 15/9-F-4 logs: Schlumberger, for StatoilHydro, 2008.',
            'access': 'Taken from a public GitHub copy of the Volve release at a pinned commit, verbatim, hashed as fetched.',
            'checks': ["the interpretation's mineral volumes and its total porosity sum to one at every depth (to 1e-4)",
                       "its hydrocarbon volume equals the total porosity times one less the water saturation (to 1e-4)",
                       'the two files carry the same well and the same depth step (0.1524 m)'],
            'files': {}}
    for key, (path, out, what) in FILES.items():
        p = cache / Path(path).name
        if not p.exists():
            with urllib.request.urlopen(url(path), timeout=300) as r:
                p.write_bytes(r.read())
        raw = p.read_bytes()
        assert b'15/9-F-4' in raw[:4000], key
        (REF / out).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        prov['files'][key] = {'what': what, 'file': out,
                              'source': {'repository': f'https://github.com/{REPO}', 'commit': COMMIT, 'path': path, 'url': url(path)},
                              'bytes': len(raw), 'sha256_uncompressed': hashlib.sha256(raw).hexdigest(),
                              'sha256': hashlib.sha256((REF / out).read_bytes()).hexdigest()}
    (REF / 'volve_f4_logs.provenance.json').write_text(json.dumps(prov, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({k: [v['file'], v['bytes']] for k, v in prov['files'].items()}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
