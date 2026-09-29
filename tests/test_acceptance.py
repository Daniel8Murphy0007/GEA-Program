"""The product gate: the in-package acceptance suite must pass end to end."""
import subprocess, sys


def test_acceptance_suite():
    r = subprocess.run([sys.executable, "-m", "gea", "accept"], capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "[ACCEPTANCE] OK" in r.stdout


def test_no_source_program_reachable():
    """The package must not import the source program's corpus modules."""
    import importlib, pkgutil, gea
    for m in pkgutil.iter_modules(gea.__path__):
        src = open(f"{gea.__path__[0]}/{m.name}.py", encoding="utf-8").read()
        assert "uqff_calculator" not in src and "uqff_registry_primitives" not in src, m.name
