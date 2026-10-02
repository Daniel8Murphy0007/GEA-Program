# Bring data in

> A file, a catalogue entry, or a live feed; each becomes a well with the same reports.

**The command**: a file - `gea workspace --path C:\site --action add-file --file historian.csv --name "Well A"` (CSV, LAS, SEG-Y, an operator table, an OLE workbook with the `xls` extra; the Files page browses the roots an administrator allows and imports with a duplicate guard by hash). A live feed - `gea workspace ... --action add-live --name "Pad 3 OPC" --port opcua --file opcua.json`, or on the Patch panel: WITS Level 0 (TCP connect, TCP listen, serial), WITSML 1.4.1, OPC UA, MQTT (Sparkplug B), Modbus. `gea wits0-sim` and the WITSML test store let you rehearse with no rig.

**What it writes**: `wells/<id>/source/<file>` copied in verbatim with its SHA-256; a live patch writes `wells/<id>/records/live/patch_<name>_<YYYYMMDD>.records.csv` as each sample arrives and folds them into `<stamp>_stream.csv` at every refresh.

**The number to check**: on the well page, the source hash equals `sha256sum` of your original; on the Patch panel, `samples per minute` is not zero and the state is CONNECTED.

**What this page will not call a measurement**: a sample's unit is converted only at the mapping (`unit_in` on the tag), and the conversion is printed; a value the source marks bad or a sentinel (WITS `-9999`) is a GAP, never a number.

Related: `gea help quality`, `gea help patches`, `gea help files`.
