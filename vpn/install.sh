#!/bin/sh
# GEA-VPN - install on a Linux computer (Debian or Ubuntu: a rig PC, a window's PC, a box), as root:  sudo sh install.sh
# What it does: WireGuard from the distribution, the program into /opt/gea-vpn (its own virtual environment, nothing
# else touched), the data folder /var/lib/gea-vpn (root only), the service gea-vpn enabled so the page is there from
# the next boot at http://127.0.0.1:8795/ - ready for the connect information the office makes in GEA-Program.
# Nothing connects until that information is given to the page (or: gea-vpn connect gea-vpn-<member>.zip).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX=/opt/gea-vpn
echo "== GEA-VPN: installing into $PREFIX"
apt-get install -y wireguard python3-venv >/dev/null 2>&1 || echo "   (apt-get could not install wireguard/python3-venv: install them by hand)"
python3 -m venv "$PREFIX"
"$PREFIX/bin/pip" install --upgrade pip >/dev/null
"$PREFIX/bin/pip" install "$HERE"/gea_vpn-*.whl 2>/dev/null || "$PREFIX/bin/pip" install "$HERE"
mkdir -p /var/lib/gea-vpn && chmod 700 /var/lib/gea-vpn
"$PREFIX/bin/python" -m gea_vpn selftest
"$PREFIX/bin/python" -m gea_vpn install --data /var/lib/gea-vpn --port 8795
cat <<'EOT'

== done. Next:
   1. the office makes this computer's connect information in GEA-Program (Administration, GEA-VPN, add a member, its zip)
   2. open http://127.0.0.1:8795/ on this computer and drop the zip on the page - or:  sudo /opt/gea-vpn/bin/gea-vpn connect gea-vpn-<member>.zip
   3. the page says when the hub answers and when the office does; the tunnel is up from every boot after that
EOT
