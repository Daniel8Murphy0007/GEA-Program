# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The product gate: the in-package acceptance suite must pass end to end."""
import subprocess, sys


def test_acceptance_suite():
    r = subprocess.run([sys.executable, "-m", "gea", "accept"], capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "[ACCEPTANCE] OK" in r.stdout


def test_self_contained():
    """Every module under gea/ imports only the standard library, numpy, the declared
    optional extras and gea itself; no tracked file names another program."""
    import subprocess, sys, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, os.path.join(root, "tools", "standalone_check.py")], capture_output=True, text=True, cwd=root)
    assert r.returncode == 0, r.stdout[-3000:]
