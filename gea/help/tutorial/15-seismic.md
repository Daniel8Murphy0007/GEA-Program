# 15. Seismic stations, arrays and tracks
Who: office
Where: #/seismic
Takes: 45 minutes
> Raw seismic records in, spectra and persistent lines out, one measured number before anything else is promised - the distance at which a known rig shows in this station's record - and, with an array, the direction it came from.

## Steps
1. Open **Seismic**, **Add a station or an array**: the name, the record files (miniSEED or SAC - the format is read from the content), the station's position, and the **sources** - a CSV of rigs with their positions and working windows (`source_id, lat, lon, start_utc, end_utc`). A permit export from the regulator converts on the way in. For an array, several records with a sensors CSV (`sensor_id, lat, lon`).
2. Press **Refresh this station**. The leg runs as a job: the Welch spectrum, the lines that persist above the local floor in the band, the **detectability test** - did this station see each listed rig in the windows it worked alone, and at what distance - and for an array the back-azimuth and slowness the band's energy crossed it with.
3. Open the station's page: the spectrum, the lines, the verdict per rig, the beam. The report is written beside it.
4. **Tracks**: two or more arrays make a track - **Add track** over them, with the lateral's surveyed points as ground truth when you have them. **Refresh this track** crosses the arrays' bearings window by window into positions with their ellipses; the track view shows the map, a slider through time and the bearings over time.
5. **Live stations (SeedLink)**: a station that serves SeedLink (TexNet's through EarthScope, or the site's own digitiser) is added with its config and started; the service keeps it up and the finished day files fold into the station's records.
6. **SAR panel**: the control panel and the screen of the whole leg - the trace, the spectrogram arriving, the beam power map swinging onto the working rig, the bearing against the rig's true bearing, the tally per rig - played on a labelled scene stamped `SIMULATION_SELF_TEST`.
7. The station's instrument response, when the site has the StationXML: attach it and the leg turns counts into velocity before anything else.

## You know it worked when
The station's page says at what distance the listed rig was seen, and the track view shows the lateral's advance against its surveyed points.

Help: seismic, catalog, seedlink
