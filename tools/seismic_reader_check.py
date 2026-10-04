# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""seismic_reader_check - the miniSEED reader against libmseed on a corpus of real-station files.

    python tools/seismic_reader_check.py            # needs internet and pip; writes nothing into the repository
    python tools/seismic_reader_check.py --keep DIR # keep the downloaded corpus

What it does: downloads (with pip, no install) the obspy wheel, whose test data
holds about ninety miniSEED files from real stations and odd recorders - every
encoding, both byte orders, 512- to 4096-byte records, gaps, time corrections,
blockettes of every kind, broken and truncated records - and pymseed, the
Python binding of libmseed (the format's reference implementation). Each file
is decoded by both; the verdict per file is EXACT (every channel sample for
sample), SKIPPED-BY-DESIGN (text/log records this reader does not call a time
series), REFERENCE-REFUSES (libmseed rejects the file; this reader's result is
reported but unverified), or MISMATCH. The corpus is not redistributed with
this program; the check is re-run, not re-shipped.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', default=None, help='directory to keep the corpus in (default: a temporary one)')
    ap.add_argument('--corpus', default=None, help='an already extracted corpus directory (skips the download)')
    a = ap.parse_args(argv)
    work = a.corpus or a.keep or tempfile.mkdtemp(prefix='gea_reader_check_')
    os.makedirs(work, exist_ok=True)
    data = os.path.join(work, 'data')
    if not a.corpus:
        subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps', '-q', '-d', work, 'obspy'], check=True)
        whl = glob.glob(os.path.join(work, 'obspy-*.whl'))[0]
        z = zipfile.ZipFile(whl)
        os.makedirs(data, exist_ok=True)
        n = 0
        for name in z.namelist():
            if '/tests/data/' in name and not name.endswith('/') and (name.startswith('obspy/io/mseed/tests/data/') or name.endswith(('.mseed', '.seed', '.ms'))):
                with open(os.path.join(data, name.replace('obspy/', '').replace('/', '__')), 'wb') as f:
                    f.write(z.read(name))
                n += 1
        print(f'== corpus: {n} files from {os.path.basename(whl)}')
    try:
        import pymseed  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--user', 'pymseed'], check=True)
    import numpy as np
    from pymseed import MS3TraceList
    from gea import seismic as S

    def sid_of(t):
        ch = ('   ' + t.channel)[-3:]              # a two-letter channel code is read as libmseed reads it: band empty
        return f"FDSN:{t.network}_{t.station}_{t.location}_{ch[0].strip()}_{ch[1].strip()}_{ch[2].strip()}"
    rows = []
    for f in sorted(glob.glob(os.path.join(data, '*'))):
        name = os.path.basename(f)
        ref, ref_err = {}, None
        try:
            for tid in MS3TraceList.from_file(f, unpack_data=True):
                for seg in tid:
                    ref.setdefault(tid.sourceid, []).append((seg.starttime_seconds, np.asarray(seg.np_datasamples)))
        except Exception as e:
            ref_err = f'{type(e).__name__}: {str(e)[:90]}'
        mine, my_err, skipped = {}, None, []
        try:
            trs = S.read_mseed(f)
            skipped = list(getattr(trs, 'skipped', []))
            for t in trs:
                mine.setdefault(sid_of(t), []).append((t.starttime, t.data))
        except Exception as e:
            my_err = f'{type(e).__name__}: {str(e)[:90]}'
        if my_err:
            rows.append(('MISMATCH', name, f'this reader raised: {my_err}'))
            continue
        if ref_err:
            rows.append(('REFERENCE-REFUSES', name, f'libmseed: {ref_err}; this reader: {sum(len(v) for v in mine.values())} trace(s), {len(skipped)} record(s) skipped'))
            continue
        notes = []
        for sid in sorted(set(ref) | set(mine)):
            ra = np.concatenate([d for _, d in sorted(ref.get(sid, []), key=lambda x: x[0])]) if sid in ref else np.zeros(0)
            ma = np.concatenate([d for _, d in sorted(mine.get(sid, []), key=lambda x: x[0])]) if sid in mine else np.zeros(0)
            if len(ra) != len(ma) or (len(ra) and not np.array_equal(ra.astype(float), ma.astype(float))):
                notes.append(f'{sid}: libmseed {len(ra)} samples, this reader {len(ma)}')
        if not notes:
            rows.append(('EXACT', name, f'{sum(len(v) for v in ref.values())} segment(s)'))
        elif skipped and all('text record' in s.get('reason', '') for s in skipped) and all(sid not in mine for sid in ref):
            rows.append(('SKIPPED-BY-DESIGN', name, 'text/log records: libmseed returns the bytes as samples, this reader lists them as skipped'))
        else:
            rows.append(('MISMATCH', name, '; '.join(notes)[:200]))
    for r in rows:
        print(f'{r[0]:18s} {r[1][:62]:62s} {r[2]}')
    c = Counter(r[0] for r in rows)
    print('==', dict(c))
    return 1 if c.get('MISMATCH') else 0


if __name__ == '__main__':
    sys.exit(main())
