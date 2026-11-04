# 26. The monitor on the box
Who: pad
Where: #/seismic
Takes: 10 minutes
> A screen on the Pi's HDMI port is the box's own instrument panel: green when it records, red with the reason when it does not, nothing to press.

## Steps
1. Plug the screen into the Pi's HDMI port. After `install.sh`, once: `sudo sh kiosk.sh`. From the next boot the screen shows the live view full screen.
2. Read it, left to right and top to bottom: **RECORDING** in green, or in red what is wrong - *not recording*, *clock below 90*, *rig feed down*, *disk under 2 GB*, a channel with *no signal* (a cable off) or *clipping* (a sensor swamped). The clock and what its time is worth. The rig's bit depth and hole depth as they come in. The disk and the files closed. For every channel, the last minute of signal scrolling by with its level, and a five-minute spectrogram (1-80 Hz) for the ground sensors.
3. What to do: *no signal* on a channel - follow its cable to the sensor; *clipping* - the sensor is too near the source or the gain is too high; *clock below 90* - the GPS antenna's view of the sky; *rig feed down* - the EDR's line or the operator's switch; *disk* - swap the USB mirror and tell the office; *not recording* - the page's message says why, and the service restarts the source every five seconds by itself.
4. The same view is at `/live` on the box's page - `http://192.168.1.50:8780/live` when that is its address - from any browser on the pad network, and from the office through the VPN.
5. A box with no screen is read from the office: **Open the box's monitor** on the Field recorder card.

## You know it worked when
The screen is green, every channel scrolls, and the spectrogram shows the rig's lines when the pumps are on.

Help: recorder
