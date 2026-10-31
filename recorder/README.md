# GEA Recorder - the black box

A field recorder for a drilling site, on a Raspberry Pi, feeding GEA-Program. It records the ground channels
(geophones, accelerometers, electrode dipoles) through a 24-bit ADC and the rig's WITS0 feed, every sample on one
GPS-disciplined clock, into hourly miniSEED files with a SHA-256 beside each and a manifest GEA reads; it serves
one status page on the pad network. No screen, no keyboard, nothing to type: power it and it records. Stop it and
every file is closed and listed. It never deletes anything.

## What it is made of

| Part | What | About |
|---|---|---|
| Computer | Raspberry Pi 4 or 5 (2 GB is enough), Raspberry Pi OS 64-bit | $60-80 |
| ADC | Waveshare High-Precision AD/DA board (Texas Instruments ADS1256: 8 inputs, 24-bit, SPI) | $30 |
| Clock | A GPS receiver with a PPS output (u-blox NEO-M8/M9 breakout), its PPS pin to GPIO 18, its serial to the Pi's UART | $20-40 |
| Storage | A 128 GB or larger card; a USB stick as the mirror | 8 channels at 200 samples/s is 55 MB an hour |
| Sensors | Vertical geophones (4.5 or 10 Hz), buried; non-polarising electrodes (copper-copper sulphate) in pairs 50-100 ft apart | the geophone's own frequency sits below the band GEA uses (8-80 Hz) |
| Power | 5 V 3 A; a 12 V battery with a buck converter runs it a week | 3-5 W |
| Enclosure | A sealed box with cable glands; the GPS antenna outside | |

Nothing on the rig. The reference sensor is buried within 40 ft of the substructure; the rig feed, when the
operator gives it, is a serial line or a TCP port from the EDR, read only.

## Install, once

On the Pi, with the release's `GEA-Recorder-<version>.tar.gz` unpacked:

    sudo sh install.sh

It makes `/opt/gea-recorder` (a virtual environment of its own), writes the example config to
`/etc/gea-recorder/config.json`, makes `/var/lib/gea-recorder`, enables SPI, enables the service
(`gea-recorder.service`, starting at boot) and runs the self-test. Then edit the config - the site, the wellhead's
latitude and longitude, each channel's ADC input, id, kind and place in feet east and north of the wellhead, the
ADC driver (`ads1256` for the HAT; `sim` records a test signal with no hardware), the rig feed - and
`sudo systemctl restart gea-recorder`. The status page is at port 8780 of the box's address on the pad network (`http://192.168.1.50:8780/` when that is its address).

### The clock

The box's clock is disciplined by chrony from the GPS receiver's pulse-per-second line; every sample is stamped from
it, so the ground channels, the rig feed and any other box on the pad agree to well under a millisecond, which is
what GEA's bit-seismic arithmetic needs. Wire the receiver's PPS to GPIO 18 and its serial output to the UART, then:

    /boot/firmware/config.txt      dtoverlay=pps-gpio,gpiopin=18
                                   enable_uart=1
    /etc/chrony/chrony.conf        refclock SHM 0 refid GPS precision 1e-1 offset 0.0 delay 0.2
                                   refclock PPS /dev/pps0 refid PPS lock GPS
    sudo apt install gpsd pps-tools; gpsd on /dev/serial0; reboot

`chronyc tracking` then names PPS as the reference with an offset of microseconds. The box asks chrony every ten
seconds and writes what the time is worth into every record (blockette 1001, 0-100: 100 locked to the PPS under
100 microseconds; 90 under a millisecond; 60 a network time source; 20 unsynchronised; 0 the box's own clock) and
into the manifest, so a reader knows what each hour's time is worth. Without GPS the box still records and says so.

### The ADC

The ADS1256 cycles its multiplexer through the inputs in use, each conversion waited for on DRDY; the chip's data
rate is set to give every channel at least the configured rate. The driver follows the datasheet and the
Waveshare wiring (SPI0; DRDY on GPIO 17, RESET 18, CS 22 - change `drdy_gpio`, `reset_gpio`, `cs_gpio` in the
config if the board differs); it has run on the bench through the simulator only, and the first Pi with the HAT
verifies the rate it achieves - the status page shows samples recorded against the hour. A channel configured at a
lower rate than the ADC (the electrodes at 10 samples/s against 200) is averaged down before it is written.

### The rig feed

WITS0 over a serial line or TCP (`rig.transport`, `rig.host`/`rig.port` or `rig.serial`/`rig.baud`). Every frame
is logged raw with the time of receipt (`rig/wits0.<hour>.log`), and the items the arithmetic needs are written
as a CSV on the box's clock (`rig/depth.csv`: bit depth, hole depth, block height, ROP, hookload, WOB, RPM,
torque, flow in, standpipe pressure, the rig's own date and time). The item codes are the WITS0 record 1 standard;
a rig that numbers them differently is mapped in the config (`rig.items`). Nothing is ever sent to the rig.

## What it writes

    /var/lib/gea-recorder/data/
      <YYYY>/<DDD>/GE.REF.HHZ.<YYYY>.<DDD>.<HH>.mseed        one file per channel per hour: 512-byte INT32 records,
      <YYYY>/<DDD>/GE.REF.HHZ.<YYYY>.<DDD>.<HH>.mseed.sha256  blockette 1000 and 1001; the SEED station code is the sensor id
      manifest.json                                            every closed file (name, bytes, SHA-256, start, end, samples,
                                                               timing quality), the site, the wellhead, the sensors
      sensors.csv                                              sensor_id, lat, lon, x_ft, y_ft, kind, code - the array as GEA reads it
      rig/depth.csv, rig/wits0.<hour>.log

Samples are appended every five seconds in whole records, so a power cut loses at most five seconds and never a
file. When a USB stick is mounted at the configured mirror, each closed file and the manifest are copied to it.
When free space falls under the floor (500 MB) the box stops recording and the page says so; it deletes nothing.

## Getting the files into GEA

On the pad network, GEA pulls them: the Seismic tab's card **Field recorder**, the box's address
(`http://192.168.1.50:8780` - the box's page shows its address), **Pull**. GEA fetches the manifest, downloads every file it does not have, checks each
against its SHA-256, and adds or refreshes the array station with the sensors' positions, so the files are on the
Seismic tab as any station's. From a terminal: `gea workspace --path C:\site --action field-pull --box http://192.168.1.50:8780`.
Off the network, the USB mirror carried to the office is the same folder.

## Commands

    gea-recorder run --config /etc/gea-recorder/config.json   record until stopped (what the service runs)
    gea-recorder selftest [--seconds 20]                     the test signal recorded, read back, every sample checked
    gea-recorder status                                      what the running box says
    gea-recorder config --write FILE | --check FILE          the example config; check one
    sudo systemctl status gea-recorder                       the service

Standard library plus numpy; spidev, lgpio and pyserial only where the hardware is. Mozilla Public License 2.0.
https://github.com/Daniel8Murphy0007/GEA-Program
