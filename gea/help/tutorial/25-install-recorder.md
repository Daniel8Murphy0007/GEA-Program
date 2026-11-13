# 25. Install the recorder on the Pi
Who: pad
Where: #/seismic
Takes: 30 minutes
> One script, one config file, one restart.

## Before you start
The box assembled (lesson 24), the Pi on the pad network, and the release's `GEA-Recorder-0.88.0.tar.gz` - from the office's `installers\` folder (lesson 22) or the release page - copied to the Pi.

## Steps
1. On the Pi, unpack the kit and run the installer once: `tar xzf GEA-Recorder-0.88.0.tar.gz`, `cd GEA-Recorder-0.88.0`, `sudo sh install.sh`. It makes `/opt/gea-recorder` (a Python environment of its own), writes the example config to `/etc/gea-recorder/config.json`, makes `/var/lib/gea-recorder`, enables SPI, enables the service to start at boot and runs the self-test.
2. The clock: wire the GPS as lesson 24 says, then the three lines the README gives - `dtoverlay=pps-gpio,gpiopin=18` and `enable_uart=1` in `/boot/firmware/config.txt`, the two `refclock` lines in `/etc/chrony/chrony.conf` - `sudo apt install gpsd pps-tools`, and reboot. `chronyc tracking` then names PPS as the reference with an offset of microseconds.
3. Edit the config: `sudo nano /etc/gea-recorder/config.json`. The site's name as the office spells it; the station id; the wellhead's latitude and longitude; each channel's ADC input, its id (`REF`, `S01`...), its kind (`geophone` or `electrode`) and its place in feet east and north of the wellhead; the ADC driver `ads1256` (`sim` records a test signal with no hardware); the rig feed - `rig.enabled`, the serial port or the TCP host and port the EDR offers; the mirror path for the USB stick.
4. `sudo systemctl restart gea-recorder`. Open the box's page from any computer on the pad network: port 8780 of the box's address - `http://192.168.1.50:8780/` when that is its address. It shows the site, the station, RECORDING, the clock and what its time is worth, every channel's level, the files closed, the free space, the rig feed.
5. `gea-recorder status` on the Pi prints the same; `gea-recorder selftest` runs the self-test again.
6. Tell the office the box's address; it goes in the Field recorder card (lesson 17).

## You know it worked when
The page says RECORDING with the clock at 100 (locked to the PPS), every channel shows a level, and after an hour the files closed count reads 8.

Help: recorder
