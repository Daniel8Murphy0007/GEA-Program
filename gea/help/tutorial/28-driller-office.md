# 28. GEA Driller from the office
Who: office
Where: #/drillstring
Takes: 40 minutes
> The directional driller's agent: what it is, the twin as the rig on this computer, its screen, and its records pulled in.

## What it is
GEA Driller is a program for the rig PC. It reads the rig's own feed and the pre-drill model of the target layer and, every 30 ft of new hole, recommends in the driller's units what to do next: build, drop or hold and by how much (as toolface and steering ratio for a rotary steerable system), the weight on bit, the rotary speed, the flow rate, and whether to drill on, circulate, or ream and wash first. The driller accepts, rejects, or records what was done instead; every recommendation, answer and command is on record with its hash. The agent is a policy trained by reinforcement learning in a physics twin of the lateral - a layer that rolls with dip, undulation and faults; the gamma and the deep-resistivity boundary the tools read of it; the steering tool, the bit, the hydraulics, the hole - on thousands of scenes drawn fresh, judged on scenes it never saw against a textbook driller. Advisory by default; the closed loop ships as a design with a switch and a downlink that keeps every command and sends none.

## Steps
1. Open **Drill string**; the card **GEA Driller** is at the bottom. Press **Start the twin as the rig**: the driller's own program runs on this computer with a scene drilling itself under the agent, one step every three seconds. Press **Open its screen**.
2. Read the screen: the green band is the model's layer window, the blue line the hole from the surveys, the orange dot the bit, the dotted stub where the recommendation points, and the orange band where the position filter puts the layer from the readings - the tile beside it says how sure it is that the bit is in the layer and how far the top and the base are. The recommendation in words with its reasons - what the deep-resistivity tool says when the scene has one, the gamma against the model's values, the model's bracket, the limits - and the confidence of each choice. The rig's numbers against the limits. The decisions so far, and whether they were answered.
3. Type your name and press **Accept** on a recommendation; press **Reject** on the next; press **Did something else…** and record a build of +1.5°/100 ft at 25 klb on a third. Each answer is on the record with your name; the twin drills on under the agent.
4. The closed loop: **Arm for this section** to 300 ft further, confirm. The panel turns red: every decision is now a command, and the agent's panel says the null downlink kept it and sent nothing. **Disarm now**, or wait for the section's end - it disarms itself, and says why.
5. Back in GEA, choose the well the records go under and press **Pull from it**: a job fetches the records, checks each against its SHA-256 and keeps them under the well. The table says how many decisions, accepted, rejected, done otherwise, the hole under advice, the armed sections and the commands; the last decisions are listed below. **Stop it** closes the twin's records.
6. **Shadow a drilled well**: choose a well with a drilling-recorder export and press it. A job replays the drilled lateral through the advisor - at every 30 ft the agent sees only what the rig had shown to that point - and sets its recommendation beside what the record did next; the table says how often the steer agreed or opposed, the weight, speed and flow, and at each stretch the gamma marks as out of the layer whether the advisor called the way back at the first reading; the **SHADOW MODE** report under the well's reports has the drawing. Nothing of the export leaves this computer.
7. A real rig PC: its address in **The rig PC's address** (`http://192.168.1.60:8790`), the well, **Pull** - the same table. **Open the driller's screen** opens its screen from here. Over a modem, the rig PC joins GEA-VPN as a member of kind `driller` (lesson 23) and the address is its private one, `http://10.77.0.4:8790`.

## You know it worked when
The table shows your three answers under your name, one armed section with its commands and none sent, and the well's folder holds `driller\decisions.jsonl` with its `.sha256`.

## What the numbers are not
The twin's figures - the hole in the layer, feet a day - are a simulation's, labelled so on the screen and in the records; `gea-driller evaluate` on the rig PC prints the policy against the textbook driller on held-out scenes.

Help: driller, drillstring, vpn
