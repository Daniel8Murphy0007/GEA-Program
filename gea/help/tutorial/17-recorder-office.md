# 17. The field recorder from the office
Who: office
Where: #/seismic
Takes: 30 minutes
> Pulling the box's files in, trying the whole chain on this computer before a box exists, and the drill-bit image.

## Steps
1. Before there is a box: **Seismic**, the **Field recorder** card, **Start the simulated box**. The recorder's own program runs on this computer with its test signal in place of the ADC, as a process of its own; its page is on port 8781. **Open its monitor** shows the live screen the attendant will see on the pad (lesson 26).
2. Wait a minute or two, then **Pull from it**. GEA fetches the manifest, downloads every file it does not have, checks each against its SHA-256, and makes two stations: the **array** of the ground-motion sensors (8-80 Hz, the sensors' positions from the box's config) and the **electrode** station (0.01-5 Hz). They appear under the Seismic tab as any station.
3. **Stop it**: every file closes and is listed. The pull brought real files; look at the array's page.
4. A real box on the pad network: the card takes its address (`http://192.168.1.50:8780` - the box's own page shows it), or the folder of the USB mirror carried to the office; **Pull**. Pull again later: only the new hours come across and the stations update in place. **Open the box's monitor** opens its live screen from its address.
5. A box on another network: GEA-VPN (lesson 23) gives it a private address the office pulls from, `http://10.77.0.3:8780`, through the pad's cellular modem.
6. **The image**: the card runs the drill-bit seismic end to end on a labelled scene - the stations on the ground, the rig as a source of known position moving along the well, the arrival-time differences resolving the rock's velocity layer by layer and placing the first layer below the bit. **Run the scene** (about ten seconds); the tiles say how far the recovered column is from the scene's own and where the layer below the bit was found against where the scene put it. The property column and the recorder's list - how many stations, where, at what rate - fold out below.

## You know it worked when
The array station from the simulated box has four sensors on its page and the electrode station two, and the image's tiles are green on the scene.

Help: recorder, bit-image
