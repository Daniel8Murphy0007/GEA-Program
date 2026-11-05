# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""build_driller - GEA Driller for the rig PC: dist/GEA-Driller-<version>.tar.gz, the folder a person unpacks on the
rig PC and installs with `sh install.sh` (Linux) or `install.cmd` (Windows): the wheel with the trained policy inside
it, the source, the README, the example config, the install scripts, SHA256SUMS.txt and a MANIFEST.json. The
driller's self-test runs on the built wheel before the archive is made. Stdlib only; `pip wheel` for the wheel."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'driller'


def version() -> str:
    for line in (SRC / 'gea_driller' / '__init__.py').read_text(encoding='utf-8').splitlines():
        if line.startswith('__version__'):
            return line.split('"')[1]
    raise SystemExit('no __version__ in recorder/gea_driller/__init__.py')


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def build(out: Path, no_selftest: bool = False) -> Path:
    ver = version()
    kit = out / f'gea-driller-{ver}'
    if kit.exists():
        shutil.rmtree(kit)
    kit.mkdir(parents=True)
    print(f'== GEA Driller {ver}')
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', str(SRC), '--no-deps', '-w', str(kit), '-q'], check=True)
    shutil.copytree(SRC / 'gea_driller', kit / 'gea_driller', ignore=shutil.ignore_patterns('__pycache__'))
    for n in ('pyproject.toml', 'README.md', 'install.sh', 'install.cmd'):
        shutil.copyfile(SRC / n, kit / n)
    sys.path.insert(0, str(SRC))
    from gea_driller import config as C          # noqa: E402
    C.write_example(str(kit / 'config.example.json'))
    if not no_selftest:
        r = subprocess.run([sys.executable, '-m', 'gea_driller', 'selftest', '--seconds', '8', '--json'], cwd=str(kit), capture_output=True, text=True)
        res = json.loads(r.stdout or '{}') if r.returncode in (0, 1) else {}
        if r.returncode != 0 or not res.get('ok'):
            print(r.stdout); print(r.stderr); raise SystemExit('the driller self-test failed on the built kit')
        print(f"== self-test OK: {', '.join(c['check'] for c in res['checks'])}")
    for pc in kit.rglob('__pycache__'):
        shutil.rmtree(pc, ignore_errors=True)
    files = sorted(p for p in kit.rglob('*') if p.is_file())
    (kit / 'SHA256SUMS.txt').write_text(''.join(f'{sha256(p)}  {p.relative_to(kit).as_posix()}\n' for p in files), encoding='utf-8')
    (kit / 'MANIFEST.json').write_text(json.dumps({'name': 'gea-driller', 'version': ver, 'built_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                                   'n_files': len(files), 'bytes': sum(p.stat().st_size for p in files)}, indent=1), encoding='utf-8')
    tgz = out / f'GEA-Driller-{ver}.tar.gz'
    with tarfile.open(tgz, 'w:gz') as tf:
        tf.add(kit, arcname=kit.name)
    print(f'== {tgz} ({tgz.stat().st_size / 1e6:.2f} MB)')
    return tgz


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='build GEA Driller, the agent on the rig PC')
    ap.add_argument('--out', default=str(ROOT / 'dist'))
    ap.add_argument('--no-selftest', action='store_true')
    a = ap.parse_args(argv)
    build(Path(a.out), a.no_selftest)
    return 0


if __name__ == '__main__':
    sys.exit(main())
