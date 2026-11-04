# 12. Where the well is: Map, 3D, structure, interval and slip
Who: office
Where: #/map
Takes: 30 minutes
> Everything with a position in one frame, the well path among the mapped formations, and the two geological statements the program makes about a well.

## Steps
1. Open **Map**. The wells sit where their surface positions were declared (lesson 3); a well without one is listed as *not placed* and never drawn - **declare it** beside it opens the Setup item. Drag to pan, the wheel to zoom, **Home** to return. Tick **Rings** for distance circles around every well.
2. Bring a structure layer: **Seismic**, **Structure layers**, **Add layer** - a shapefile zip (a public structure map from the USGS, a state survey or BEG/TexNet, or the operator's own) with its name and citation. A zip holding several shapefiles lists them; each becomes its own layer. A layer in a coordinate system the program cannot use by name is refused and says so.
3. Back on the Map, tick the layer; click anywhere for what every layer says about that spot - the nearest fault with its distance and bearing, the contours it lies between, the area it falls in, the distance to every well.
4. The well page's **Mapped structure** card is the first geological statement: how far, and in which direction, the nearest mapped fault is.
5. **The interval against the mapped tops**: declare the injection interval (top and base, TVD, below the reference elevation) on the well's Setup item, and tell a formation-top layer what it maps (its formation and its role, *top* or *isopach*). The card says ABOVE, BELOW or NOT RESOLVED, and whether the declared depth tier agrees with the mapped base.
6. **Slip tendency**: declare the stress state once with its sources - the vertical and horizontal stress gradients, the pore pressure gradient, the SHmax azimuth (or let the World Stress Map's records near the well give it, stated) - and the card says how close each mapped fault within the radius is to slipping under the declared pressure change, with what every number rests on.
7. Open **3D**. Choose this workspace's wells or the Volve example. Drag to turn, shift-drag to pan, the wheel to zoom. A workspace well is drawn once it has its surface position, its reference elevation and its **directional survey** - **Attach a directional survey** on this tab (a WITSML trajectory or a table of measured depth, inclination and azimuth, with its azimuth reference and source). Colour the wells by formation, inclination, dogleg or a recorder channel; tick the surfaces; **Replay** runs the bit down the hole.

## You know it worked when
Clicking the map at the well names the nearest fault, and the 3D view shows the well path through the mapped tops.

Help: map, structure, interval, slip, well3d
