# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""clock - one clock for every channel.

The box's system clock is disciplined by chrony from a GPS receiver's pulse-per-second line (the README has the
three lines of chrony.conf). Every sample is stamped from that clock, so the ground channels, the rig feed and any
other box on the pad agree to better than a millisecond - which is what the arithmetic downstream (GEA's bit
seismic) needs. This module asks chrony how well the clock is held and turns the answer into the timing quality
every miniSEED record carries (blockette 1001, 0 to 100), so a reader knows what each hour's time is worth:

    100  chrony locked to the PPS, offset under 100 microseconds
     90  chrony locked to the PPS, offset under 1 ms
     60  chrony locked to a network time source (no PPS), offset under 10 ms
     20  chrony running but not synchronised, or the offset larger
      0  no chrony: the box's own clock, free-running

Nothing here sets the clock; chrony does. The box records what it has and says what it was worth."""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from typing import Dict, Optional


def _chronyc_tracking() -> Optional[Dict[str, str]]:
    exe = shutil.which('chronyc')
    if not exe:
        return None
    try:
        out = subprocess.run([exe, 'tracking'], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    fields = {}
    for line in out.splitlines():
        k, _, v = line.partition(':')
        if _:
            fields[k.strip().lower()] = v.strip()
    return fields or None


def parse_tracking(fields: Optional[Dict[str, str]]) -> dict:
    """chronyc tracking's fields -> {source, locked, pps, offset_s, stratum, quality, detail}. Pure, so it is testable."""
    if not fields:
        return {'source': 'system', 'locked': False, 'pps': False, 'offset_s': None, 'stratum': None, 'quality': 0,
                'detail': 'chrony is not installed or not answering: the box\'s own clock, free-running'}
    ref = fields.get('reference id', '')
    stratum_txt = fields.get('stratum', '')
    try:
        stratum = int(stratum_txt)
    except ValueError:
        stratum = None
    leap = fields.get('leap status', '').lower()
    m = re.search(r'([-+]?\d+\.\d+)\s*seconds', fields.get('system time', ''))
    offset = abs(float(m.group(1))) if m else None
    pps = 'PPS' in ref.upper() or 'GPS' in ref.upper()
    synced = 'not synchronised' not in leap and 'not synchronized' not in leap and stratum is not None and stratum < 16
    if synced and pps and offset is not None and offset < 1e-4:
        q, d = 100, 'locked to the GPS pulse-per-second'
    elif synced and pps and offset is not None and offset < 1e-3:
        q, d = 90, 'locked to the GPS pulse-per-second, offset under a millisecond'
    elif synced and offset is not None and offset < 1e-2:
        q, d = 60, 'locked to a network time source, no PPS'
    elif synced:
        q, d = 20, 'synchronised, but the offset is large'
    else:
        q, d = 20, 'chrony is running but not synchronised'
    return {'source': 'chrony', 'locked': synced, 'pps': pps, 'offset_s': offset, 'stratum': stratum, 'quality': q,
            'detail': d, 'reference': ref}


def status(source: str = 'chrony') -> dict:
    if source == 'chrony':
        return parse_tracking(_chronyc_tracking())
    return {'source': 'system', 'locked': False, 'pps': False, 'offset_s': None, 'stratum': None, 'quality': 0,
            'detail': 'the box\'s own clock, as configured (clock.source = system)'}


def now() -> float:
    """Epoch seconds, UTC: the disciplined system clock."""
    return time.time()
