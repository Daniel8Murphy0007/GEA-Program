#!/bin/sh
# GEA Driller - install on a Linux rig PC (Debian or Ubuntu), as root:  sudo sh install.sh
# What it does: the Python package with the trained policy into /opt/gea-driller (its own virtual environment,
# nothing else touched), the example config at /etc/gea-driller/config.json (left alone when one is there), the
# data folder, and the service enabled so the agent runs from the next boot. Advisory by default: the closed loop
# is off until closed_loop.allowed is set in the config, and no command is ever sent by this version's downlink.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=/opt/gea-driller
echo "== GEA Driller: installing into $PREFIX"
apt-get install -y python3-venv python3-dev >/dev/null 2>&1 || true
python3 -m venv "$PREFIX"
"$PREFIX/bin/pip" install --upgrade pip >/dev/null
"$PREFIX/bin/pip" install "$HERE"/gea_driller-*.whl 2>/dev/null || "$PREFIX/bin/pip" install "$HERE"
"$PREFIX/bin/pip" install pyserial 2>/dev/null || echo "   (pyserial not installed: a TCP feed and the twin still work; a serial feed needs it)"
mkdir -p /var/lib/gea-driller/data /etc/gea-driller
[ -f /etc/gea-driller/config.json ] || "$PREFIX/bin/python" -m gea_driller config --write /etc/gea-driller/config.json
id -u gea >/dev/null 2>&1 || useradd --system --home /var/lib/gea-driller --groups dialout gea 2>/dev/null || useradd --system --home /var/lib/gea-driller gea
chown -R gea:gea /var/lib/gea-driller
"$PREFIX/bin/python" -m gea_driller selftest --seconds 8
"$PREFIX/bin/python" -m gea_driller install --config /etc/gea-driller/config.json --user gea
cat <<'EOT'

== done. Next:
   1. edit /etc/gea-driller/config.json: the well and the site, the rig feed (tcp: the EDR's WITS0 host and port; serial: the port),
      the pre-drill model (the target layer's top at the heel, its thickness and dip, the gamma in, above and below, the heel's MD and TVD),
      the tool's cap and tendency, the limits; then: sudo systemctl restart gea-driller
   2. the driller's screen is at port 8790 of this PC's address: http://192.168.1.60:8790/live when that is its address
   3. GEA pulls the decisions from the same address (the Drill string tab, GEA Driller); over a modem, through GEA-VPN (member kind: driller)
EOT
