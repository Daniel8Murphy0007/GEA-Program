# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""certificates - calibration certificates and datasheets, per instrument.

A measurement is only as good as the paper behind the gauge. This register
keeps, per well, one line per certificate: the instrument serial, the tag it
serves, the certificate id, the laboratory, issue and expiry dates, the
stated accuracy (percent of full scale) and full scale, the datasheet or
scan's file hash, who filed it. Append-only; a renewal is a new line.

`status(now)` answers, per tag: VALID / EXPIRING (inside the warning window)
/ EXPIRED / MISSING, with the days left. The dashboard shows the counts and
the well page lists the certificates; the drift report prints the stated
accuracy beside the measured bias so a reader sees whether a bias is inside
the instrument's own class. The swap register (`sensor_swap.py`) names the
certificate of the new instrument.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

STATUSES = ('VALID', 'EXPIRING', 'EXPIRED', 'MISSING')


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


class CertificateRegister:
    def __init__(self, path: str, warn_days: int = 60):
        self.path = path
        self.warn_days = warn_days

    def list(self) -> List[dict]:
        if not os.path.isfile(self.path):
            return []
        out = []
        with open(self.path, encoding='utf-8') as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def add(self, tag_id: str, serial: str, certificate_id: str, issued_utc: str, valid_until_utc: str, actor: str,
            lab: str = '', accuracy_pct_fs: Optional[float] = None, full_scale: Optional[float] = None, unit: str = '',
            file_path: Optional[str] = None, note: str = '') -> dict:
        if not (tag_id and serial and certificate_id):
            raise ValueError('a certificate needs the tag id, the instrument serial and the certificate id')
        issued, until = _parse(issued_utc), _parse(valid_until_utc)
        if until <= issued:
            raise ValueError('valid_until must be after the issue date')
        if accuracy_pct_fs is not None and not (0 < float(accuracy_pct_fs) <= 10):
            raise ValueError('accuracy must be a percentage of full scale between 0 and 10')
        e = {'certificate_id': certificate_id, 'tag_id': tag_id, 'serial': serial, 'lab': lab, 'issued_utc': _iso(issued),
             'valid_until_utc': _iso(until), 'accuracy_pct_fs': (float(accuracy_pct_fs) if accuracy_pct_fs is not None else None),
             'full_scale': (float(full_scale) if full_scale is not None else None), 'unit': unit,
             'file': os.path.basename(file_path) if file_path else '', 'file_sha256': _sha(file_path) if file_path and os.path.isfile(file_path) else '',
             'filed_by': actor, 'filed_utc': _iso(datetime.now(timezone.utc)), 'note': note}
        if any(x['certificate_id'] == certificate_id and x['serial'] == serial for x in self.list()):
            raise ValueError(f'certificate already filed: {certificate_id} for serial {serial}')
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(e, sort_keys=True) + '\n')
        return e

    def current_by_tag(self, serial_by_tag: Optional[Dict[str, str]] = None) -> Dict[str, dict]:
        """The latest-issued certificate per tag (for the tag's current serial when the swap register says which)."""
        out: Dict[str, dict] = {}
        for e in self.list():
            want = (serial_by_tag or {}).get(e['tag_id'])
            if want and e['serial'] != want:
                continue
            cur = out.get(e['tag_id'])
            if cur is None or e['issued_utc'] > cur['issued_utc']:
                out[e['tag_id']] = e
        return out

    def status(self, tags: List[str], now: Optional[datetime] = None, serial_by_tag: Optional[Dict[str, str]] = None) -> List[dict]:
        now = now or datetime.now(timezone.utc)
        cur = self.current_by_tag(serial_by_tag)
        rows = []
        for tag in tags:
            e = cur.get(tag)
            if e is None:
                rows.append({'tag_id': tag, 'status': 'MISSING', 'days_left': None, 'certificate_id': None, 'serial': (serial_by_tag or {}).get(tag),
                             'accuracy_pct_fs': None, 'valid_until_utc': None})
                continue
            days = (_parse(e['valid_until_utc']) - now).total_seconds() / 86400.0
            st = 'EXPIRED' if days < 0 else ('EXPIRING' if days <= self.warn_days else 'VALID')
            rows.append({'tag_id': tag, 'status': st, 'days_left': int(days), 'certificate_id': e['certificate_id'], 'serial': e['serial'],
                         'accuracy_pct_fs': e.get('accuracy_pct_fs'), 'full_scale': e.get('full_scale'), 'unit': e.get('unit'),
                         'valid_until_utc': e['valid_until_utc'], 'lab': e.get('lab', '')})
        return rows

    @staticmethod
    def summary(rows: List[dict]) -> dict:
        c = {s: 0 for s in STATUSES}
        for r in rows:
            c[r['status']] += 1
        return {'counts': c, 'n': len(rows), 'all_valid': c['EXPIRED'] == 0 and c['MISSING'] == 0}


def accuracy_band_psi(row: dict) -> Optional[float]:
    """The instrument's own accuracy as an absolute band (±), from percent of full scale."""
    if row.get('accuracy_pct_fs') is None or row.get('full_scale') is None:
        return None
    return float(row['accuracy_pct_fs']) / 100.0 * float(row['full_scale'])
