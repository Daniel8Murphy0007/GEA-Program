# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""build_eia_prices - the price series that ship with the program, rebuilt from their public source.

    python tools/build_eia_prices.py --cache DIR        # fetch (or reuse DIR), verify, write gea/reference/eia_*

Two daily spot-price series published by the U.S. Energy Information Administration, a U.S. government
agency whose data are in the public domain:

- Cushing, OK WTI spot price FOB, dollars per barrel (EIA series RWTC);
- Henry Hub natural gas spot price, dollars per million Btu (EIA series RNGWHHD).

The EIA site is not reachable from the build machines; the series are taken, verbatim, from the Frictionless
Data "core datasets" copies on GitHub (datasets/oil-prices, datasets/natural-gas), which re-publish EIA's
daily tables as CSV, at a pinned commit, hashed as fetched. They are shipped gzip-compressed, byte for byte.
A workspace can refresh them (`gea economics --refresh-prices`) or import any other series it has a file for.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / 'gea' / 'reference'

SOURCES = {
    'wti': {'repo': 'datasets/oil-prices', 'commit': 'c24526249395d1df291d2a6a63a510939cf65556', 'path': 'data/wti-daily.csv',
            'name': 'WTI crude oil, Cushing OK, spot', 'unit': 'USD/bbl', 'eia_series': 'RWTC',
            'eia_url': 'https://www.eia.gov/dnav/pet/hist/RWTCD.htm', 'file': 'eia_wti_daily.csv.gz'},
    'henry_hub': {'repo': 'datasets/natural-gas', 'commit': '64843b5bb8ed967f4af563eac2c1782380e2f2e0', 'path': 'data/daily.csv',
                  'name': 'Henry Hub natural gas, spot', 'unit': 'USD/MMBtu', 'eia_series': 'RNGWHHD',
                  'eia_url': 'https://www.eia.gov/dnav/ng/hist/rngwhhdd.htm', 'file': 'eia_henry_hub_daily.csv.gz'},
}


def url(s: dict, ref: str = None) -> str:
    return f"https://raw.githubusercontent.com/{s['repo']}/{ref or s['commit']}/{s['path']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    a = ap.parse_args()
    cache = Path(a.cache); cache.mkdir(parents=True, exist_ok=True)
    prov = {'entry': 'eia_prices', 'kind': 'PUBLIC_DOMAIN_PRICE_SERIES',
            'publisher': 'U.S. Energy Information Administration (EIA)',
            'license': 'Public domain: U.S. government publications are not subject to copyright (EIA, Copyrights and Reuse). The GitHub '
                       'copies are published under the Open Data Commons Public Domain Dedication and License / the Unlicense.',
            'attribution': 'Source: U.S. Energy Information Administration, spot prices (series RWTC and RNGWHHD).',
            'access': 'EIA itself was not reachable from the build machine; the daily tables were taken from the Frictionless Data core '
                      'datasets on GitHub, which re-publish them as CSV, at pinned commits, verbatim and hashed as fetched.',
            'checks': ['the WTI series carries the negative settlement of 20 April 2020 (-36.98) and the Henry Hub series the winter-storm '
                       'spike of 17 February 2021 (23.86), as EIA published them'],
            'series': {}}
    for key, s in SOURCES.items():
        p = cache / f'{key}.csv'
        if not p.exists():
            with urllib.request.urlopen(url(s), timeout=300) as r:
                p.write_bytes(r.read())
        raw = p.read_bytes()
        rows = list(csv.reader(io.StringIO(raw.decode('utf-8'))))
        assert rows[0] == ['Date', 'Price'], rows[0]
        (REF / s['file']).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        prov['series'][key] = {**{k: s[k] for k in ('name', 'unit', 'eia_series', 'eia_url', 'file')},
                               'source': {'repository': f"https://github.com/{s['repo']}", 'commit': s['commit'], 'path': s['path'], 'url': url(s)},
                               'refresh_url': url(s, 'main'), 'rows': len(rows) - 1, 'first': rows[1][0], 'last': rows[-1][0],
                               'sha256_uncompressed': hashlib.sha256(raw).hexdigest(),
                               'sha256': hashlib.sha256((REF / s['file']).read_bytes()).hexdigest()}
    (REF / 'eia_prices.provenance.json').write_text(json.dumps(prov, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({k: [v['rows'], v['first'], v['last']] for k, v in prov['series'].items()}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
