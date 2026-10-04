# Report samples

One rendered example of every client report, produced by `python tools/render_report_samples.py` from this checkout
(the build number is printed in each report). The real well is the Volve 15/9-F-12 / F-14 catalogue excerpt; the files
marked SYNTHETIC are rendered from `gea telemetry`, the program's own synthetic field generator, because the alarm,
resilience, monitored-drift and SLA reports need faults and months that no public catalogue well carries. Nothing here
is a measurement of any site. Re-rendered before every ship; the acceptance suite checks the set matches the code.

| sample | the command that made it |
|---|---|
| `dashboard_index.html` | `gea dashboard --catalog-well volve_f12_f14_production_excerpt:15/9-F-12:10000 --catalog-well volve_f12_f14_production_excerpt:15/9-F-14:10000 --td 10500 --name "Report samples" --out dash` |
| `gauge_drift_report_volve_F12.html` | `the dashboard run above` |
| `gauge_drift_report_volve_F14.html` | `the dashboard run above` |
| `well_test_validation_volve_F12.html` | `the dashboard run above` |
| `well_test_validation_volve_F14.html` | `the dashboard run above` |
| `accuracy_statement_library.html` | `the dashboard run above (the library back-test)` |
| `model_card_gauge_aging_rate.html` | `the dashboard run above` |
| `model_card_quality_rules.html` | `the dashboard run above` |
| `model_card_well_baseline.html` | `the dashboard run above` |
| `model_card_well_test_detector.html` | `the dashboard run above` |
| `model_card_rock_density_inventory.html` | `the dashboard run above` |
| `model_card_strata_property_estimator.html` | `the dashboard run above` |
| `gauge_drift_report_monitored_SYNTHETIC.html` | `gea telemetry --hours 720 --seed 11 --out field.csv; gea drift-monitor --action evaluate --file field.csv --log-dir monitor --force --name "SYNTHETIC field (gea telemetry)" --report mon_report` |
| `alarm_event_report_SYNTHETIC.html` | `gea alarms --file field.csv --event-log alarms/events.jsonl --name "SYNTHETIC field (gea telemetry)" --out alarms` |
| `data_resilience_report_SYNTHETIC.html` | `gea store-forward --file field.csv --outage "<day 3 06:00,day 3 18:00>" --name "SYNTHETIC field (gea telemetry)" --out sf` |
| `sla_report_SYNTHETIC.html` | `gea sla-report --month 2026-01 --monitor-log-dir monitor --alarm-log alarms/events.jsonl --name "SYNTHETIC field (gea telemetry)" --out sla` |
| `sat_protocol.html` | `gea fat-sat --kind SAT --name "Report samples" --out sat` |
| `seismic_station_report_SYNTHETIC.html` | `gea workspace --path seis_site --action add-seismic --name "SYNTHETIC node" --files scene.mseed --lat 31 --lon -102 --sources rigs.csv --band 1 20; gea workspace --path seis_site --action refresh-seismic (the record and the rigs are the labelled synthetic scene of gea seismic --action selftest)` |
| `seismic_track_report_SYNTHETIC.html` | `gea workspace --path seis_site --action add-seismic --name "SYNTHETIC array 1" --files A1S00.mseed ... --sensors sensors1.csv --band 1 20 (and array 2); gea workspace --path seis_site --action add-track --name "SYNTHETIC lateral" --stations SYNTHETIC-array-1 SYNTHETIC-array-2 --truth truth.csv; gea workspace --path seis_site --action refresh-track (the records and the truth are the labelled lateral scene of gea seismic --action track-selftest)` |
