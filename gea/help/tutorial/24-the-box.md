# 24. The box: what it is and what is in it
Who: pad
Where: #/seismic
Takes: 20 minutes to read; a day to build
> GEA Recorder is a Raspberry Pi in a sealed box that records the ground around the pad and the rig's feed, every sample on one GPS clock. Nothing to type: power it and it records.

## What it records
Geophones buried around the pad (8-80 Hz, the band the drill bit and the pumps show in) and electrode pairs driven into the ground (0.01-5 Hz), through a 24-bit ADC, plus the rig's WITS0 feed - bit depth, hole depth, block height, ROP, hook load, WOB, RPM, torque, flow, standpipe pressure - when the operator gives it. Nothing is on the rig; nothing is ever sent to the rig.

## The parts
1. Computer: a Raspberry Pi 4 or 5 (2 GB is enough) with Raspberry Pi OS 64-bit on a 128 GB or larger card.
2. ADC: the Waveshare High-Precision AD/DA board (the Texas Instruments ADS1256: 8 inputs, 24-bit), on the Pi's header. Its wiring is the board's own: SPI0, DRDY on GPIO 17, RESET 18, CS 22.
3. Clock: a GPS receiver with a PPS output (a u-blox NEO-M8 or M9 breakout): its PPS pin to GPIO 18, its serial to the Pi's UART, its antenna outside the box.
4. Sensors: vertical geophones (4.5 or 10 Hz), each buried a foot down, the **reference** within 40 ft of the substructure and the others in a ring around the pad; non-polarising electrodes (copper-copper sulphate) in pairs 50-100 ft apart. Every sensor's place is measured in feet east and north of the wellhead and written in the config.
5. Power: 5 V at 3 A; a 12 V battery through a buck converter runs it a week. The box draws 3-5 W.
6. Storage: the card, and a USB stick as the **mirror** - every closed file is copied to it, so the stick carried to the office is the record.
7. Enclosure: a sealed box with cable glands; a 7-10 inch HDMI panel in the lid, or the doghouse TV, for the monitor (lesson 26).
8. Network: the pad's switch or a cellular modem. The box serves one page on port 8780; nothing is opened inbound. Over a modem it joins the office through GEA-VPN (lesson 27).

## How much it writes
8 channels at 200 samples a second is 55 MB an hour; one file per channel per hour, each with a SHA-256 beside it and a manifest. It never deletes anything; under 500 MB free it stops recording and says so.

## You know it worked when
The box powers up on its battery, the GPS antenna has the sky, every sensor's cable comes through a gland to the ADC's inputs, and you have written down each sensor's input number and its feet east and north of the wellhead for the config (lesson 25).

Help: recorder
