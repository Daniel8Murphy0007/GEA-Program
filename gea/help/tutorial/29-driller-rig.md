# 29. Install GEA Driller on the rig PC
Who: pad
Where: #/drillstring
Takes: 30 minutes
> One install, one config file, and the driller's screen on the rig.

## Before you start
The rig PC (the EDR's own, or the directional driller's laptop) on the rig network, with the EDR's WITS0 feed available on a TCP port or a serial line - ask the EDR vendor's hand for the port; it is read-only and nothing is ever sent to it. The kit `GEA-Driller-0.90.0.tar.gz` from the office's `installers\` folder (lesson 22) or the release page. On Windows, Python 3.9 or newer from python.org with "Add to PATH" ticked.

## Steps
1. Unpack the kit. On a Linux PC: `sudo sh install.sh`. On Windows: double-click `install.cmd`. Either makes a Python environment of its own with the package and the trained policy inside it, writes the example config beside it, makes the data folder, runs the self-test (a minute: the twin, the policy against the textbook driller, a short training that learns, the service), and sets the program to start with the PC.
2. Edit the config (`/etc/gea-driller/config.json` on Linux; `%LOCALAPPDATA%\GEA-Driller\config.json` on Windows): the site and the well as the office spells them; `rig.transport` `tcp` with the EDR's host and port (or `serial` with the COM port); the pre-drill model from the office - the target layer's top TVD at the heel, its thickness, its dip along the lateral and across it (positive deepening to the right of the plan line), the gamma of the layer and of the rock above and below, whether a deep-resistivity tool is on the string, the heel's MD and TVD from the survey, the plan's azimuth (or empty: the first survey's), the corridor the hole may run in each side of the plan line, where the heel sits across it; the tool's cap and tendency from the directional driller; the limits the well's program sets.
3. Restart it: `sudo systemctl restart gea-driller`, or double-click `start-gea-driller.cmd`. Open the screen from any browser on the rig network: port 8790 of the PC's address - `http://192.168.1.60:8790/live` when that is its address. The state reads **advising** once the feed is live; **waiting for the rig feed** means no frame has come - check the EDR's port.
4. Keep the surveys current: the feed's record 7 brings them when the EDR sends it; otherwise the directional driller keeps `surveys.csv` in the data folder - `md, inclination, azimuth` per station - and the program reads it. The hole on the screen - the section and the plan view - is projected from them and from the feed's continuous inclination and azimuth (WITS items 0831 and 0832); a station without an azimuth is taken along the plan.
5. The directional driller works the screen: each recommendation accepted, rejected, or recorded as done otherwise, with their name. Tell the office the PC's address; GEA pulls the records from it (lesson 28). Over a modem, the office hands you `gea-vpn-rig-pc.zip` and the PC joins GEA-VPN like a box (lesson 27).
6. The closed loop stays off unless the office sets `closed_loop.allowed` in the config - and even then this version's downlink keeps every command and sends none: there is no vendor protocol in it. Nothing in this program can move the tool.

## You know it worked when
The screen reads **advising**, the hole depth on it matches the EDR's, the gamma tile moves, the vibration tile reads a number with *from surface data* under it (or *downhole tool* when the string has one and the EDR sends its item), and a recommendation appears within a minute of drilling.

Help: driller, recorder
