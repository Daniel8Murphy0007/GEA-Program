# 27. The USB mirror, handing files to the office, and joining the VPN
Who: pad
Where: #/seismic
Takes: 15 minutes
> Two ways the office gets the record: the stick carried in, or the box pulled through the private network.

## The mirror
1. A USB stick mounted at the mirror path in the config (lesson 25) receives every closed file and the manifest as they close. Nothing is deleted from the box when it is copied.
2. To hand files over: wait for the top of the hour (the files close hourly), unmount the stick, carry it in. The office puts the stick's folder in the Field recorder card and presses **Pull** (lesson 17): every file is checked against its SHA-256; a file that was altered or truncated is named and not taken.
3. Put a second stick in; the box fills it from the files the first one had not yet copied.

## The network
4. On the pad's switch, the office pulls from the box at its address on port 8780 - nothing to do on the box.
5. Behind a cellular modem the box joins **GEA-VPN** (lesson 23): the office hands you `gea-vpn-pad-3-box.zip` by a channel of its own - it holds this box's key. Copy it to the Pi, unpack it, and once: `sudo sh vpn.sh`. It installs WireGuard, puts the box's configuration in place and starts the tunnel at boot. The box connects out to the hub; nothing is opened on the modem.
6. From then on the office pulls from the box at its private address (`http://10.77.0.3:8780`) and watches its monitor at `/live` there. On the box, `sudo wg show gea-vpn` prints the last handshake with the hub; a handshake under three minutes old means the tunnel is up.
7. A lost stick or a lost zip: tell the office. A stick is only copies; a zip is a key, and the office removes the member and makes a new one.

## You know it worked when
The office tells you the box's row on the GEA-VPN card reads *answers*, and the pull from the box's private address brought the morning's hours.

Help: recorder, vpn
