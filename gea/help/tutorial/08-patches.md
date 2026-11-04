# 8. Live data: the patch panel
Who: office
Where: #/live
Takes: 30 minutes
> A supervised live connection to a rig or a panel: it reconnects on its own, records every sample, and says when it is down.

## The protocols
WITS Level 0 (serial, or TCP), WITSML 1.4.1 and WITSML 2.x over ETP, OPC UA, MQTT (Sparkplug B), Modbus TCP and Modbus RTU, DNP3, HART-IP, PI Web API (a historian pulled on an interval), SeedLink (a seismic station). Each has a stand-in to rehearse against before the rig is on the line.

## Steps
1. Rehearse with no rig: in a terminal, `gea wits0-sim --port 5001` sends WITS0 frames from a synthetic well. Leave it running.
2. Open **Patch panel**, **Add a patch**: the protocol `wits0`, the well it feeds, the host `127.0.0.1` and the port `5001`, and the tag map - **Load the example map** fills it; the map says which incoming item becomes which channel, in which unit.
3. Press **Add and start**. The patch goes CONNECTING, then CONNECTED; the sample count climbs. Click the patch for its page: every sample recorded, the last values, the log.
4. Stop the simulator. The patch goes DEGRADED (no sample for longer than `stale_after_s`) and then DOWN; it retries with a backoff of 2 to 60 seconds and says so. Start the simulator again: CONNECTED, with no gap in what was recorded while it was up.
5. For the real rig: the same form with the rig's address, or the COM port for a serial line (the `serial` extra: `pip install "gea-program[serial]"`). Nothing is ever sent to the rig; every port is read-only.
6. A gauge panel on the pad: protocol `modbus_rtu`, `dnp3` or `hartip`; **Load the example map** and put in the panel's registers or points with their units and the map's source. A historian: `piwebapi`, with the PI Point paths; the credentials are named by environment variables, never kept in the map.
7. The Patch panel tab carries a badge with the patches that are DOWN; a `patch.down` event goes to the notification rules (lesson 9).

## You know it worked when
The well's page shows the live channels on its chart and the patch reads CONNECTED after you stopped and started the source.

Help: patches, data-in, field-ports, historian, etp, seedlink
