#!/bin/sh
# GEA Recorder - install on a Raspberry Pi (Raspberry Pi OS, Bookworm or later), as root:  sudo sh install.sh
# What it does: the Python package into /opt/gea-recorder (its own virtual environment, nothing else touched),
# the example config at /etc/gea-recorder/config.json (left alone when one is there), the data folder, SPI on,
# and the service enabled so the box records from the next boot. The GPS/PPS lines for chrony are printed, not
# applied: they depend on which GPIO the receiver's PPS pin is on (README.md).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=/opt/gea-recorder
echo "== GEA Recorder: installing into $PREFIX"
apt-get install -y python3-venv python3-dev chrony >/dev/null 2>&1 || true
python3 -m venv "$PREFIX"
"$PREFIX/bin/pip" install --upgrade pip >/dev/null
"$PREFIX/bin/pip" install "$HERE"/gea_recorder-*.whl 2>/dev/null || "$PREFIX/bin/pip" install "$HERE"
"$PREFIX/bin/pip" install spidev lgpio pyserial 2>/dev/null || echo "   (spidev/lgpio/pyserial not installed: the sim and csv sources still work; the ADS1256 needs them)"
if command -v raspi-config >/dev/null 2>&1; then raspi-config nonint do_spi 0 || true; echo "   SPI enabled"; fi
mkdir -p /var/lib/gea-recorder /etc/gea-recorder
id -u gea >/dev/null 2>&1 || useradd --system --home /var/lib/gea-recorder --groups spi,gpio,dialout gea 2>/dev/null || useradd --system --home /var/lib/gea-recorder gea
chown -R gea:gea /var/lib/gea-recorder
"$PREFIX/bin/python" -m gea_recorder install --config /etc/gea-recorder/config.json --user gea --datadir /var/lib/gea-recorder
"$PREFIX/bin/python" -m gea_recorder selftest --seconds 10
cat <<'EOT'

== done. Next:
   1. edit /etc/gea-recorder/config.json: the site, the wellhead position, each channel's input, id and place in feet
      from the wellhead, the ADC driver (ads1256 for the HAT), the rig feed; then: sudo systemctl restart gea-recorder
   2. the GPS clock (README.md): wire the receiver's PPS to a GPIO, then in /boot/firmware/config.txt
         dtoverlay=pps-gpio,gpiopin=18
      and in /etc/chrony/chrony.conf
         refclock SHM 0 refid GPS precision 1e-1 offset 0.0 delay 0.2
         refclock PPS /dev/pps0 refid PPS lock GPS
      with gpsd reading the receiver's serial line; reboot; `chronyc tracking` says PPS
   3. the status page: http://<this box>:8780/
EOT
