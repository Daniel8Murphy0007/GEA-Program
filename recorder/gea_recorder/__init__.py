# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""gea_recorder - the black box: a field recorder for a drilling site, on a Raspberry Pi, feeding GEA-Program.

It records the ground channels (geophones, accelerometers, electrode dipoles) through a 24-bit ADC and the rig's
WITS0 feed, every sample on one GPS-disciplined clock, into hourly miniSEED files with a SHA-256 beside each and a
manifest GEA reads, and serves a status page on the pad network. It runs as a service with no screen, no keyboard
and nothing to type: power it, and it records. Standard library plus numpy; the hardware drivers (spidev, lgpio)
are imported only where the hardware is.
"""
__version__ = "0.78.1"
NAME = "GEA Recorder"
