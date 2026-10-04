# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""render_report_samples - one rendered example of every client report, from this checkout.

    python tools/render_report_samples.py            # writes docs/report_samples/
    python tools/render_report_samples.py --out DIR

The set is what a reader opens to see what the program writes before it is
installed anywhere: the dashboard index, the gauge drift report (a real
catalogue well, and a monitored synthetic historian with its evaluation log),
well-test validation, the alarm and event report, the data-resilience report,
every model card, the accuracy statement, a monthly SLA report, the SAT
protocol and the pressure-transient report. Each file is produced by the same
command a site would run, named in SAMPLES.md beside it, and carries the build
number of this checkout. Re-run before every ship so the samples never drift
from the code; the acceptance suite checks they match.

The real well is Volve 15/9-F-12 and F-14 (the catalogue excerpt). The alarm,
resilience, monitored-drift and SLA samples need a historian with faults and
months in it, and no public catalogue entry has one: those are rendered from
`gea telemetry`, the program's own synthetic field generator, and the reports
say SYNTHETIC in their well name; the seismic station report is rendered from
the labelled synthetic scene of the second leg. Nothing here is a measurement
of any site.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

SAMPLES = [
    # (file in the sample set, source path inside the work folder, the command that made it)
    ('dashboard_index.html', 'dash/index.html', 'gea dashboard --catalog-well volve_f12_f14_production_excerpt:15/9-F-12:10000 --catalog-well volve_f12_f14_production_excerpt:15/9-F-14:10000 --td 10500 --name "Report samples" --out dash'),
    ('gauge_drift_report_volve_F12.html', 'dash/wells/volve_f12_f14_production_excerpt__15-9-F-12/gauge_drift_report.html', 'the dashboard run above'),
    ('gauge_drift_report_volve_F14.html', 'dash/wells/volve_f12_f14_production_excerpt__15-9-F-14/gauge_drift_report.html', 'the dashboard run above'),
    ('well_test_validation_volve_F12.html', 'dash/wells/volve_f12_f14_production_excerpt__15-9-F-12/well_test_validation.html', 'the dashboard run above'),
    ('well_test_validation_volve_F14.html', 'dash/wells/volve_f12_f14_production_excerpt__15-9-F-14/well_test_validation.html', 'the dashboard run above'),
    ('accuracy_statement_library.html', 'dash/accuracy_statement.html', 'the dashboard run above (the library back-test)'),
    ('model_card_gauge_aging_rate.html', 'dash/model_card_gauge_aging_rate.html', 'the dashboard run above'),
    ('model_card_quality_rules.html', 'dash/model_card_quality_rules.html', 'the dashboard run above'),
    ('model_card_well_baseline.html', 'dash/model_card_well_baseline.html', 'the dashboard run above'),
    ('model_card_well_test_detector.html', 'dash/model_card_well_test_detector.html', 'the dashboard run above'),
    ('model_card_rock_density_inventory.html', 'dash/model_card_rock_density_inventory.html', 'the dashboard run above'),
    ('model_card_strata_property_estimator.html', 'dash/model_card_strata_property_estimator.html', 'the dashboard run above'),
    ('gauge_drift_report_monitored_SYNTHETIC.html', 'mon_report/gauge_drift_report.html', 'gea telemetry --hours 720 --seed 11 --out field.csv; gea drift-monitor --action evaluate --file field.csv --log-dir monitor --force --name "SYNTHETIC field (gea telemetry)" --report mon_report'),
    ('alarm_event_report_SYNTHETIC.html', 'alarms/alarm_event_report.html', 'gea alarms --file field.csv --event-log alarms/events.jsonl --name "SYNTHETIC field (gea telemetry)" --out alarms'),
    ('data_resilience_report_SYNTHETIC.html', 'sf/data_resilience_report.html', 'gea store-forward --file field.csv --outage "<day 3 06:00,day 3 18:00>" --name "SYNTHETIC field (gea telemetry)" --out sf'),
    ('sla_report_SYNTHETIC.html', 'sla/sla_report_{month}.html', 'gea sla-report --month {month} --monitor-log-dir monitor --alarm-log alarms/events.jsonl --name "SYNTHETIC field (gea telemetry)" --out sla'),
    ('sat_protocol.html', 'sat/sat_protocol.html', 'gea fat-sat --kind SAT --name "Report samples" --out sat'),
    ('seismic_station_report_SYNTHETIC.html', 'seis_site/reports/seismic/SYNTHETIC-node/seismic_station_report.html',
     'gea workspace --path seis_site --action add-seismic --name "SYNTHETIC node" --files scene.mseed --lat 31 --lon -102 --sources rigs.csv --band 1 20; gea workspace --path seis_site --action refresh-seismic (the record and the rigs are the labelled synthetic scene of gea seismic --action selftest)'),
]


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
    if r.returncode != 0:
        raise SystemExit(f"failed: {' '.join(cmd)}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return r.stdout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='render one example of every client report')
    ap.add_argument('--out', default=str(ROOT / 'docs' / 'report_samples'))
    a = ap.parse_args(argv)
    out = Path(a.out)
    work = Path(tempfile.mkdtemp(prefix='gea_samples_'))
    g = [PY, '-m', 'gea']
    try:
        run(g + ['dashboard', '--catalog-well', 'volve_f12_f14_production_excerpt:15/9-F-12:10000', '--catalog-well', 'volve_f12_f14_production_excerpt:15/9-F-14:10000',
                 '--td', '10500', '--name', 'Report samples', '--out', 'dash'], work)
        run(g + ['telemetry', '--hours', '720', '--seed', '11', '--out', 'field.csv'], work)
        # the synthetic historian's own dates decide the SLA month and the outage
        import csv
        with open(work / 'field.csv', newline='', encoding='utf-8') as f:
            rows = list(csv.reader(f))
        stamps = [r[0] for r in rows[1:] if r and r[0][:4].isdigit()]
        first = stamps[0]
        month = first[:7]
        day3 = first[:8] + f"{int(first[8:10]) + 2:02d}"
        run(g + ['drift-monitor', '--action', 'evaluate', '--file', 'field.csv', '--log-dir', 'monitor', '--force', '--name', 'SYNTHETIC field (gea telemetry)',
                 '--report', 'mon_report'], work)
        (work / 'alarms').mkdir()
        run(g + ['alarms', '--file', 'field.csv', '--event-log', 'alarms/events.jsonl', '--name', 'SYNTHETIC field (gea telemetry)', '--out', 'alarms'], work)
        run(g + ['store-forward', '--file', 'field.csv', '--outage', f'{day3}T06:00:00Z,{day3}T18:00:00Z', '--name', 'SYNTHETIC field (gea telemetry)', '--out', 'sf'], work)
        run(g + ['sla-report', '--month', month, '--monitor-log-dir', 'monitor', '--alarm-log', 'alarms/events.jsonl', '--name', 'SYNTHETIC field (gea telemetry)', '--out', 'sla'], work)
        run(g + ['fat-sat', '--kind', 'SAT', '--name', 'Report samples', '--out', 'sat'], work)
        # the seismic station: the labelled synthetic scene through the workspace, as a site would run it
        run([PY, '-c', 'from gea import seismic as S, seismic_detect as D; tr, srcs, m = D.synthetic_scene(hours=6); '
                       'S.write_mseed([tr], "scene.mseed", "STEIM2"); D.write_sources_csv(srcs, "rigs.csv")'], work)
        run(g + ['workspace', '--path', 'seis_site', '--action', 'init', '--name', 'Report samples'], work)
        run(g + ['workspace', '--path', 'seis_site', '--action', 'add-seismic', '--name', 'SYNTHETIC node', '--files', 'scene.mseed', '--lat', '31', '--lon', '-102',
                 '--sources', 'rigs.csv', '--band', '1', '20'], work)
        run(g + ['workspace', '--path', 'seis_site', '--action', 'refresh-seismic'], work)
        if out.exists():
            for p in out.glob('*.html'):
                p.unlink()
        out.mkdir(parents=True, exist_ok=True)
        lines = ['# Report samples', '',
                 'One rendered example of every client report, produced by `python tools/render_report_samples.py` from this checkout',
                 '(the build number is printed in each report). The real well is the Volve 15/9-F-12 / F-14 catalogue excerpt; the files',
                 'marked SYNTHETIC are rendered from `gea telemetry`, the program\'s own synthetic field generator, because the alarm,',
                 'resilience, monitored-drift and SLA reports need faults and months that no public catalogue well carries. Nothing here',
                 'is a measurement of any site. Re-rendered before every ship; the acceptance suite checks the set matches the code.', '',
                 '| sample | the command that made it |', '|---|---|']
        n = 0
        for name, src, cmd in SAMPLES:
            srcp = work / src.replace('{month}', month)
            if not srcp.exists():
                raise SystemExit(f"{name}: {srcp} was not written")
            shutil.copyfile(srcp, out / name)
            lines.append(f"| `{name}` | `{cmd.replace('{month}', month)}` |")
            n += 1
        (out / 'SAMPLES.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f'== {n} samples -> {out}')
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
