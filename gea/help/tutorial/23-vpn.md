# 23. GEA-VPN: the programs on separate computers
Who: office
Where: #/admin
Takes: 45 minutes
> The control panel, each pad's recorder, the operator's window and the client's reader run on separate computers in separate places; GEA-VPN joins them into one private network over the internet.

## What you need first
One **hub**: a small cloud server of the site's own - any Debian or Ubuntu machine with a public address; the smallest rented virtual server is enough - with UDP port 51820 reachable. Every member connects out to it; it forwards between the office and each member, and members never see one another. WireGuard on the office PC, from wireguard.com (installed once, with administrator rights). GEA makes every key and writes every configuration; nothing is typed on a pad.

## Steps
1. **Administration**, the **GEA-VPN** card. Enter the hub's public name or address (`vpn.pad3site.com` or `203.0.113.10`) and press **Make the network**. The office member - this control panel, at `10.77.0.2` - is made with it.
2. Download **gea-vpn-hub.zip**, copy it to the hub and run there, once: `sudo sh install-hub.sh`. It installs WireGuard, opens the port, turns forwarding on and starts the tunnel at boot.
3. On this PC: open WireGuard, **Import tunnel(s) from file**, choose `gea-vpn.conf` from **gea-vpn-office.zip**, **Activate**. The card's pill turns to **tunnel up**.
4. Add a member per computer: its name (`Pad 3 box`) and its kind - **box** for a recorder (reaches the office only), **operator** or **reader** for a window (reaches the office's control panel only), **office** for a second control panel - then **Add the member**. Its address and its zip appear in the table. Run the hub installer again - the hub's folder is written anew with every member.
5. Hand each member its zip by a channel of its own - in person, or not by the same e-mail as anything else: it holds that computer's key. A box: `sudo sh vpn.sh` on the Pi, once (lesson 27). Every other member installs **GEA-VPN** once (`GEA-VPN-Setup-0.91.0.exe` on Windows, `GEA-VPN-0.91.0.tar.gz` on Linux, from the release or the office's `installers\` folder), opens it from the Start menu, drops its zip on the page and presses **Connect** - the page reads *connected* when the hub has answered, and says whether this office answers through the tunnel. (A Windows member without GEA-VPN imports its `gea-vpn.conf` into WireGuard by hand.)
6. The table shows each member's **last handshake** (within two or three minutes means its tunnel is up) and whether its program **answers** through the tunnel. Pull from a box at its private address: `http://10.77.0.3:8780`.
7. A lost file, or a computer that leaves: **Remove** the member and run the hub installer again; add it again for a new key. The keys are in `keys\vpn\network.json`, owner-only - the backup carries it, the dossier never does.

## You know it worked when
The box's row reads a handshake a minute old and *answers*, and the Field recorder card pulls from `http://10.77.0.3:8780` from the office.

Help: vpn, recorder
