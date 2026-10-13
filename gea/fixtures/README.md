# fixtures

Small, verbatim, public-domain extracts kept so the gate can prove a reader on real records without a network.

- `comcat_delaware_basin_2026-09.txt` - USGS ComCat, FDSN event web service text output, fetched 2026-10-12:
  `https://earthquake.usgs.gov/fdsnws/event/1/query?format=text&starttime=2026-09-01&endtime=2026-10-01&minlatitude=30.5&maxlatitude=32.5&minlongitude=-104.5&maxlongitude=-102.0&minmagnitude=2.5&orderby=time`
  (28 events M >= 2.5, Delaware and Midland basins; contributors tx = TexNet, us = NEIC). US Government work, public domain.
- `comcat_youngstown_2011-2012.txt` - the same service, fetched 2026-10-12:
  `https://earthquake.usgs.gov/fdsnws/event/1/query?format=text&starttime=2011-03-01&endtime=2012-03-01&latitude=41.1&longitude=-80.68&maxradiuskm=25&minmagnitude=1.5&orderby=time-asc`
  (9 events within 25 km of Youngstown, Ohio: the 2011 sequence that ended with the M 4.0 of 2011-12-31; contributor ld = Lamont-Doherty). Public domain.
- `ne_110m_coastline.json` - Natural Earth 1:110m coastline (`ne_110m_coastline`), fetched 2026-10-13 from
  `https://github.com/nvkelso/natural-earth-vector` (geojson/ne_110m_coastline.geojson, md5 0b8f91e9d9c4fb607e61e121b5ac48fa):
  134 lines, 5,127 points after rounding to 0.01 degree and dropping consecutive duplicates. Public domain (Natural Earth).
  The only thing the catalogue world map draws besides the catalogue's own positions; a coastline at this scale places a point to the
  nearest ~10 km on the picture, which is what a world map is for and nothing more.
