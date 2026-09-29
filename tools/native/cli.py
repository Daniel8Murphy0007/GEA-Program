"""gea.cli - the front door.

    gea quickstart                      first run: a catalogue well, its reports, the dashboard
    gea survey mywell.las               a LAS file in, one strata report out (--demo: bundled KTB excerpt)
    gea dashboard ...                   the report family + index.html (see `gea dashboard -h`)
    gea client-report ...  gea drift-monitor ...  gea well-test ...  gea alarms ...
    gea model-cards ...    gea store-forward ...  gea sla-report ...  gea fat-sat ...
    gea guide                           the click-by-click tester guide
    gea docs                            where the catalogue and the reports live on this machine
    gea gui                             the desktop window (desktop extra)
    gea accept                          the product gate

Every subcommand not listed here is passed to `python -m gea` unchanged, so the
full command set is one entry point. PATH-proof form: `python -m gea.cli ...`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FRONT = {'quickstart', 'survey', 'guide', 'docs', 'gui', 'help', '-h', '--help'}


def _pkg_root() -> Path:
    return Path(__file__).resolve().parent


def _repo_root() -> Path:
    return _pkg_root().parent


def cmd_survey(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog='gea survey', description='a LAS file in, one strata report out')
    ap.add_argument('file', nargs='?', default=None, help='LAS file (omit with --demo)')
    ap.add_argument('--demo', action='store_true', help='run the bundled public KTB excerpt')
    ap.add_argument('--family', default='continental_crystalline', help='prior family for the estimator')
    ap.add_argument('--lat', type=float, default=None)
    ap.add_argument('--elev', type=float, default=None)
    a = ap.parse_args(argv)
    from .survey_cmd import run_survey
    txt, _ = run_survey(path=a.file, demo=a.demo, family=a.family, lat=a.lat, elev=a.elev)
    print(txt)
    return 0


def cmd_guide(_argv) -> int:
    for cand in (_repo_root() / 'docs' / 'TESTER_GUIDE.md', _pkg_root() / 'TESTER_GUIDE.md'):
        if cand.exists():
            print(cand.read_text(encoding='utf-8'))
            return 0
    print('TESTER_GUIDE.md not found beside the package')
    return 2


def cmd_docs(_argv) -> int:
    from . import __version__
    cat = _pkg_root() / 'catalog'
    n = sum(1 for p in cat.iterdir() if p.suffix != '.json') if cat.exists() else 0
    print(f'gea build {__version__}')
    print(f'package:    {_pkg_root()}')
    print(f'catalogue:  {cat} ({n} entries, each with a .provenance.json)')
    print(f'reports:    written where you point --out (dashboard/, client_report/, model_cards/, ...)')
    print(f'records:    drift_monitor/ (evaluations.jsonl, change_log.jsonl), well_tests/ (approvals.jsonl), config_store/')
    print(f'guide:      gea guide')
    return 0


def cmd_quickstart(_argv) -> int:
    from . import __version__
    print(f'== gea quickstart (build {__version__}) ==')
    print('[1/3] a real catalogue well: Volve 15/9-F-12, 157 daily downhole-pressure samples')
    from .__main__ import main as sub
    out = os.path.abspath('gea_quickstart')
    rc = sub(['dashboard', '--catalog-well', 'volve_f12_f14_production_excerpt:15/9-F-12:10000',
              '--td', '10500', '--name', 'quickstart', '--out', out])
    if rc:
        return rc
    print('[2/3] the strata survey on the bundled KTB excerpt')
    rc = cmd_survey(['--demo'])
    if rc:
        return rc
    print('[3/3] done.')
    print(f'   open {os.path.join(out, "index.html")} in a browser - every tile links to its report.')
    print('   next: gea dashboard -h | gea client-report -h | gea guide | gea accept')
    return 0


def cmd_gui(_argv) -> int:
    try:
        from .shell import main as shell_main
    except ImportError as e:
        print(f'gea gui needs the desktop extra:  pip install "gea-program[desktop]"  ({e})')
        return 3
    return shell_main()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('help', '-h', '--help'):
        print(__doc__)
        if argv and argv[0] in ('-h', '--help'):
            return 0
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == 'quickstart':
        return cmd_quickstart(rest)
    if cmd == 'survey':
        return cmd_survey(rest)
    if cmd == 'guide':
        return cmd_guide(rest)
    if cmd == 'docs':
        return cmd_docs(rest)
    if cmd == 'gui':
        return cmd_gui(rest)
    from .__main__ import main as sub
    return sub(argv)


if __name__ == '__main__':
    raise SystemExit(main())
