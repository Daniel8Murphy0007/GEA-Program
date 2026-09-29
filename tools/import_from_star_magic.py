#!/usr/bin/env python3
"""Import the downhole gauge program from a Star-Magic-Program checkout into
this repository as the `gea` package - renamed, with the corpus dependencies
removed and replaced by standard constants and published anchors.

    python tools/import_from_star_magic.py /path/to/Star-Magic-Program

What it does, in order (every step is a text transformation you can read):

  1. Copies uqff_downhole_simulator/ -> gea/ and renames every uqff_*.py
     module to its bare name (uqff_reconciler.py -> reconciler.py).
  2. Drops the two modules that exist only to compose numbers from the
     corpus primitives (structural_ladder, differentiator) and their
     acceptance sections (O, W, X1-X2).
  3. quartz_hpht_extension: removes the corpus import; the engineering
     constants stay as plain constants, labelled as such.
  4. forward_model: replaces the corpus-composed {g, G, R} with standard
     constants (CODATA 2018 G; standard gravity; IUGG mean radius), cited.
  5. rock_inventory: keeps the seventeen density anchors and the Vp ranges
     from the published tables; removes the primitive decompositions.
  6. project (survey report): drops the structural-frame section.
  7. Rewrites identifiers: uqff_downhole_simulator -> gea, UQFF* names ->
     GEA*/standard names, the package's own `python -m` strings.
  8. Copies catalog/ (public entries + provenance), the sample files and
     the protocol; never the operator tier.
  9. Brings the helper programs and documents that belong to the product:
     the front door (gea/cli.py) and the desktop window (gea/shell.py) from
     tools/native/, written for this repository; the tester guide, the
     commercial documents and renders, the requirements matrix that shaped
     the report family, and the bench protocol, into docs/.
 10. Writes gea/IMPORT_RECORD.md with the source commit, file map and hashes.

Refuses to overwrite an existing gea/ unless --force is given: after the first
import this repository evolves on its own.

Then run:  python -m gea accept          (the product's acceptance suite)
           python tools/register_audit.py (what internal-register text remains)
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEST = os.path.join(REPO, 'gea')
DROP_MODULES = {'uqff_structural_ladder.py', 'uqff_differentiator.py'}
DATA_DIRS = ['catalog']
DATA_FILES = ['sample_well_profile.csv', 'example_register_map.json', 'BENCH_TEST_PROTOCOL.md', 'README.md']


def rd(p):
    with io.open(p, encoding='utf-8') as f:
        return f.read()


def wr(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(s)


def must(s, old, new, count=1, where=''):
    if old not in s:
        raise SystemExit(f'import step failed: expected text not found in {where}: {old[:70]!r}')
    return s.replace(old, new, count)


def git_head(src):
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=src, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return 'unknown'


def main(src: str) -> int:
    pkg = os.path.join(src, 'uqff_downhole_simulator')
    if not os.path.isdir(pkg):
        raise SystemExit(f'not a Star-Magic-Program checkout: {pkg} missing')
    if os.path.isdir(DEST):
        if '--force' not in sys.argv:
            raise SystemExit('gea/ already exists - this repository has been imported; pass --force to re-import from source')
        try:
            shutil.rmtree(DEST)
        except PermissionError:
            print('note: cannot delete the existing gea/ here; files will be overwritten in place')
    os.makedirs(DEST, exist_ok=True)
    filemap = {}
    # 1. copy + rename modules ------------------------------------------------------------------
    for fn in sorted(os.listdir(pkg)):
        p = os.path.join(pkg, fn)
        if fn.endswith('.py'):
            if fn in DROP_MODULES:
                filemap[fn] = '(dropped)'
                continue
            new = fn[5:] if fn.startswith('uqff_') else fn
            shutil.copy2(p, os.path.join(DEST, new))
            filemap[fn] = new
    for d in DATA_DIRS:
        shutil.copytree(os.path.join(pkg, d), os.path.join(DEST, d), dirs_exist_ok=True)
    for fn in DATA_FILES:
        if os.path.exists(os.path.join(pkg, fn)):
            if fn == 'README.md':
                os.makedirs(os.path.join(REPO, 'docs'), exist_ok=True)
                shutil.copy2(os.path.join(pkg, fn), os.path.join(REPO, 'docs', 'HISTORY.md'))
            else:
                shutil.copy2(os.path.join(pkg, fn), os.path.join(DEST, fn))

    # 3. quartz_hpht_extension: corpus import out, constants in ----------------------------------
    p = os.path.join(DEST, 'quartz_hpht_extension.py')
    s = rd(p)
    # module docstring: the model is an engineering model with named constants
    s = re.sub(r'^"""uqff_quartz_hpht_extension.*?"""\n', (
        '"""quartz_hpht_extension - the gauge aging models.\n\n'
        'Two aging models evaluated at station pressure and temperature: the conventional\n'
        'datasheet model (baseline drift scaled by thermal and pressure factors with the\n'
        'template knees and exponents) and the program aging model, which multiplies the\n'
        'conventional model by a fixed suppression composition of three engineering\n'
        'constants (K_MEX = 25/12, PHI_RES = 0.84, F_TRZ = 0.1; ratio 0.9686). The\n'
        'suppression composition is an engineering model with no field validation on\n'
        'record; the model card says so, and the drift evaluation uses the band between\n'
        'the two models, never the program model alone. `k_structural_trim` and\n'
        '`phi_coupling_trim` (default 1.0) are instrument-tuning gains. Industry anchors\n'
        'carry inline source comments.\n"""\n'), s, count=1, flags=re.S)
    start = s.index('# ---------------------------------------------------------------------------\n# UQFF binding')
    end = s.index('def canonical_suppression(')
    s = (s[:start]
         + '# Engineering constants of the program aging model (template lineage, 22 Aug 2026).\n'
         + '# They are settings, not measurements; the model card labels this model as\n'
         + '# having no field validation on record.\n'
         + '_K_MEX = 25.0 / 12.0        # structural factor (engineering constant)\n'
         + '_PHI_RES = 0.84             # resonance factor (engineering constant)\n'
         + '_F_TRZ = 0.1                # vacuum-stability factor (engineering constant)\n'
         + '_U_I = 2.75e-7              # coupling constant (engineering constant)\n'
         + 'PROGRAM_MODEL_AVAILABLE = True\n\n\n'
         + s[end:])
    wr(p, s)

    # 4. forward_model: standard constants ----------------------------------------------------------
    p = os.path.join(DEST, 'forward_model.py')
    s = rd(p)
    s = must(s, "# UQFF-composed constants (papers + honest residuals in the module docstring)\n"
                "G_UQFF = 6.669e-11                 # PAPER_593\n"
                "G_SURFACE_UQFF = 9.8125            # PAPER_1598\n"
                "R_EARTH_UQFF_M = 6371.0e3          # PAPER_1209CC S603 EXACT (km -> m)\n",
             "# Standard constants, cited\n"
             "G_STD = 6.67430e-11                # CODATA 2018 Newtonian constant of gravitation, m^3 kg^-1 s^-2\n"
             "G_SURFACE_STD = 9.80665            # standard acceleration of gravity, ISO 80000-3 / CGPM 1901, m s^-2\n"
             "R_EARTH_M = 6371008.8              # IUGG mean Earth radius (R1), m\n", where='forward_model constants')
    s = s.replace('FREE_AIR_UQFF = 2.0 * G_SURFACE_UQFF / R_EARTH_UQFF_M', 'FREE_AIR_STD = 2.0 * G_SURFACE_STD / R_EARTH_M')
    s = must(s, "            'this test validates the UQFF constant chain {g_U, G_U, R_U} '\n"
                "            'against the vendor loop - a constants test, not an independent '\n"
                "            'strata test. It remains falsifiable: an incorrect UQFF g, G or '\n"
                "            'R appears directly as bias here.'),",
             "            'this test uses standard constants (CODATA 2018 G, standard gravity, '\n"
             "            'IUGG mean radius) against the vendor loop - a constants test, not an '\n"
             "            'independent strata test. It remains falsifiable: a wrong g, G or R '\n"
             "            'appears directly as bias here.'),", where='forward_model caveat')
    # module docstring header lines
    s = re.sub(r"^    g_U  = .*\n    G_U  = .*\n    R_U  = .*\n", "    g   = 9.80665 m/s^2 (standard gravity)\n    G   = 6.67430e-11 m^3/kg/s^2 (CODATA 2018)\n    R   = 6371.0088 km (IUGG mean radius)\n", s, count=1, flags=re.M)
    s = s.replace("free-air gradient F_U = 2*g_U/R_U = 0.30804 mGal/m", "free-air gradient F = 2*g/R = 0.30785 mGal/m")
    wr(p, s)

    # 5. rock_inventory: published anchors only -------------------------------------------------------
    p = os.path.join(DEST, 'rock_inventory.py')
    s = rd(p)
    # drop the primitives import block
    s = re.sub(r"_ROOT = .*?\nfrom uqff_registry_primitives import [^\n]*\n(?:\s+[^\n]*\)\n)?", "", s, count=1, flags=re.S)
    s = re.sub(r"^from uqff_registry_primitives import[^\n]*\n", "", s, flags=re.M)
    # density inventory: parse the entries and rebuild them from anchors
    m = re.search(r"def _f\(\):\n.*?\n    return \{\n(.*?)\n    \}\n", s, flags=re.S)
    if not m:
        raise SystemExit('rock_inventory: _f() not found')
    entries = re.findall(r"'(\w+)':\s*dict\(tier='(\w+)', anchor=([\d.]+), lo=([\d.]+), hi=([\d.]+)", m.group(1))
    if len(entries) != 17:
        raise SystemExit(f'rock_inventory: expected 17 entries, parsed {len(entries)}')
    body = "def _f():\n    \"\"\"The seventeen density anchors from the cited tables (g/cc).\"\"\"\n    return {\n"
    for name, tier, anchor, lo, hi in entries:
        body += f"        '{name}': dict(tier='{tier}', anchor={anchor}, lo={lo}, hi={hi}, rho={anchor}, form='published anchor'),\n"
    body += "    }\n"
    s = s[:m.start()] + body + s[m.end():]
    s = must(s, "'(Telford et al. 1990; Schoen 2015), range disclosed; '\n                         'primitive form: K4 family, Daniel 2026-09-08')",
             "'(Telford et al. 1990; Schoen 2015), range disclosed')", where='rock_inventory citation')
    # Vp tier: midpoints of the published ranges, no forms
    m = re.search(r"def _vpf\(\):\n.*?\n    \}\n", s, flags=re.S)
    if not m:
        raise SystemExit('rock_inventory: _vpf() not found')
    s = s[:m.start()] + ("def _vpf():\n    \"\"\"Vp tier: the published range midpoints (km/s), no decomposition.\"\"\"\n"
                         "    return {name: ('published midpoint', mid / 1000.0) for name, (lo, hi, mid) in VP_RANGES.items()}\n") + s[m.end():]
    wr(p, s)

    # 6. project: drop the structural-frame section ------------------------------------------------------
    p = os.path.join(DEST, 'project.py')
    s = rd(p)
    s = must(s, "    from .uqff_structural_ladder import ladder, earth_model_audit\n", "", where='project import')
    s = must(s, "    aud = earth_model_audit()\n    rungs = ladder()\n", "", where='project ladder calls')
    m = re.search(r"    w\('## 2\. Structural frame.*?aud\['library_reach_pct_of_crust'\]\)\)\n    w\(''\)\n", s, flags=re.S)
    if not m:
        raise SystemExit('project: structural frame section not found')
    s = s[:m.start()] + s[m.end():]
    s = s.replace("    w('UQFF-composed constants vs the KTB borehole gravimeter: correlation '", "    w('Standard-constant gravity kernel vs the KTB borehole gravimeter: correlation '")
    wr(p, s)

    # __init__: remove the dropped modules from the package lists ----------------------------------------
    p = os.path.join(DEST, '__init__.py')
    s = rd(p)
    for mod in ('uqff_structural_ladder', 'uqff_differentiator'):
        s = re.sub(r"^from \.%s import[^\n]*\n(?:\s+[^\n]*\n)*?(?=\S)" % mod, "", s, flags=re.M)
        s = re.sub(r"^\s*\"%s\",\n" % mod, "", s, flags=re.M)
    wr(p, s)

    # acceptance: drop sections O and W and the differentiator checks in X ------------------------------------
    p = os.path.join(DEST, 'acceptance_tests.py')
    s = rd(p)
    for fn in ('section_o_structural_ladder', 'section_w_differentiator'):
        m = re.search(r"\ndef %s\(\)[^\n]*\n.*?(?=\ndef |\Z)" % fn, s, flags=re.S)
        if not m:
            raise SystemExit(f'acceptance: {fn} not found')
        s = s[:m.start()] + s[m.end():]
        s = s.replace(f"        {fn}()\n", "")
    m = re.search(r"    from \.uqff_differentiator import u_i_coupling_harness\n.*?(?=    from \.uqff_gravity_reference import)", s, flags=re.S)
    if not m:
        raise SystemExit('acceptance: X1-X2 block not found')
    s = s[:m.start()] + s[m.end():]
    s = must(s, "    ok(abs(FREE_AIR_UQFF * 1e5 - 0.30804) < 0.00001", "    ok(abs(FREE_AIR_UQFF * 1e5 - 0.30785) < 0.00001", where='acceptance N1')
    s = s.replace("UQFF free-air gradient composes to 0.30804 mGal/m", "standard free-air gradient 2g/R = 0.30785 mGal/m")
    s = must(s, "    ok(len(vi) == 17 and n_exact == 11 and worst < 0.65", "    ok(len(vi) == 17 and n_exact == 17 and worst < 1e-9", where='acceptance Z10')
    wr(p, s)

    # 7. identifier and text rewrites across the package --------------------------------------------------------
    rewrites = [
        ('uqff_downhole_simulator', 'gea'),
        ('UQFFDownholeEngine', 'DownholeEngine'),
        ('calculate_quartz_transducer_hpht_UQFF', 'calculate_quartz_transducer_hpht_program'),
        ('UQFF_AVAILABLE', 'PROGRAM_MODEL_AVAILABLE'),
        ('G_SURFACE_UQFF', 'G_SURFACE_STD'), ('R_EARTH_UQFF_M', 'R_EARTH_M'), ('FREE_AIR_UQFF', 'FREE_AIR_STD'), ('G_UQFF', 'G_STD'),
        ("'g_UQFF'", "'g_std'"), ("'R_earth_UQFF_m'", "'R_earth_m'"), ("'free_air_UQFF_mgal_per_m'", "'free_air_std_mgal_per_m'"),
        ('NO_UQFF_MODEL', 'NO_PROGRAM_MODEL'), ('NO_UQFF_LEG', 'NO_PROGRAM_LEG'),
        ('UQFF-composed constants', 'standard constants'), ('UQFF-composed', 'standard-constant'), ('UQFF leg', 'program-model leg'), ('UQFF-leg', 'program-model-leg'),
        ('UQFF', 'GEA'), ('Star-Magic-Program', 'GEA-Program'), ('Star-Magic Program', 'GEA-Program'), ('Star-Magic', 'GEA'),
        ('_uqff', '_program'), ('star-magic-program', 'gea-program'), ('star-magic survey', 'gea survey'), ('star-magic', 'gea'),
        ('--uqff-csv', '--program-csv'), ('STAR-MAGIC SURVEY', 'GEA SURVEY'),
    ]
    targets = [os.path.join(DEST, fn) for fn in os.listdir(DEST) if fn.endswith('.py') or fn.endswith('.md')]
    targets.append(os.path.join(REPO, 'docs', 'HISTORY.md'))
    for p in targets:
        fn = os.path.basename(p)
        s = rd(p)
        s = s.replace('uqff_downhole_simulator', 'gea')                       # the package name first
        s = re.sub(r"\.uqff_([a-z0-9_]+)", r".\1", s)                       # relative imports
        s = re.sub(r"\buqff_(?!calculator|registry)([a-z0-9_]+)", r"\1", s)   # bare module names in strings/imports
        for a, b in rewrites:
            s = s.replace(a, b)
        wr(p, s)
    # the report vocabulary gate keeps forbidding the SOURCE program's register (never the product's own name)
    p = os.path.join(DEST, 'client_reports.py')
    s = rd(p)
    s = re.sub(r"FORBIDDEN_TERMS = \(.*?\)\n", "FORBIDDEN_TERMS = ('uqff', 'star-magic', 'star magic', 'primitive', 'doctrine',\n"
               "                   'pinned_awaiting', 'refus', 'aether', 'dpm', 'rule 7', 'honest',\n"
               "                   'landmark', 'lattice')\n", s, count=1, flags=re.S)
    wr(p, s)
    # sanity: nothing left that reaches the corpus
    for fn in os.listdir(DEST):
        if fn.endswith('.py'):
            t = rd(os.path.join(DEST, fn))
            if 'uqff_calculator' in t or 'uqff_registry_primitives' in t or 'sys.path.insert' in t and '_ROOT' in t:
                raise SystemExit(f'{fn} still reaches the corpus')

    # 9. helper programs and documents ------------------------------------------------------------------------
    native = os.path.join(HERE, 'native')
    for fn in sorted(os.listdir(native)):
        if fn.endswith('.py'):
            shutil.copy2(os.path.join(native, fn), os.path.join(DEST, fn))
            filemap[f'(native tools/native/{fn})'] = fn
    docs = os.path.join(REPO, 'docs')
    os.makedirs(os.path.join(docs, 'commercial'), exist_ok=True)
    guide = rd(os.path.join(src, 'TESTER_GUIDE.md'))
    guide = (guide.replace('# HOW TO TEST STAR-MAGIC', '# HOW TO TEST GEA')
                  .replace('pip install star-magic-program', 'pip install gea-program      (or, from a checkout of this repository:  pip install .)')
                  .replace('python -m star_magic_cli', 'python -m gea.cli').replace('py -m star_magic_cli', 'py -m gea.cli')
                  .replace('INSTALL STAR-MAGIC', 'INSTALL GEA').replace('Star-Magic', 'GEA').replace('star-magic', 'gea'))
    wr(os.path.join(docs, 'TESTER_GUIDE.md'), guide)
    for fn in ('COMMERCIAL.md',):
        if os.path.exists(os.path.join(src, fn)):
            wr(os.path.join(docs, 'commercial', fn), rd(os.path.join(src, fn)))
    csrc = os.path.join(src, 'commercial')
    if os.path.isdir(csrc):
        for root, _, fns in os.walk(csrc):
            for fn in fns:
                rel = os.path.relpath(os.path.join(root, fn), csrc)
                dst = os.path.join(docs, 'commercial', rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(root, fn), dst)
    tm = os.path.join(src, 'tender', 'CDG2752P27_REQUIREMENTS_MATRIX.md')
    if os.path.exists(tm):
        wr(os.path.join(docs, 'REQUIREMENTS_MATRIX.md'),
           '> The requirements matrix that shaped the report family: a production-operations scope of work mirrored clause by clause, '
           'with what the program does today and what it gets better by. Format reference, not a bid.\n\n' + rd(tm))
    # rename pass over the imported documents too
    for root, _, fns in os.walk(docs):
        for fn in fns:
            if fn.endswith('.md'):
                p = os.path.join(root, fn)
                t = rd(p)
                t = t.replace('uqff_downhole_simulator', 'gea')
                t = re.sub(r"\buqff_(?!calculator|registry)([a-z0-9_]+)", r"\1", t)
                for a, b in rewrites:
                    t = t.replace(a, b)
                wr(p, t)

    # 10. import record --------------------------------------------------------------------------------------------
    head = git_head(src)
    lines = [f'# Import record\n', f'Source: Star-Magic-Program @ {head}', f'Imported: {datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}', '',
             '| source | destination | sha256 (first 16) |', '|---|---|---|']
    for a, b in sorted(filemap.items()):
        h = hashlib.sha256(rd(os.path.join(DEST, b)).encode('utf-8')).hexdigest()[:16] if b != '(dropped)' else '-'
        lines.append(f'| uqff_downhole_simulator/{a} | gea/{b} | {h} |')
    lines += ['', 'Transformations: corpus import removed from quartz_hpht_extension (constants labelled as settings); '
              'forward_model on CODATA 2018 G, standard gravity and the IUGG mean radius; rock_inventory on the published '
              'anchors and range midpoints only; structural_ladder and differentiator dropped; survey report without the '
              'structural-frame section; identifiers renamed (see tools/import_from_star_magic.py).']
    wr(os.path.join(DEST, 'IMPORT_RECORD.md'), '\n'.join(lines) + '\n')
    print(f'imported {sum(1 for v in filemap.values() if v != "(dropped)")} modules into gea/ from {head[:10]}; dropped {sorted(DROP_MODULES)}')
    return 0


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) != 1:
        raise SystemExit(__doc__)
    sys.exit(main(os.path.abspath(args[0])))
