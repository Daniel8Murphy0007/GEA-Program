# GEA-VPN - the client of the site's private network

A program for every computer that joins a site's private network but the hub: the rig PC, a window's PC, the
office's second computer, a box without the recorder's own installer. Installed once, it waits - **ready for the
connect information** the office makes in GEA-Program (Administration, the GEA-VPN card: add the member, download
its zip). Given that zip on its page (dropped on it, chosen, or the text of `gea-vpn.conf` pasted), it brings the
WireGuard tunnel up now and at every start of the computer, and from then on says how it is doing: whether the
hub answered and when, whether the office answers through the tunnel. Nothing is typed on the computer but a name.

The tunnel is WireGuard's - the VPN in every Linux kernel, and WireGuard for Windows from wireguard.com on Windows.
GEA-VPN drives it and reports what it says; it adds no cryptography of its own. The connect information holds the
computer's own key: the page keeps it owner-only under the program's data folder, and shows the public key only.

## Install, once

**Windows**: `GEA-VPN-Setup-<version>.exe` from the release (administrator rights, once: the tunnel is a Windows
service). It installs WireGuard for Windows when the computer has none (the official installer travels inside the
setup), puts the program under Program Files, and starts it as a service of its own (the scheduled task `GEA-VPN`,
as SYSTEM, at every start). The Start menu's **GEA-VPN** opens the page at `http://127.0.0.1:8795/`.

**Linux** (Debian or Ubuntu): unpack `GEA-VPN-<version>.tar.gz`, `sudo sh install.sh`. WireGuard from the
distribution, the program under `/opt/gea-vpn`, the service `gea-vpn` under systemd, the page at the same address.

## Then

1. The office adds this computer as a member in GEA-Program and hands over its zip - by a channel of its own,
   never with anything else by the same channel: it holds the key.
2. Open the page, drop the zip on it, press **Connect**. The pill reads *connected* when the hub has answered;
   the office's tile says *answers* when the control panel is reachable through the tunnel.
3. Nothing more. The tunnel comes up with the computer from then on. **Disconnect and forget** removes the
   connect information from the computer; the office removes the member and makes a new zip for a new one.

From a terminal: `gea-vpn status`, `gea-vpn connect gea-vpn-<member>.zip`, `gea-vpn disconnect`, `gea-vpn forget`,
`gea-vpn selftest`.

## What it keeps

Under `%ProgramData%\GEA-VPN` (Windows, administrators only) or `/var/lib/gea-vpn` (Linux, root only):
`gea-vpn.conf` (the connect information as given, owner-only), `state.json` (what it says of the member - no key),
`log.txt` (what was done, by whom, when). The page listens on 127.0.0.1 only and answers its own requests only.

Licence: Mozilla Public License 2.0. Part of GEA-Program: https://github.com/Daniel8Murphy0007/GEA-Program
