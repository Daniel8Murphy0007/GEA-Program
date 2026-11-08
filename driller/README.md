# GEA Driller - the directional driller's agent

A program for the rig PC, feeding GEA-Program. It reads the rig's own feed - the EDR's WITS0 over TCP or a serial
line: depths, rate, weight, speed, torque, flow, standpipe pressure, the MWD gamma, the survey - and the pre-drill
model of the target layer, and every 30 ft of new hole it recommends, in the directional driller's own units, what
to do next: build, drop or hold and by how much, where to aim the azimuth - along the plan or a few degrees left or
right of it - and the turn that asks (the two as one toolface and steering ratio for a rotary steerable system), the
weight on bit, the rotary speed, the flow rate, and whether to
drill on, circulate, or ream and wash first. The driller accepts, rejects, or records what was done instead; every
recommendation, every answer and every command is on record with its hash, and GEA pulls the record in.

Where the bit sits in the layer is estimated the way a geosteerer does it, by a position filter: a few hundred
guesses at how the real layer differs from the model - the top's offset, the dip's error along the plan and across
it, the thickness - moved every decision as undulation and faults would move them and kept to the ones that explain
the gamma and the deep-resistivity distance. The screen shows the band it puts the layer in and how sure it is; the
agent reads the same estimate.

The lateral has an azimuth (since 0.80.0). The bit is placed in the plan's frame - along the plan's azimuth, right
of the plan line, in depth - by minimum curvature through the surveys' azimuths and the feed's continuous azimuth;
the config's model carries the plan's azimuth, the layer's cross dip, the corridor the lease and the offset wells
leave the hole on each side of the plan line, and where the heel sits across it. The screen draws the plan view
under the section.

The vibration the agent reasons from (since 0.81.0) comes from the downhole tool's item when the EDR sends one, and
otherwise is read off the surface channels every EDR sends - the torque's, the weight's and the hook load's
oscillation over the last minute, scaled onto the index the agent was trained with. The screen and the record say
which it was; it is a surface proxy, calibrated on the twin, and labelled so.

The agent is a reinforcement-learning policy - a small neural network trained by proximal policy optimization in a
physics twin of the lateral: a target layer whose top is a surface rolling with its dip along the plan and across
it, undulation and faults with a strike; the gamma and the deep-resistivity boundary the near-bit tools read of it; a
steering tool with a tendency, a walk and a cap on the dogleg of the build and the turn together; the corridor, a
step outside of which costs and 150 ft outside of which loses the lateral; a bit whose
rate, wear and balling follow the weight, speed and flow; the hydraulics against the fracture gradient; the
cuttings bed and the stuck pipe it leads to; the hours each decision costs. Every training scene is drawn fresh
from the same ranges, so what the policy learns is the drilling, not a formation. It is judged on scenes it never
saw between a textbook driller written from the same observation - the bar - and an oracle told the twin's own
layer - the ceiling; the self-test refuses a policy that does not beat the textbook. `gea-driller evaluate` prints
the three rows and how far the agent stands from the one toward the other. The numbers of that judgement are a
simulation's and are labelled so.

**Authority.** Advisory by default: nothing goes to the rig. The closed loop exists as a design that ships
disconnected: `closed_loop.allowed` in the config puts the switch on the screen; an operator arms it by name for
one section of at most 500 ft; every decision then becomes a command through the downlink port - and this
version's only downlink is the null one, which keeps every command on record and sends none, because no vendor's
downlink protocol ships with it. The loop disarms itself when the feed goes stale, a limit is crossed, the section
is drilled, or the operator says so.

## Install, once

On a Linux rig PC: unpack `GEA-Driller-<version>.tar.gz`, `sudo sh install.sh`. On a Windows rig PC with Python
3.9 or newer: unpack it, double-click `install.cmd`. Either way: the package with the trained policy inside it, the
example config beside it, the data folder, the self-test, and the program set to start with the PC. Then edit the
config and restart it. The driller's screen is at port 8790 of the PC's address: `http://192.168.1.60:8790/live`
when that is its address.

### The config

- `rig`: `transport` tcp (the EDR's WITS0 `host` and `port`), serial (the COM port and baud), or `sim` - the twin as
  the rig, for the office; `items` maps a rig that numbers its WITS items differently (`"0824": "gamma"`).
- `model`: the pre-drill model from the offset wells - the target layer's top TVD at the heel, its thickness, its
  dip along the lateral, the gamma of the layer and of the rock above and below, whether a deep-resistivity tool is
  on the string, the lateral's length, and the heel's MD and TVD from the survey.
- `tool`: the steering tool's cap in °/100 ft and what it builds or drops on its own.
- `limits`: what no recommendation and no command may go outside - the dogleg, the vibration index, the fracture
  gradient and the mud weight, the motor's differential, and the ranges of WOB, rpm and flow.
- `decision`: a decision every 30 ft of new hole, and at least every 60 s while drilling.
- `closed_loop`: `allowed` (false: the switch is not on the screen at all), `downlink` (`null`), `max_section_ft`,
  `stale_s`.
- `output.dir`: `decisions.jsonl`, `commands.jsonl`, `surveys.csv`, each with its `.sha256`, and `manifest.json`.

## The screen

The section: the model's layer window in green and the hole drilled through it in blue from the surveys and the
continuous inclination, the bit, and where the recommendation points. The recommendation in words with its
reasons - what the deep-resistivity tool says, what the gamma says against the model's values, the model's
bracket, the limits - and the confidence of each choice. The rig's numbers against the limits. Accept, Reject,
or Did something else (with what); the operator's name goes on every answer. The closed-loop panel when the
config allows it. The last decisions. The same at `/live` from any browser on the rig network; the plain status at
`/`; `/health`, `/manifest.json`, `/decisions.jsonl`, `/commands.jsonl`, `/surveys.csv` for GEA.

## Commands

    gea-driller run --config /etc/gea-driller/config.json       advise until stopped (what the service runs)
    gea-driller sim --seed 7 --pace 3 --port 8790               the twin as the rig on this PC: the screen with a lateral drilling itself
    gea-driller train --seconds 600 --out policy.json           train a policy in the twin (resume one with --resume)
    gea-driller evaluate --scenes 20                            the policy against the textbook driller on held-out scenes, labelled SIMULATION
    gea-driller selftest                                        the twin, the policy, a short training, the service; exit 0 when right
    gea-driller status                                          what the running program says
    gea-driller config --write config.json                      the example config to edit

## Getting the records into GEA

On the rig network, GEA pulls them: the **Drill string** tab's card **GEA Driller**, the PC's address
(`http://192.168.1.60:8790`), **Pull**. GEA fetches the manifest, downloads the records it does not have, checks
each against its SHA-256, and keeps them under the well with the decisions, the answers and the commands
summarized. Over a cellular modem the rig PC joins GEA-VPN as a member of kind `driller` and GEA pulls from its
private address (`http://10.77.0.4:8790`). Before there is a rig, **Start the twin as the rig** on the same card
runs this program on the office PC with a scene drilling itself and opens the screen.
