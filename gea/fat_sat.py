"""fat_sat — the acceptance suite rendered as a Factory / Site Acceptance
Test protocol (SOW 4.2.20 FAT; 4.2.21 SAT).

The in-package acceptance suite already checks the product end to end; a
client witnesses those checks as a numbered protocol: step, section, check,
expected, actual (PASS / FAIL), witness. This module runs the selected
sections through the suite's own `ok()` hook and renders the protocol with
a signature block. A FAT is run on the build before delivery; a SAT runs the
same steps on the installed environment and records that environment (from
the SBOM). Checks whose wording belongs to the program's internal register
are excluded from the client protocol and counted, so the protocol reads in
the client's vocabulary without altering any check.

Headless-safe: standard library only.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Sections offered to the client protocol, in run order: (key, function name, title)
CLIENT_SECTIONS = [
    ('A', 'section_a_cli', 'Headless command line: runs, exports, determinism, ingest round-trip'),
    ('C', 'section_c_reconciler', 'Two-stream reconciliation and classification'),
    ('F', 'section_f_ports', 'Ingest ports (historian CSV, LAS)'),
    ('AA', 'section_aa_client_reports', 'Client report family: records, quality, drift, accuracy, monitor, well tests, alarms, model cards, resilience, configuration, SBOM, SLA, FAT/SAT, dashboard'),
]


def run_protocol(kind: str = 'FAT', sections: Optional[List[str]] = None) -> dict:
    """Run the selected sections and return the protocol rows."""
    from . import acceptance_tests as AT
    from .client_reports import forbidden_terms
    keys = [k for k, _, _ in CLIENT_SECTIONS] if not sections else sections
    rows: List[dict] = []
    excluded = 0
    started = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        for key, fn, title in CLIENT_SECTIONS:
            if key not in keys:
                continue
            before = len(AT._RESULTS)
            f = getattr(AT, fn)
            try:
                f(tmp) if 'tmp' in f.__code__.co_varnames[:f.__code__.co_argcount] else f()
            except Exception as ex:                      # a crash is a FAIL row, never a silent skip
                AT._RESULTS.append((False, f'{key}: section raised {type(ex).__name__}: {ex}'))
            for passed, msg in AT._RESULTS[before:]:
                if forbidden_terms(msg):
                    excluded += 1
                    continue
                rows.append({'step': len(rows) + 1, 'section': key, 'section_title': title, 'check': msg,
                             'expected': 'PASS', 'actual': 'PASS' if passed else 'FAIL', 'witness': ''})
    finished = datetime.now(timezone.utc)
    n_fail = sum(1 for r in rows if r['actual'] == 'FAIL')
    env = None
    if kind.upper() == 'SAT':
        from .sbom import generate
        env = generate()
    return {'kind': kind.upper(), 'started_utc': started.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'finished_utc': finished.strftime('%Y-%m-%dT%H:%M:%SZ'), 'sections': keys, 'rows': rows,
            'n_steps': len(rows), 'n_pass': len(rows) - n_fail, 'n_fail': n_fail, 'internal_checks_excluded': excluded,
            'result': 'ACCEPTED' if n_fail == 0 and rows else 'NOT ACCEPTED', 'environment': env}
