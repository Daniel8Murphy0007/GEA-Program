#!/usr/bin/env python3
"""Write each catalogue entry's position into its provenance sidecar, from the entry's own file (gea/catalog_map.py says how).
Run once per leg after a catalogue change; the gate holds the sidecars equal to a fresh recompute. Prints what changed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gea import catalog_map as CM  # noqa: E402

if __name__ == '__main__':
    written = CM.write_sidecars()
    chk = CM.check_sidecars()
    print(CM.text().splitlines()[0])
    print(f'sidecars written: {len(written)}' + (': ' + ', '.join(written) if written else ' (all already current)'))
    print('check:', 'OK' if chk['ok'] else chk['bad'])
    sys.exit(0 if chk['ok'] else 1)
