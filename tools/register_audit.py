#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""List what remains of the source program's internal register in this tree.

    python tools/register_audit.py [--fail]

Counts, per file, the terms a client should not meet in a product: the source
program's brand and corpus vocabulary, paper citations, and doctrine words.
Client reports are gated at write time by gea.client_reports.forbidden_terms;
this audit covers everything else (source comments, docstrings, history). It
prints a table and, with --fail, exits non-zero when any term remains in the
package (docs/ are reported but never fail).
"""
import os, re, sys

TERMS = ['uqff', 'star-magic', 'star magic', 'primitive', 'lattice', 'PAPER_', 'Rule 7', 'Rule A', 'doctrine',
         'landmark', 'aether', 'DPM', 'refus', 'honest', 'canonical', 'grok_', 'Daniel']
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def scan(root):
    rows = []
    for dp, _, fns in os.walk(root):
        if '__pycache__' in dp or '/catalog' in dp:
            continue
        for fn in sorted(fns):
            if not fn.endswith(('.py', '.md', '.txt', '.json')):
                continue
            p = os.path.join(dp, fn)
            t = open(p, encoding='utf-8', errors='ignore').read()
            low = t.lower()
            counts = {term: (len(re.findall(re.escape(term), t)) if term[0].isupper() or term in ('PAPER_',) else low.count(term.lower())) for term in TERMS}
            counts = {k: v for k, v in counts.items() if v}
            if counts:
                rows.append((os.path.relpath(p, REPO), counts))
    return rows


def main():
    fail = '--fail' in sys.argv
    rows = scan(REPO)
    total = 0
    pkg_total = 0
    for path, counts in rows:
        n = sum(counts.values())
        total += n
        if path.startswith('gea/'):
            pkg_total += n
        print(f'{path:48s} {n:5d}  ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items(), key=lambda kv: -kv[1])))
    print(f'\n{len(rows)} files carry internal-register terms; {total} occurrences ({pkg_total} inside gea/).')
    return 1 if (fail and pkg_total) else 0


if __name__ == '__main__':
    sys.exit(main())
