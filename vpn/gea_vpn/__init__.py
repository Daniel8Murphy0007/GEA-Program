# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""GEA-VPN - the client program that joins a computer to a site's private network. Installed once on an office PC,
a rig PC or a window's computer, it waits for the connect information the office makes in GEA-Program (the member's
zip), brings the WireGuard tunnel up from it, keeps it up from boot, and shows on a page of its own whether the hub
and the office answer. Standard library only; WireGuard does the tunnel."""
NAME = "GEA-VPN"
__version__ = "0.83.0"
