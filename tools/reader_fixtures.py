# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""reader_fixtures - the dossiers GEA Reader is checked on, with what verify_dossier.py says of each.

Writes into OUT: a synthetic workspace; good.zip (a well's dossier); rotated.zip (the pad's, signed after a key
hand-over, sources embedded); t_file / t_man / t_extra / t_drop (a report changed, the manifest changed, an unsigned
file added, a report removed); stored.zip (the good one without compression); dupe.zip (a second copy of a report
under the same name, the first copy changed: a zip reader that shows the first copy would show a file that was not checked);
notzip.zip; GEA-Reader.html made from the workspace; and expect.json. Then `node tools/reader_check.mjs OUT`.

Usage:  python tools/reader_fixtures.py OUT
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def rewrite(src, dst, change):
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zo:
        for n in zi.namelist():
            b2 = change(n, zi.read(n))
            if b2 is not None:
                zo.writestr(n, b2)


def main(argv=None) -> int:
    import warnings
    from gea import __version__, drill_string as DS, well_report as WR, dossier as DZ, dossier_verify as DV, reader as RD
    from gea.workspace import Workspace
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 2
    d = Path(argv[0])
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    syn = DS.synthetic_motor_record(str(d))
    ws = Workspace.create(str(d / 'ws'), 'Pad Ü SYNTHETIC', actor='tester')
    w = ws.add_well_file(syn['path'], display='SYNTHETIC well', actor='tester')
    ws.refresh_dashboard(actor='tester')
    WR.write_well(ws, w['id'], __version__, actor='tester')
    WR.write_pad(ws, __version__, actor='tester')
    e1 = DZ.build(ws, 'tester', __version__, w['id'])
    shutil.copy(Path(ws.reports_dir, e1['path']), d / 'good.zip')
    fp1 = DZ.key_info(ws)['fingerprint']
    DZ.rotate_key(ws, 'tester', 'a scheduled change - ä, "quoted"')
    e2 = DZ.build(ws, 'tester', __version__, None, include_sources=True)
    shutil.copy(Path(ws.reports_dir, e2['path']), d / 'rotated.zip')
    good = d / 'good.zip'
    with zipfile.ZipFile(good) as z:
        man = json.loads(z.read('manifest.json'))
    rep = next(f['path'] for f in man['files'] if f['path'].endswith('well_report.html'))
    rewrite(good, d / 't_file.zip', lambda n, b: b.replace(b'SYNTHETIC', b'SYNTHETIX', 1) if n == rep else b)
    rewrite(good, d / 't_man.zip', lambda n, b: b.replace(b'"created_by":"tester"', b'"created_by":"someone"') if n == 'manifest.json' else b)
    rewrite(good, d / 't_extra.zip', lambda n, b: b)
    with zipfile.ZipFile(d / 't_extra.zip', 'a') as za:
        za.writestr('reports/extra.html', '<p>added</p>')
    rewrite(good, d / 't_drop.zip', lambda n, b: None if n == rep else b)
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(d / 'stored.zip', 'w', zipfile.ZIP_STORED) as zo:
        for n in zi.namelist():
            zo.writestr(n, zi.read(n))
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(d / 'dupe.zip', 'w', zipfile.ZIP_DEFLATED) as zo, warnings.catch_warnings():
        warnings.simplefilter('ignore')                                   # zipfile warns of the duplicate name: that is the point
        for n in zi.namelist():
            if n == rep:
                zo.writestr(n, zi.read(n).replace(b'SYNTHETIC', b'SOMETHING ELSE', 1))
            zo.writestr(n, zi.read(n))
    (d / 'notzip.zip').write_bytes(b'this is not a zip file')
    fixtures = {}
    for name in ('good.zip', 'rotated.zip', 't_file.zip', 't_man.zip', 't_extra.zip', 't_drop.zip', 'stored.zip', 'dupe.zip', 'notzip.zip'):
        r = DV.check(str(d / name))
        fixtures[name] = {'status': r['status'], 'checks': [{'check': c['check'], 'ok': c['ok']} for c in r['checks']],
                          'fingerprint': r.get('fingerprint'), 'key_chain': r.get('key_chain') or []}
    RD.write(str(d / 'GEA-Reader.html'), ws)
    with open(d / 'expect.json', 'w', encoding='utf-8') as f:
        json.dump({'fp1': fp1, 'fp2': DZ.key_info(ws)['fingerprint'], 'fixtures': fixtures}, f, indent=1)
    print(f"reader fixtures: {len(fixtures)} dossiers in {d}: " + ', '.join(f"{k} {v['status']}" for k, v in fixtures.items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
