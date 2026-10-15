# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""tool_taxonomy - the downhole tools in use across the industry, by class, each with a tutorial.

The gauge library (`tool_library`) holds the instruments this program reads and simulates. This
module holds the rest of the hole: every class of downhole tool a rig, a completion crew, a logging
unit or a fishing hand runs - bits, motors and steerable systems, MWD and LWD, the drill string and
its subs, casing and cementing equipment, completion and artificial-lift hardware, wireline and
cased-hole tools, fishing and milling tools, test tools.

Each class carries:
  * a one-line description (the hover),
  * a tutorial in six parts (the pop-open): what it is, where it runs, how it works, what it shows
    in the data, what to watch, and what this program does with it,
  * the public pages it was checked against (`reference/tool_sources.json`), each fetched on the
    date recorded there,
  * published figures ONLY as the cited page states them: the quote, the unit, the page. A figure
    is the page's own - usually one maker's product - and never a limit for the class. The tutorial
    prose carries no numbers at all; every number on a tool's card is a quoted, cited figure.

The operator's own tools - make, model, serial, the datasheet that rates them - are the FLEET:
held in the workspace (`config/fleet.json`), each entry tied to one class here and to the datasheet
that rates it. The library does not invent a rating for a model.

Headless-safe: stdlib only.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

SOURCES_FILE = os.path.join(os.path.dirname(__file__), 'reference', 'tool_sources.json')
SOURCES_FETCHED = '2026-10-14'

CATEGORIES = [
    ('bits', 'Bits and hole enlargement'),
    ('drive', 'Steering and drive'),
    ('measure', 'Measurement and telemetry while drilling'),
    ('bha', 'Drill string and BHA components'),
    ('casing', 'Casing and cementing equipment'),
    ('completion', 'Completion and artificial lift'),
    ('logging', 'Wireline and cased-hole logging'),
    ('fishing', 'Fishing, milling and intervention'),
    ('testing', 'Well testing'),
]
CATEGORY_NAMES = dict(CATEGORIES)

PARTS = ('what', 'where', 'how', 'reads', 'watch', 'program')
PART_TITLES = {'what': 'What it is', 'where': 'Where it runs', 'how': 'How it works',
               'reads': 'What it shows in the data', 'watch': 'What to watch', 'program': 'In this program'}

# what the program does with a class, by category, unless the class says more
_PROGRAM = {
    'bits': "The program does not model the bit. Its run shows in a drilling record (an EDR export): rate of penetration, weight on bit, rotary speed, torque and the hours on bottom, section by section. Put the model in the fleet with its datasheet to keep the record of what was run.",
    'drive': "The program does not model drive or steering tools. Their work shows in the drilling record - the recorder's TVD against hole depth, the rotating and on-bottom hours, the differential pressure - and in the directional survey the survey command reads.",
    'measure': "The program reads what these tools deliver when it arrives as a file: LAS, DLIS or WITSML logs, the recorder's gamma in an EDR export (written as a gamma log), surveys. It does not decode mud-pulse or electromagnetic telemetry itself.",
    'bha': "The program does not model the drill string. Its behaviour shows in the drilling record - hook load, torque, standpipe pressure, connections - and the fleet holds the components you run, each with its datasheet.",
    'casing': "The program does not model casing or cementing equipment. The fleet records what was run, with the datasheet that rates it; the depths it was run to belong in the well's declarations.",
    'completion': "Where a completion carries a gauge, the program reads it: the permanent gauge streams, their drift and their well tests are the program's core. Other completion hardware is recorded in the fleet with its datasheet.",
    'logging': "The program reads the logs these tools produce - LAS and DLIS, gamma first - into the strata survey, and says what a log lacks. It does not process raw tool data.",
    'fishing': "The program does not model fishing or milling. A fishing job shows in the drilling record as overpull on hook load, torque and time off bottom; the fleet holds the tools with their datasheets.",
    'testing': "The program's well-test detector reads pressure build-ups and drawdowns from gauge data; a drill stem test's own recorder data can be read the same way when it arrives as a file.",
}

# id, category, name, one-line (the hover), what, where, how, reads, watch[, program]
_T = [
    # -- bits and hole enlargement ----------------------------------------------------------------------------------
    ('pdc_bit', 'bits', 'PDC bit (polycrystalline diamond compact)',
     'A fixed-cutter bit whose synthetic-diamond cutters shear rock; the workhorse of most shale and lateral sections.',
     "A drill bit with no moving parts: steel or matrix blades carry polycrystalline diamond compact cutters that shear the rock rather than crush it.",
     "At the bottom of the string, below the motor or rotary steerable system, from below surface casing to total depth in most land wells.",
     "Weight on bit pushes the cutters into the rock and rotation drags them across it, peeling off a continuous chip. Mud through the nozzles cools the cutters and clears the cuttings.",
     "Rate of penetration rising with weight and rotary speed until it stops responding; torque that follows weight closely; a smooth standpipe pressure.",
     "Torque swinging with rotary speed (stick-slip), rate of penetration falling at the same weight (dull or balled cutters), and a sudden torque spike in hard stringers - each is a reason to change parameters before the cutters chip."),
    ('roller_cone_bit', 'bits', 'Roller-cone (tricone) bit',
     'A bit with toothed cones that roll on bearings and crush rock; milled-tooth or carbide-insert.',
     "A bit with conical cutters on bearings that roll across the hole bottom as the string turns; the teeth are milled steel or tungsten-carbide inserts.",
     "At the bottom of the string, mostly in top holes, hard or broken rock, and where a fixed-cutter bit would be damaged.",
     "The cones roll and the teeth crush and gouge the rock under weight; jets in the bit body sweep cuttings away from the cones.",
     "Rate of penetration that responds to weight more than to rotary speed; a torque trace that is rougher than a PDC run.",
     "Torque rising with no change in weight can mean a locked cone or a bearing going; bearings have a life measured in revolutions, so long runs are counted, not guessed."),
    ('hybrid_bit', 'bits', 'Hybrid bit (PDC and roller cone)',
     'One bit carrying both roller cones and PDC blades: the cones crush while the blades shear.',
     "A bit that puts roller cones and fixed PDC blades on one body, so the cones crush and the blades shear the same hole.",
     "In interbedded and hard formations where a PDC bit alone suffers impact damage and a roller-cone bit alone drills slowly.",
     "The cones take the impact and stabilise the bit; the blades cut between them. The mix is meant to steady torque.",
     "Torque that is steadier than a PDC bit in the same rock, with a rate of penetration between the two bit types.",
     "Cone bearing life still applies; watch torque for the signs of a locked cone as on any roller-cone bit."),
    ('impregnated_bit', 'bits', 'Diamond-impregnated bit',
     'A bit whose matrix carries fine diamonds through its body; it grinds very hard, abrasive rock and wears to expose fresh diamond.',
     "A bit whose cutting structure is a matrix with diamond grit mixed through it; as the matrix wears, new diamond is exposed.",
     "In the hardest, most abrasive formations, usually under a high-speed motor or turbodrill.",
     "High rotary speed grinds the rock with the exposed diamond; the matrix is designed to wear at the pace the diamond dulls.",
     "Low rate of penetration at high bit speed, with a long run on bottom.",
     "Too little weight polishes the face and stops it cutting; too much burns it. Rate of penetration falling at constant parameters is the first sign."),
    ('bicenter_bit', 'bits', 'Bi-center bit',
     'A bit with an off-center reaming section: it passes through casing and drills a hole larger than its pass-through size.',
     "A one-piece tool combining a pilot bit with an eccentric reaming section, so the hole drilled is larger than the hole it passed through.",
     "Below a casing shoe where a larger hole is wanted than the casing drift allows - for more cement around the next string or to run a larger liner.",
     "The pilot section keeps the bit centred while the eccentric wing cuts the outer diameter as the string rotates.",
     "Torque and vibration that are typically rougher than a concentric bit; a rate of penetration similar to a PDC bit.",
     "Lateral vibration and whirl are the known problem; a caliper log afterwards says whether the full hole size was cut."),
    ('core_barrel', 'bits', 'Core barrel and coring bit',
     'A ring-shaped bit and a barrel above it that cut and keep a column of rock for the lab.',
     "A coring bit with a hole through its centre, and the barrel above it: an inner tube that receives and holds the core inside an outer tube that carries the load.",
     "Run in place of the drilling bit over the interval to be cored, then pulled to recover the core at surface.",
     "The bit cuts an annulus and the core rises into the inner barrel; a core catcher at the bottom holds it when the barrel is lifted.",
     "A controlled, steady rate of penetration and low weight; the cored footage in the drilling record matches the core recovered at surface.",
     "Jamming - torque up and rate of penetration stopping - means the core has stopped entering the barrel; continuing grinds it. Trip speeds on the way out are slowed to avoid damaging the core."),
    ('hole_opener', 'bits', 'Hole opener',
     'A fixed-diameter tool that enlarges an existing hole behind a pilot bit.',
     "A tool with fixed arms or blades carrying cutters, run to enlarge a pilot hole to a larger diameter no greater than the drift of the casing above it.",
     "Above a pilot bit or bullnose, opening a hole drilled earlier, or opening while drilling.",
     "The cutters on the arms cut the wall out to the tool's diameter as the string rotates and is lowered.",
     "Added torque over the pilot assembly alone; a slower rate of penetration than drilling the pilot hole.",
     "Its diameter is fixed, so it cannot pass a restriction smaller than itself - unlike an underreamer."),
    ('underreamer', 'bits', 'Expandable underreamer',
     'Cutter arms that open downhole on command to drill a hole larger than the restriction they were run through.',
     "A tool whose cutter arms stay closed while running in and open downhole, so it drills a hole larger than the casing or restriction above.",
     "In the BHA above the bit, often above the MWD; opened where the enlarged section begins.",
     "Pump pressure or a dropped ball opens the arms; with pumps off they close so the tool can be pulled through the casing.",
     "A change in standpipe pressure when the arms open (a signature the operator watches for), and added torque while reaming.",
     "Confirming the arms opened matters - a pressure signature, a pull test against the shoe, or a caliper. Arms that do not close will hang up at the shoe on the way out."),
    ('roller_reamer', 'bits', 'Roller reamer',
     'A string tool with rollers that ream the hole to gauge and steady the string, reducing torque in hard rock.',
     "A tool in the string with sets of hardened rollers on its body that ream the wall to gauge and stabilise the string.",
     "In the BHA near the bit, in hard or abrasive rock where a blade stabiliser wears quickly, and in long reach wells.",
     "The rollers turn against the wall, cutting ledges and keeping the hole at size with less torque than a fixed blade.",
     "Lower torque and less stick-slip than a blade stabiliser in the same place.",
     "Roller bearings wear; a reamer pulled under gauge has stopped doing its job."),
    # -- steering and drive -----------------------------------------------------------------------------------------
    ('pdm_motor', 'drive', 'Positive displacement mud motor (PDM)',
     'A downhole motor driven by the mud: a spiral rotor in a lobed stator turns the bit without turning the pipe.',
     "A downhole motor whose power section - a helical rotor inside an elastomer stator with one more lobe - is turned by the drilling fluid, driving the bit.",
     "Directly above the bit (or the bit and a near-bit tool), below the MWD, with a bent housing for steering.",
     "Mud forced through the cavities between rotor and stator turns the rotor; bit speed follows flow rate and torque follows the pressure drop across the motor.",
     "Differential pressure (standpipe pressure on bottom minus off bottom) tracks motor torque; sliding shows as on-bottom hours with no rotary speed; rotating adds the string speed to the motor's.",
     "A sudden jump in differential pressure with rate of penetration stopping is a stall; repeated stalls damage the stator. Rising pressure with no weight can mean a chunked stator or a plugged bit."),
    ('turbodrill', 'drive', 'Turbodrill',
     'A downhole motor turned by mud flowing through turbine stages: high speed, low torque.',
     "A motor with many stages of turbine blades - a stator and rotor pair per stage - that turns the bit when mud flows through it.",
     "Above the bit, often with impregnated bits in very hard rock, and in through-tubing work.",
     "Mud flowing past the angled blades of each stage spins the rotor shaft; speed rises with flow and falls with load.",
     "High bit speed; standpipe pressure that changes little with load compared with a PDM.",
     "Speed is not shown at surface; a turbine stalls with little warning in the pressure trace, so weight is applied gently."),
    ('rss_push', 'drive', 'Rotary steerable system, push-the-bit',
     'Steers while the whole string rotates, by pushing pads against the wall to side-load the bit.',
     "A steering tool that lets the whole string rotate while drilling a curve: pads on the tool push against the borehole wall to push the bit sideways.",
     "Directly above the bit, below the MWD and LWD.",
     "The pads extend in turn as the tool rotates, always on the side opposite the wanted direction; downlinked commands set the direction and force.",
     "Continuous rotation (no sliding hours) through the curve; downlinks show as deliberate flow or rotary speed changes; the recorder's TVD follows the plan smoothly.",
     "Pad wear in abrasive rock, and soft formations where the pads have little to push against."),
    ('rss_point', 'drive', 'Rotary steerable system, point-the-bit',
     'Steers while the whole string rotates, by tilting the bit axis inside the tool.',
     "A steering tool that tilts the bit's axis relative to the tool body, pointing the bit in the wanted direction while the string rotates.",
     "Directly above the bit, below the MWD and LWD.",
     "An internal mechanism bends or offsets the drive shaft so the bit points off the tool axis; the offset is held steady in space as the outer body rotates.",
     "Continuous rotation through the curve; a smoother hole with less spiralling than sliding; downlinks as deliberate flow or rotary speed changes.",
     "Less dependent on the formation for steering than push-the-bit systems; watch the build and turn rates against the plan in each survey."),
    ('bent_housing', 'drive', 'Bent housing and bent sub',
     'A small fixed bend in the motor (or a bent sub above it) that points the bit when the string is not rotating.',
     "A housing in the motor - or a short sub above it - made with a slight bend, so the bit points off the axis of the string.",
     "In the motor assembly between the power section and the bearing section, or above the motor.",
     "Sliding with the bend oriented in one direction builds or turns the hole; rotating the string averages the bend out and drills ahead.",
     "Sliding shows as on-bottom hours with rotary speed at zero and a toolface held by the driller; rotating sections in between.",
     "A larger bend builds faster but stresses the string when rotated; drag while sliding limits how far a bend can steer in a long lateral."),
    ('whipstock', 'drive', 'Whipstock',
     'A hardened wedge set in the hole that deflects a mill or bit to cut a window and start a sidetrack.',
     "A wedge-shaped steel ramp set in the casing or open hole that deflects the mill or bit to one side.",
     "Anchored at the kick-off depth, oriented with a survey tool, ahead of a sidetrack or a lateral.",
     "Milling assemblies slide down the face, cut a window through the casing wall, and drill a short rathole beyond it.",
     "A milling run: low rate of penetration, high torque, metal returns at the shakers; then a new hole leaving the old one.",
     "Orientation must be right before the window is cut; an anchor that slips spoils the window."),
    ('agitator', 'drive', 'Agitator (friction reduction / axial oscillation tool)',
     'A tool that sends axial vibrations along the string to break static friction, so weight reaches the bit in long laterals.',
     "A tool that makes the drill string oscillate along its axis, cutting the friction between the pipe and the hole wall.",
     "In the string above the BHA, usually with a shock tool to turn pressure pulses into axial movement; sometimes two tools in very long laterals.",
     "A valve driven by the mud flow opens and closes in a cycle, creating pressure pulses that a shock tool turns into a push-pull motion of the string.",
     "A steady oscillation in standpipe pressure while it runs; better weight transfer and toolface control while sliding.",
     "It adds pressure drop; its effect fades with distance from the tool, and it shakes the MWD - another reason the recorder's channels must be read for what they are."),
    # -- measurement and telemetry while drilling -------------------------------------------------------------------
    ('mwd_mud_pulse', 'measure', 'MWD with mud-pulse telemetry',
     'Measures direction, inclination and drilling data downhole and sends it to surface as pressure pulses in the mud.',
     "A measurement-while-drilling tool: sensors for inclination, azimuth and toolface, often gamma and vibration, and a pulser that sends their readings to surface through the mud column.",
     "In the BHA above the motor or rotary steerable system, inside a nonmagnetic collar to keep the magnetometers clear of steel.",
     "The pulser restricts the flow (positive pulse), vents it to the annulus (negative pulse), or modulates a continuous wave; surface transducers decode the pressure signal.",
     "Surveys at connections; a toolface stream while sliding; noise in the standpipe pressure where the pulses ride. The recorder's gamma channel often comes from this tool.",
     "Pump noise, motor stalls and high flow rates can drown the signal; surveys taken while the pipe moves, or near steel, are suspect."),
    ('mwd_em', 'measure', 'MWD with electromagnetic telemetry',
     'Sends downhole measurements through the earth as low-frequency electromagnetic signals instead of mud pulses.',
     "An MWD tool that transmits by injecting a low-frequency current across an insulated gap in the string; a surface antenna picks up the signal from the ground.",
     "In the BHA like a mud-pulse tool, with a gap sub; common on land wells and where there is no continuous mud column, such as air or underbalanced drilling.",
     "The tool modulates a current across the gap; the signal travels through the formation and is measured between the wellhead and a ground stake.",
     "Surveys and toolface delivered without needing the pumps on; data rates that drop as depth increases.",
     "Signal is lost in very conductive or very resistive formations and at depth; surface electrical noise interferes."),
    ('wired_pipe', 'measure', 'Wired drill pipe',
     'Drill pipe with a conductor and inductive couplers in every joint: a high-speed data network along the string.',
     "Drill pipe with a cable through each joint and inductive couplers in the connections, forming a network from the BHA to surface.",
     "The whole string, with booster subs at intervals to keep the signal up.",
     "Data passes joint to joint through the couplers; sensors can be placed along the string, not only at the bottom.",
     "Downhole data at rates far above mud pulse, and along-string measurements of pressure and dynamics.",
     "A failed joint breaks the network until it is found; the string must be handled to keep the couplers clean."),
    ('gyro_mwd', 'measure', 'Gyro survey tool (gyro MWD and gyro multishot)',
     'Finds direction from the earth\'s rotation, not its magnetic field: works inside casing and near steel.',
     "A survey tool that measures direction with gyroscopes sensing the earth's rotation, instead of magnetometers sensing its magnetic field.",
     "Run inside casing, near other wells, and at shallow depths where steel would spoil a magnetic survey; as an MWD it rides in the BHA.",
     "The gyro senses the horizontal component of the earth's rotation to find north; accelerometers give inclination.",
     "Survey stations with azimuth that does not depend on nearby steel.",
     "Gyro azimuth weakens at high latitude and near vertical; drift corrections and station times matter."),
    ('magnetic_survey', 'measure', 'Magnetic single-shot and multishot survey tool',
     'Records inclination and magnetic direction at one station, or many, on the way out of the hole.',
     "A survey instrument that records inclination and magnetic azimuth - once (single-shot) or at many stations (multishot) - in its own memory.",
     "Dropped or run on wireline into a nonmagnetic collar, or pulled out with the string recording at stations.",
     "Accelerometers or a pendulum give inclination; magnetometers or a compass give azimuth relative to magnetic north.",
     "A survey listing: measured depth, inclination, azimuth.",
     "It needs a nonmagnetic collar long enough to clear the steel around it; magnetic declination must be applied to get grid or true north."),
    ('lwd_gamma', 'measure', 'LWD gamma ray',
     'Counts natural gamma rays from the rock while drilling; shale reads high, clean sand and carbonate read low.',
     "A logging-while-drilling sensor that counts the natural gamma radiation of the formation; total gamma, sometimes spectral or azimuthal.",
     "In the BHA, often in the MWD collar, some distance behind the bit.",
     "Scintillation or Geiger counters register gamma rays from potassium, thorium and uranium in the rock.",
     "A gamma curve against depth; in an EDR export it is the recorder's gamma channel, shown only while the sensor reports.",
     "The reading is indexed to the sensor, which sits behind the bit: plotted against bit depth it is shallow by that offset. Azimuthal gamma up and down tells a geosteerer whether the bit is leaving the bed through its top or bottom.",
     "The drilling record writes the recorder's gamma as a one-foot LAS log by bit depth and runs the lithology-from-gamma index on it; the survey command reads that log. Both say the depth is bit depth."),
    ('lwd_resistivity', 'measure', 'LWD propagation resistivity',
     'Measures how the rock attenuates and phase-shifts an electromagnetic wave: resistivity at several depths of investigation.',
     "An LWD sensor that transmits electromagnetic waves and measures their phase shift and attenuation between receivers, from which formation resistivity is computed.",
     "In the BHA close behind the bit, so the reading comes before mud filtrate invades far.",
     "Transmitters at several spacings and frequencies send waves through the formation; different spacings read different depths into the rock.",
     "Several resistivity curves of different depth; hydrocarbon zones read high, water-bearing ones low.",
     "Curves separating can mean invasion, anisotropy or a nearby bed boundary; the tool's own corrections matter."),
    ('lwd_density_neutron', 'measure', 'LWD density and neutron porosity',
     'Radioactive-source measurements of bulk density and hydrogen index while drilling: the porosity pair.',
     "LWD sensors that measure formation bulk density (from scattered gamma rays) and hydrogen index (from neutrons), the standard porosity pair.",
     "In the BHA, usually above the resistivity tool; the sources are loaded at surface and handled under licence.",
     "A gamma source and detectors measure density from Compton scattering; a neutron source and detectors measure how quickly neutrons slow, which hydrogen controls.",
     "Density and neutron porosity curves, often with an image of density around the hole.",
     "Readings depend on standoff from the wall, so rotation and hole washouts matter. A stuck tool with sources is a regulated fishing job."),
    ('lwd_sonic', 'measure', 'LWD acoustic (sonic)',
     'Times sound through the rock while drilling: compressional and shear slowness for porosity and mechanics.',
     "An LWD tool with an acoustic transmitter and an array of receivers that measures how fast sound travels through the formation.",
     "In the BHA, isolated from drilling noise as far as the design allows.",
     "The transmitter fires a pulse; the arrival times across the receiver array give the formation's slowness.",
     "Compressional and shear slowness curves; the basis of synthetic seismograms and mechanical-property estimates.",
     "Drilling noise and the collar's own arrival interfere; slow formations may give no shear reading."),
    ('lwd_imaging', 'measure', 'LWD borehole imaging',
     'Builds a picture of the borehole wall while rotating: bedding, fractures and breakouts by azimuth.',
     "An LWD tool that records a measurement - resistivity, density, gamma or ultrasonic - in many azimuthal bins as the string rotates, building an image of the wall.",
     "In the BHA; images need rotation, so they are thin in sliding intervals.",
     "Sensors on the collar sample the wall all the way round each turn; bins are stacked by depth into an image.",
     "An unwrapped image of the borehole wall with dipping beds as sine waves; dips, fractures and breakouts picked from it.",
     "Sliding leaves gaps; image quality depends on rotation speed and rate of penetration."),
    ('lwd_nmr', 'measure', 'LWD nuclear magnetic resonance',
     'Measures the fluids in the pore space while drilling: porosity independent of rock type, and pore-size distribution.',
     "An LWD tool that uses magnets and radio-frequency pulses to measure hydrogen nuclei in pore fluids, giving porosity, pore-size distribution and bound versus free fluid.",
     "In the BHA; sensitive to tool motion, so it favours steady drilling.",
     "Permanent magnets polarise hydrogen in the formation; radio pulses tip it and the decay of the echoes is recorded.",
     "Porosity and relaxation-time distributions against depth.",
     "Lateral motion of the tool spoils the measurement; rate of penetration is limited to what the polarisation needs."),
    ('pwd', 'measure', 'Pressure while drilling (annular pressure sub)',
     'Measures bore and annulus pressure at the BHA: equivalent circulating density, hole cleaning, kick and loss warnings.',
     "A sensor sub measuring the pressure in the drill-string bore and in the annulus at the BHA, and often temperature.",
     "In the BHA, usually in or beside the MWD or an LWD collar.",
     "Pressure transducers ported to the bore and annulus record the downhole pressure, transmitted in real time or stored.",
     "Equivalent circulating density from the annulus pressure; swab and surge on trips; a building pressure when cuttings load the annulus.",
     "Annulus pressure creeping up at steady flow is poor hole cleaning; a drop can be a loss; the real-time stream is the first warning of both."),
    ('near_bit_sensor', 'measure', 'Near-bit (at-bit) inclination and gamma sensor',
     'Inclination and gamma measured just above the bit, so the geosteerer sees the formation and the hole angle sooner.',
     "A sensor package close to the bit - in the motor's bearing section or a short sub - measuring inclination and often gamma, with a short-hop link to the MWD above.",
     "Directly above the bit or within the motor, below the MWD.",
     "Accelerometers and gamma detectors read near the bit; a short-range link carries the data past the motor to the MWD for transmission.",
     "Inclination and gamma that lead the MWD's own readings by the distance between them.",
     "The short-hop link can drop out; the near-bit reading is what to trust when the bit is leaving a target bed."),
    ('dynamics_sub', 'measure', 'Drilling dynamics and vibration recorder (memory sub)',
     'Records shock, vibration, stick-slip and whirl downhole, mostly to memory, for the run review.',
     "A sensor package that records accelerations, rotation speed and sometimes bending downhole; often memory only, sometimes summarised in real time.",
     "In the BHA, alone or inside an MWD or LWD collar.",
     "Accelerometers and magnetometers sample at high rate; the tool stores bursts and statistics for download after the run.",
     "Downhole rotation speed against surface rotary speed (stick-slip), lateral shocks (whirl), axial shocks (bit bounce).",
     "Read the surface torque trace alongside: torque swinging at surface is the stick-slip the sub measures at the bit. This is the memory data a full frequency analysis needs.",
     "The drilling record's channels are one-second surface snapshots; downhole dynamics need the recorder's own high-rate memory, which the program will read when it arrives with its clock offset."),
    ('seismic_while_drilling', 'measure', 'Seismic while drilling',
     'Downhole receivers in the BHA record a surface seismic source at connections: a time-depth tie and a look ahead of the bit.',
     "LWD seismic receivers in the BHA that record signals from a surface source when the pumps are off, giving check shots and a vertical seismic profile while drilling.",
     "In the BHA; shots are taken at connections when the string is still.",
     "A surface air gun or vibrator fires; the downhole sensors record the arrival, and travel times tie depth to the seismic section.",
     "Travel time against depth; a look ahead of the bit at reflectors below.",
     "The string must be quiet for the shot; timing between surface and downhole clocks has to be exact."),
    ('formation_tester_lwd', 'measure', 'Formation pressure tester while drilling',
     'Sets a probe against the wall while drilling to measure formation pore pressure and mobility.',
     "An LWD tool with a probe that extends against the borehole wall and draws down a small volume to measure the formation's pore pressure and mobility.",
     "In the BHA; tests are taken with the string stationary, often at connections.",
     "The probe seals against the wall, a pretest piston draws fluid, and the pressure build-up afterwards gives formation pressure.",
     "Formation pressure points against depth: the pore-pressure profile and the fluid gradients.",
     "A test means the string is stationary in open hole, which risks differential sticking; seal failures give no pressure."),
    # -- drill string and BHA components ----------------------------------------------------------------------------
    ('drill_pipe', 'bha', 'Drill pipe',
     'The seamless steel tubing, joint by joint with tool joints, that carries rotation, weight and mud from surface to the BHA.',
     "Heavy seamless steel tubes with threaded tool joints welded on each end; joints are made up into stands and run from the rig to the BHA.",
     "Most of the string's length, above the heavyweight pipe and the BHA.",
     "It transmits torque from the top drive, carries the mud down its bore, and hangs in tension from the hook.",
     "Hook load is mostly drill-pipe weight in mud; connections in the drilling record are pipe being added; torque and drag grow with the length of pipe against the wall.",
     "Tool joints are inspected and graded; torque over the connection's rating, fatigue in doglegs, and washouts (a slow fall in standpipe pressure) end pipe early."),
    ('hwdp', 'bha', 'Heavyweight drill pipe (HWDP)',
     'Thick-walled pipe between the collars and the drill pipe: a gradual change in stiffness, and weight on bit in deviated holes.',
     "Drill pipe with a thicker wall and longer tool joints, often with a central wear pad, heavier than drill pipe but more flexible than collars.",
     "Between the drill collars and the drill pipe, and in high-angle wells as the weight that pushes the BHA.",
     "Its weight and stiffness sit between collars and pipe, easing the bending stress where the two meet.",
     "Part of the string weight in the hook load; in laterals it is often placed in the vertical section to push.",
     "Fatigue at the transition to drill pipe; wear on the centre pad."),
    ('drill_collar', 'bha', 'Drill collar',
     'Thick, heavy steel tubes above the bit that supply weight on bit and keep the string straight in compression.',
     "Thick-walled steel tubes, slick or spiral-grooved, that make up the heavy part of the BHA.",
     "Directly above the bit and the downhole tools, below the heavyweight pipe.",
     "Their weight, partly set down on the bit, supplies weight on bit while the pipe above stays in tension.",
     "Weight on bit is collar weight the driller slacks off from the hook load.",
     "Connections in collars carry high bending stress and fail by fatigue; spiral collars cut the contact area and the risk of differential sticking."),
    ('nmdc', 'bha', 'Nonmagnetic drill collar',
     'A collar of nonmagnetic alloy that houses the MWD so its magnetometers read the earth\'s field, not the string\'s steel.',
     "A drill collar made of nonmagnetic stainless alloy so it does not distort the magnetic field around a compass survey sensor.",
     "Around and next to the MWD; enough length of it to isolate the sensor from the steel above and below.",
     "It simply carries no magnetism of its own, so the magnetometers inside read the earth's field.",
     "Surveys whose magnetic field strength and dip match the reference at the location.",
     "Too short a length of nonmagnetic collar for the hole angle and direction gives azimuth errors; the alloy can pick up magnetic hot spots that need checking."),
    ('stabilizer', 'bha', 'Stabilizer',
     'Blades on a short sub that centre the BHA in the hole and set whether it builds, holds or drops angle.',
     "A short sub with blades - integral, welded or replaceable sleeve - at near hole size, placed in the BHA to centre it.",
     "Near the bit and at points along the BHA; the spacing between them sets the assembly's tendency.",
     "The blades touch the wall and fix the BHA's contact points, so its bending under weight builds, holds or drops the hole angle.",
     "Torque and drag are affected by the stabilisers' contact; packing off around them raises standpipe pressure.",
     "An under-gauge stabiliser changes the assembly's tendency; stabilisers hang up in ledges and doglegs on trips."),
    ('crossover_sub', 'bha', 'Crossover sub',
     'A short sub that joins two different thread types or sizes in the string.',
     "A short piece with a different connection at each end, to join components whose threads or sizes differ.",
     "Wherever two connection types meet: between collars and pipe, tools and collars.",
     "It is a threaded adapter; its walls are sized to carry the string's loads at the change.",
     "Nothing of its own; its length is part of the BHA tally that sets every sensor offset.",
     "The BHA tally must include it - a missed sub moves every sensor-to-bit distance."),
    ('bit_sub', 'bha', 'Bit sub',
     'The short sub that connects the bit to the collars or tools above, often holding the float valve.',
     "A short sub between the bit and the drill collars or tool above it, converting the bit's connection to the string's; it often carries the float valve.",
     "Directly above the bit in rotary assemblies.",
     "It is the adapter between the bit's pin and the string.",
     "Nothing of its own; it is part of the BHA length.",
     "Its connections see high stress close to the bit; inspected like the collars."),
    ('float_valve', 'bha', 'Drill string float valve',
     'A one-way valve near the bit that lets mud flow down but stops flow back up the string.',
     "A check valve - flapper or plunger type - in the bit sub or a float sub, letting mud pass down and closing against flow back up the bore.",
     "Near the bottom of the string.",
     "Flow from above holds it open; reverse flow or a kick closes it.",
     "Pipe must be filled when running in with a float; standpipe pressure after shut-in needs a technique to read through it.",
     "A float stops the string backflowing on connections; a leaking one lets cuttings into the BHA."),
    ('shock_sub', 'bha', 'Shock sub',
     'A spring section in the BHA that absorbs axial shock and vibration from the bit.',
     "A tool with a spring element - steel springs or elastomer - that absorbs axial and torsional shocks between the bit and the BHA.",
     "Above the bit or motor, in hard or interbedded rock.",
     "The spring lets the bit move axially against a cushion, damping bit bounce before it reaches the MWD and collars.",
     "Smoother weight on bit and less axial vibration in a dynamics recording.",
     "It changes the BHA's natural frequencies, which can move the vibration problem rather than remove it."),
    ('drilling_jar', 'bha', 'Drilling jar (hydraulic and mechanical)',
     'A tool that stores energy in the stretched or compressed string and releases it as a hammer blow to free stuck pipe.',
     "A tool in the BHA that, when the pipe is stuck, delivers a sharp upward or downward impact to free it.",
     "In the BHA above the likely sticking point, with weight below it, usually in the heavyweight pipe.",
     "The driller pulls (or slacks off) to stretch the string; a hydraulic metering delay or a mechanical latch holds the jar until it trips, releasing the stored energy as a blow.",
     "A jarring sequence shows in hook load: a pull, a hold, a sudden drop as the jar fires, repeated.",
     "Jars have a cocked and a fired position and a recommended placement for the neutral point; hydraulic jars need time to recock and heat to cycle."),
    ('jar_accelerator', 'bha', 'Jar accelerator (intensifier)',
     'Placed above a jar to store energy and add speed to the blow, especially in shallow or deviated holes.',
     "A tool with a compressible spring or gas chamber run above a jar to store energy and increase the impact.",
     "Above the jar, with collars or heavyweight pipe between.",
     "When the string is pulled, the accelerator compresses; when the jar trips, it releases and accelerates the hammer mass.",
     "Sharper hook-load drops in a jarring sequence than the jar alone.",
     "It also protects the string and rig from the shock of the blow."),
    ('circulating_sub', 'bha', 'Circulating sub (ball-activated bypass)',
     'A sub that opens ports to the annulus on command, so mud bypasses the tools below: for lost circulation material and pills.',
     "A sub with ports that can be opened - by a dropped ball, a dart or a downlink - to send flow to the annulus above the BHA.",
     "Above the motor and MWD, which would otherwise take the full flow.",
     "Opening diverts the flow, letting heavy pills or lost circulation material bypass the tools; closing restores flow to the bit.",
     "A drop in standpipe pressure when the ports open.",
     "Confirm open and closed by pressure before pumping a pill; a sub that will not close leaves the bit without flow."),
    ('string_filter', 'bha', 'Drill pipe screen and string filter',
     'A screen in the string that catches debris before it reaches the MWD and motor.',
     "A strainer run in the drill string - at the top of the BHA or in the pipe - that catches debris in the mud.",
     "Above the MWD and motor, retrievable so it can be cleaned.",
     "Mud passes through the screen; solids larger than its openings are held back.",
     "Rising standpipe pressure at steady flow as the screen loads.",
     "Pull and clean it on trips; a plugged screen raises pressure and starves the tools below."),
    # -- casing and cementing equipment -----------------------------------------------------------------------------
    ('float_shoe', 'casing', 'Float shoe and guide shoe',
     'The rounded nose on the bottom joint of casing that guides it down the hole; a float shoe adds a check valve.',
     "A rounded, drillable nose made up on the bottom of the casing string to guide it past ledges; a float shoe also carries a check valve.",
     "At the very bottom of the casing string.",
     "The nose deflects the casing off obstructions; the valve lets cement go out and keeps it from flowing back in.",
     "Casing running loads in the hook load; a drill-out of the shoe when drilling resumes.",
     "A valve that fails lets cement U-tube back up the casing; the shoe track is drilled out on the next section."),
    ('float_collar', 'casing', 'Float collar',
     'A check valve a joint or two above the shoe that holds the cement in place and catches the wiper plugs.',
     "A collar with a check valve run a short distance above the shoe; it stops back-flow of cement and seats the cementing plugs.",
     "One or more joints above the shoe; the casing between them is the shoe track.",
     "The plugs land on it; the valve holds the cement column outside the casing after pumping stops.",
     "Plug bump - a pressure rise when the top plug lands - at the end of the cement job.",
     "The shoe track keeps contaminated cement from the bottom of the plug away from the shoe."),
    ('centralizer', 'casing', 'Casing centralizer',
     'Bows or blades on the casing that hold it off the wall so cement can surround it evenly.',
     "A device clamped or built onto the casing - bow-spring or rigid blades - to keep it centred in the hole.",
     "Spaced along the casing, closer in deviated sections and across zones that need isolation.",
     "The bows or blades push against the wall, holding stand-off so the cement can flow all round the pipe.",
     "Running drag in the hook load; the cement bond log afterwards shows whether the casing was centred.",
     "Too few in a deviated hole leaves the casing on the low side and a channel in the cement; too many adds drag."),
    ('cement_plug', 'casing', 'Cementing plugs (bottom and top wiper plugs)',
     'Rubber plugs pumped ahead of and behind the cement to wipe the casing and keep mud and cement apart.',
     "Rubber wiper plugs: a bottom plug ahead of the cement and a top plug behind it, separating cement from the fluids around it.",
     "Released from the cementing head at surface and pumped down the casing to the float collar.",
     "The bottom plug wipes mud off the casing wall and ruptures when it lands; the top plug follows the cement and lands on it.",
     "The pump pressure and volumes of the cement job; the bump when the top plug lands.",
     "Pumping past the calculated displacement without a bump means the plug did not land or the volume was wrong."),
    ('stage_collar', 'casing', 'Stage cementing collar (DV tool)',
     'A collar with ports in the casing string so cement can be pumped in two stages to cover a long section.',
     "A collar with closable ports placed in the casing string so a second stage of cement can be pumped from part way up.",
     "In the casing string at the planned stage depth.",
     "After the first stage, a dropped opening plug opens the ports; the second stage is pumped through them and a closing plug shuts them.",
     "Two cement jobs in the pumping record, each with its own plug landing.",
     "Ports that fail to open or close; the collar must be drilled out afterwards."),
    ('liner_hanger', 'casing', 'Liner hanger',
     'Slips and a packer that hang a liner from the bottom of the casing above instead of running pipe to surface.',
     "A tool at the top of a liner whose slips grip the casing above to hang the liner, often with a packer to seal the top.",
     "At the top of the liner, overlapping the previous casing.",
     "The liner is run on drill pipe; pressure or rotation sets the slips and the packer; the running tool releases and comes out.",
     "Setting and release show in hook load and pressure on the job record.",
     "A hanger that sets early stops the liner short of depth; a leaking liner-top packer needs a remedial squeeze."),
    ('casing_scraper', 'casing', 'Casing scraper',
     'A tool with spring-loaded blades run to clean scale, cement and burrs off the inside of casing before tools are set.',
     "A tool with blades or pads held against the casing wall by springs, run to clean the inside of the casing.",
     "Run on a cleanout trip before packers, plugs or other tools are set in the casing.",
     "As it is reciprocated and rotated, the blades scrape off cement, scale and perforation burrs.",
     "A cleanout trip in the rig record.",
     "Debris it loosens has to be circulated out; a scraper run to the setting depth is the usual check before a packer."),
    # -- completion and artificial lift -----------------------------------------------------------------------------
    ('production_packer', 'completion', 'Production packer (permanent and retrievable)',
     'A device that seals the space between tubing and casing, isolating zones and protecting the casing.',
     "A tool with slips and an elastomer element that seals the annulus between the production tubing and the casing; permanent types are milled out, retrievable ones are released and pulled.",
     "Above the producing zone, on the production tubing, sometimes several to isolate zones.",
     "Slips grip the casing to anchor it; the element is squeezed to seal - set by pressure, tension, compression or wireline.",
     "Tubing and annulus pressures that differ - a packer that holds; an annulus that follows the tubing - a leak.",
     "Temperature and pressure cycling move the tubing; the packer must hold the loads; annulus pressure is the watch."),
    ('bridge_plug', 'completion', 'Bridge plug',
     'A plug set in the casing to isolate the hole below it - permanently, or retrievable.',
     "A tool set in casing or tubing that seals the bore completely, isolating the well below it.",
     "Set at the depth to isolate: below a zone being treated, above an abandoned zone, or for well abandonment with cement on top.",
     "Run on wireline or tubing, it is set with slips and a sealing element like a packer with no flow path.",
     "A pressure test above it confirms the seal.",
     "Setting depth must avoid casing collars; a plug that does not hold is found by its pressure test."),
    ('frac_plug', 'completion', 'Frac plug (composite and dissolvable)',
     'A plug set between stages of a plug-and-perf fracturing job to isolate the stage below; drilled out or dissolved after.',
     "A plug set in the casing between fracturing stages; a ball dropped onto it seals the stage below while the next is treated.",
     "In horizontal laterals, one per stage, set with the perforating guns on the same wireline run.",
     "Composite plugs are drilled out with coiled tubing afterwards; dissolvable plugs degrade in the well fluids.",
     "Stage-by-stage pressure in the frac record; the drill-out shows in the coiled tubing record.",
     "A plug that does not seal lets the treatment go to the stage below; drill-out debris must be circulated clean."),
    ('sliding_sleeve', 'completion', 'Sliding sleeve',
     'A sleeve in the tubing that is shifted open or closed to connect or isolate the tubing and annulus at a zone.',
     "A tubing component with ports and an inner sleeve that can be shifted to open or close communication between tubing and annulus.",
     "In the completion string opposite a zone, or above a packer for circulation.",
     "A shifting tool on wireline or coiled tubing engages the sleeve and moves it open or closed.",
     "A change in tubing and annulus pressures when it is shifted; production from the zone it opens.",
     "Scale or debris can lock a sleeve; shifting is confirmed by pressure."),
    ('frac_sleeve', 'completion', 'Ball-activated frac sleeve',
     'A sleeve in the completion opened by a dropped ball to frac stages in order without plugs or perforating.',
     "A sleeve in the liner or casing that is opened by a ball of matching size, isolating the stages below and opening ports to the formation.",
     "In multistage completions, one or more per stage, with ball seats from smallest at the toe to largest at the heel.",
     "Each ball lands on its seat, shifts the sleeve open and seals the stages below; the treatment goes out through the opened ports.",
     "A pressure spike and drop as each ball seats and the sleeve shifts, stage by stage.",
     "A ball that does not seat or a sleeve that does not shift merges stages; seats may be milled out afterwards."),
    ('sssv', 'completion', 'Subsurface safety valve',
     'A fail-safe valve in the tubing below the wellhead that closes the well if surface control is lost.',
     "A valve in the production tubing below surface that closes automatically to shut in the well if the surface equipment is damaged.",
     "In the tubing below the wellhead; tubing-retrievable or wireline-retrievable.",
     "Hydraulic pressure from a control line holds the flapper open; losing that pressure lets a spring close it.",
     "Control-line pressure; closure tests in the well's records.",
     "Valves are tested on a schedule; a valve that does not close, or leaks when closed, is a well-integrity issue."),
    ('landing_nipple', 'completion', 'Landing nipple',
     'A profile in the tubing where wireline tools - plugs, chokes, gauges - can be landed and locked.',
     "A short tubing component with an internal profile in which a lock mandrel carrying a plug, choke, check valve or gauge can be set.",
     "At planned depths in the completion string.",
     "A wireline lock latches into the profile; selective profiles let tools pass some nipples and land in another.",
     "A gauge hung in a nipple is a memory or real-time gauge whose data the program reads.",
     "Nipple profiles reduce the tubing bore; the size sequence decides which tools can pass."),
    ('gas_lift_mandrel', 'completion', 'Gas lift mandrel and valve',
     'Side pockets in the tubing that hold gas lift valves: injected gas lightens the fluid column so the well flows.',
     "A tubing component with a side pocket holding a gas lift valve that lets injection gas from the annulus into the tubing.",
     "At several depths in the production tubing, unloading valves above an operating valve.",
     "Gas injected down the annulus enters through the valves, lowering the density of the fluid column so reservoir pressure can lift it.",
     "Casing and tubing pressures and injection rate; the operating valve depth shows in a flowing gradient survey.",
     "Valves are changed by wireline; multipointing or a failed valve shows as unstable casing pressure."),
    ('esp', 'completion', 'Electric submersible pump (ESP)',
     'A multistage centrifugal pump driven by a downhole electric motor: high-rate artificial lift.',
     "A downhole pump of many centrifugal stages driven by a submersible electric motor, powered by a cable from surface.",
     "On the production tubing above the perforations or in a shroud below them.",
     "The motor turns the pump stages, each adding head; a variable speed drive at surface sets the rate.",
     "Motor current, intake pressure and temperature from the pump's own downhole sensor; flow at surface.",
     "Gas, sand and heat shorten its life; intake pressure and motor temperature from the downhole sensor are the watch. The pump sensor is a downhole gauge the program can read.",
     "The pump's downhole sensor is a gauge stream like any other: intake pressure and temperature into the program's drift and well-test checks."),
    ('rod_pump', 'completion', 'Sucker rod pump',
     'A plunger pump at the bottom of the tubing, driven up and down by a rod string from a surface pumping unit.',
     "A downhole plunger-and-barrel pump with standing and travelling valves, worked by a string of sucker rods from a surface pumping unit.",
     "At the bottom of the production tubing, at or below the fluid level.",
     "Each upstroke lifts fluid through the travelling valve; the standing valve holds it on the downstroke.",
     "The dynamometer card - rod load against position - read at surface; fluid level by acoustic survey.",
     "Fluid pound, gas interference and rod parts show in the card; worn rods and tubing in deviated wells."),
    ('pcp', 'completion', 'Progressing cavity pump (PCP)',
     'A helical rotor turning in an elastomer stator: lifts heavy and sandy oil steadily.',
     "A pump with a single helical rotor turning inside a double-helix elastomer stator, forming cavities that carry fluid upward.",
     "On the tubing at the bottom of the well, driven by rods from a surface drive or by a downhole motor.",
     "As the rotor turns, cavities progress from intake to discharge at a rate set by speed.",
     "Drive torque and speed at surface; fluid level.",
     "Elastomer swelling or heat damages the stator; running dry burns it."),
    ('sand_screen', 'completion', 'Sand screen and gravel pack',
     'A filter in the completion - with or without sized gravel around it - that keeps formation sand out of the well.',
     "A filter of wire wrap, mesh or slotted pipe placed opposite the reservoir, often with sized gravel packed between it and the formation.",
     "Across the producing interval, in open hole or inside perforated casing.",
     "The gravel holds back the formation sand and the screen holds back the gravel; fluid passes through.",
     "Sand at surface, or rising pressure drop across the completion, are the signs of failure or plugging.",
     "Screen erosion and plugging; the gravel pack's placement is checked during the job."),
    ('icd', 'completion', 'Inflow control device (ICD)',
     'Restrictions in each completion joint that even out inflow along a long horizontal well.',
     "A flow restriction built into each completion joint - channels, nozzles or orifices - so inflow is balanced along the lateral; autonomous devices restrict water or gas more than oil.",
     "Along horizontal completions, usually with packers separating compartments.",
     "Each device adds a pressure drop that limits flow from high-productivity zones so the toe contributes as well as the heel.",
     "A more even inflow profile in production logs; delayed water or gas breakthrough.",
     "Plugging and erosion change the balance; the design depends on reservoir properties."),
    ('perforating_gun', 'completion', 'Perforating gun (wireline and tubing-conveyed)',
     'A carrier of shaped explosive charges fired downhole to shoot holes through casing and cement into the rock.',
     "A carrier loaded with shaped charges, run into the cased well and fired to create tunnels through the casing and cement into the formation.",
     "On wireline, coiled tubing or tubing, positioned by a casing collar locator against the log.",
     "Each shaped charge forms a high-velocity jet that pierces the casing, cement and rock.",
     "Depth correlation by collar locator; a pressure response at surface when the guns fire.",
     "Explosives handling rules apply; depth correlation decides whether the right zone is shot."),
    ('pdg', 'completion', 'Permanent downhole gauge',
     'A pressure and temperature gauge installed with the completion and read for the life of the well.',
     "A pressure and temperature gauge - quartz, piezoresistive or vibrating-wire - mounted on the completion and cabled to surface.",
     "On the tubing above the packer or in a gauge mandrel, cabled through the wellhead.",
     "The sensor's frequency or resistance changes with pressure and temperature; downhole electronics send the readings up the cable to a surface interface.",
     "Continuous pressure and temperature at depth: build-ups and drawdowns, reservoir pressure decline, gauge drift.",
     "Drift over years, temperature effects on the reading, and a failed cable splice; the comparison against other gauges is what tells drift from the reservoir.",
     "This is the instrument the program was built for: its streams are read, quality-checked, compared for drift with a change log, and well tests are validated from them. Gauge models with published datasheets are in the gauge library on this page."),
    ('fiber_dts_das', 'completion', 'Fiber optic DTS and DAS',
     'An optical fibre along the well that senses temperature (DTS) and acoustic vibration (DAS) along its whole length.',
     "An optical fibre in a control line or behind casing; laser light backscattered along it is read at surface for distributed temperature and distributed acoustic sensing.",
     "Along the whole completion, strapped to tubing or cemented behind casing.",
     "An interrogator sends pulses and times the backscatter: temperature from the Raman signal, vibration from changes in the Rayleigh signal.",
     "A temperature or acoustic profile along the well against time: inflow, crossflow, frac stage coverage.",
     "Fibre darkening with heat and hydrogen; the depth calibration of the fibre against the well."),
    ('plunger_lift', 'completion', 'Plunger lift',
     'A free plunger that travels up and down the tubing, using the well\'s own gas to lift liquid slugs and keep a gas well flowing.',
     "A free piston that travels the length of the tubing between a bottom stop and a surface lubricator, lifting the liquid above it with the well's gas pressure.",
     "In the production tubing of gas wells loading up with liquid.",
     "The well is shut in to build pressure, then opened; the plunger rises carrying a liquid slug and falls back when the flow drops.",
     "Cycles in tubing and casing pressure and in the plunger arrival times at surface.",
     "Arrival speed too fast damages the lubricator; too slow means the cycle is mis-set."),
    # -- wireline and cased-hole logging ----------------------------------------------------------------------------
    ('wl_gamma', 'logging', 'Wireline gamma ray (and spectral gamma)',
     'Measures natural gamma radiation on wireline: the correlation curve on almost every log; spectral tools split it by element.',
     "A wireline sensor counting natural gamma rays from the formation; spectral tools resolve potassium, thorium and uranium.",
     "In nearly every wireline string, open or cased hole, as the depth-correlation curve.",
     "Scintillation detectors count gamma rays; spectral tools sort them by energy.",
     "A gamma ray log against depth: shale volume, correlation between wells, depth tie to the drilling gamma.",
     "Cased-hole gamma is attenuated by steel and cement; potassium-bearing mud raises the reading.",
     "Gamma logs in LAS or DLIS are the strata survey's first input: the program reads them, recognises the curve, and runs the lithology-from-gamma index."),
    ('wl_resistivity', 'logging', 'Wireline resistivity (induction and laterolog)',
     'Measures how well the formation conducts: induction in fresh or oil-based mud, laterolog in salty mud.',
     "Wireline tools that measure formation resistivity: induction tools induce currents with coils; laterologs focus current from electrodes.",
     "In the open-hole logging run, choosing the tool by the mud.",
     "Induction measures conductivity from secondary fields; laterologs force current into the formation and measure the voltage.",
     "Resistivity curves at several depths of investigation; with porosity, water saturation.",
     "Borehole effects, invasion and thin beds; the right tool for the mud."),
    ('wl_density', 'logging', 'Wireline density',
     'A gamma source and detectors pressed against the wall measure bulk density, the basis of porosity.',
     "A pad tool with a gamma-ray source and detectors pressed against the borehole wall to measure bulk density.",
     "In the open-hole logging run, usually with neutron porosity.",
     "Gamma rays scattered by electrons in the rock are counted; their attenuation gives electron density and so bulk density; a photoelectric reading gives lithology.",
     "Bulk density and its correction curve; with neutron porosity, porosity and lithology.",
     "Rough hole and mudcake spoil pad contact; the correction curve says how far to trust the reading."),
    ('wl_neutron', 'logging', 'Wireline neutron porosity',
     'A neutron source and detectors that respond to hydrogen in the rock: porosity, and gas where it reads low.',
     "A tool with a neutron source and detectors measuring how neutrons are slowed and captured, which depends on hydrogen in the formation.",
     "In the open-hole logging run with density; also through casing.",
     "Hydrogen nuclei slow neutrons most; the detector count rates give a hydrogen index read as porosity.",
     "Neutron porosity; crossover with density flags gas.",
     "Shale and lithology affect it; the source is regulated."),
    ('wl_sonic', 'logging', 'Wireline sonic',
     'Measures the speed of sound in the rock: porosity, mechanical properties and the seismic tie.',
     "An acoustic tool with transmitters and receiver arrays that measures compressional and shear slowness, and dipole tools for shear anisotropy.",
     "In the open-hole run, and through casing for cement evaluation variants.",
     "Pulses travel through the formation along the wall; arrival times across the array give slowness.",
     "Compressional and shear slowness; mechanical properties for fracture design; synthetic seismograms.",
     "Washouts and gas alter the waveforms; processing quality decides the shear curve."),
    ('wl_nmr', 'logging', 'Wireline nuclear magnetic resonance',
     'Measures the fluids in the pores: porosity independent of lithology, pore size, bound and free fluid.',
     "A wireline tool that uses magnets and radio-frequency pulses to measure hydrogen in pore fluids.",
     "In the open-hole run, logged slowly.",
     "Hydrogen polarised by the magnet is tipped by radio pulses; the decay of the echoes gives a distribution of relaxation times related to pore size.",
     "Total and effective porosity, bound and free fluid, a permeability estimate.",
     "Logging speed and borehole conditions limit it; the interpretation needs calibration."),
    ('wl_imager', 'logging', 'Wireline borehole imager',
     'Pads of micro-resistivity buttons or an ultrasonic transducer that picture the borehole wall.',
     "A tool that images the borehole wall - with arrays of resistivity buttons on pads, or an ultrasonic transducer.",
     "In the open-hole run, oriented by its own inclinometry.",
     "Each button or pulse samples the wall; samples are stacked by depth and azimuth into an image.",
     "An image of bedding, fractures, breakouts and faults; dips picked from it; stress direction from breakouts.",
     "Pad contact and tool speed control image quality; oil-based mud needs the right tool type."),
    ('wl_formation_tester', 'logging', 'Wireline formation tester',
     'Sets a probe or packers against the wall to measure formation pressure and take fluid samples.',
     "A wireline tool that seals a probe or straddle packers against the borehole wall to measure pressure and pump and sample formation fluid.",
     "In the open-hole run, stopped at chosen depths.",
     "The probe seals, a pretest draws fluid, the pressure build-up gives formation pressure; pump-out modules clean up and collect samples.",
     "Pressure points against depth, fluid gradients and contacts; downhole fluid analysis and samples.",
     "Stationary time in open hole risks sticking the tool; seal failures and tight rock give no pressure."),
    ('caliper', 'logging', 'Caliper tool',
     'Arms that measure the borehole\'s diameter: washouts, hole volume and the corrections other logs need.',
     "A tool with arms that touch the borehole wall and measure its diameter - two, three, four or more arms.",
     "On most open-hole runs, often built into other pad tools.",
     "The arms open against the wall; their positions give the hole diameter, and pairs give its shape.",
     "Hole diameter against depth: washouts, ledges, breakouts, the cement volume needed.",
     "A single-axis caliper misses an oval hole; compare with the bit size."),
    ('cbl', 'logging', 'Cement bond log tool (CBL and VDL)',
     'An acoustic tool in casing that shows how well the cement is bonded to the pipe and the formation.',
     "A cased-hole acoustic tool whose transmitter and receivers measure the amplitude of the casing arrival (CBL) and the full waveform (VDL).",
     "Run in the cased hole after cementing.",
     "Well-bonded cement damps the casing's ringing, so the amplitude is low; free pipe rings loud.",
     "Amplitude and the waveform display against depth: bonded, partly bonded and free pipe.",
     "Microannulus, fast formations and tool centring confuse the reading; a pressure pass can tell a microannulus apart."),
    ('plt', 'logging', 'Production logging tool (spinner flowmeter)',
     'Spinner, pressure, temperature and fluid sensors run through a producing well to find where each fluid enters.',
     "A string of sensors - spinner flowmeter, pressure, temperature, fluid density and holdup - run through a flowing well.",
     "In the producing interval of cased wells, on wireline, slickline or coiled tubing; tractored in horizontals.",
     "The spinner turns with the flow; passes at different speeds give velocity; the other sensors give the fluid mix.",
     "An inflow profile: which zone produces how much oil, water and gas.",
     "Deviated wells segregate the phases, which a single spinner misreads; multiple-sensor tools are used there.",
     "Its pressure channel is a gauge stream; a production log in LAS is read like any log."),
    ('ccl', 'logging', 'Casing collar locator',
     'A magnetic sensor that marks each casing collar: the depth reference for perforating and setting tools.',
     "A magnetic or electromagnetic sensor that registers the extra metal at each casing coupling as it passes.",
     "On nearly every cased-hole string: perforating guns, plugs, production logs.",
     "The thicker metal at a collar disturbs the magnetic field and induces a signal.",
     "A spike at each collar on the log, matched to the casing tally and the open-hole log.",
     "Depth correlation by collar is what decides that the guns are on the zone."),
    ('multifinger_caliper', 'logging', 'Multifinger caliper',
     'Many feeler arms that measure the inside of casing or tubing: corrosion, wear, deformation.',
     "A cased-hole tool with many spring-loaded fingers measuring the internal radius of the pipe all the way round.",
     "In casing or tubing, logged for integrity surveys.",
     "Each finger follows the pipe wall; the set gives the internal shape at each depth.",
     "Internal radius maps: corrosion pits, wear, ovality, collapse.",
     "Deposits on the wall read as metal loss in reverse; centring matters."),
    ('sidewall_coring', 'logging', 'Sidewall coring tool',
     'Takes small rock samples from the borehole wall on wireline: percussion bullets or rotary drills.',
     "A wireline tool that recovers small samples of rock from the borehole wall, by firing hollow bullets or by drilling plugs.",
     "In the open hole after logging, at depths chosen from the logs.",
     "Percussion tools fire retrievable bullets into the wall; rotary tools drill a plug sideways and store it.",
     "Samples matched to log depths for lithology, porosity and fluids.",
     "Percussion samples are crushed; rotary samples are better but slower."),
    ('vsp_tool', 'logging', 'VSP and checkshot tool',
     'Downhole geophones that record a surface seismic source: the time-depth tie between the well and the seismic.',
     "A wireline tool with clamping geophones that records seismic energy from a surface source at many depths.",
     "In open or cased hole, clamped to the wall at stations.",
     "The source fires; the tool records arrival times and waveforms at each level; first arrivals give the time-depth curve.",
     "A time-depth table and a vertical seismic profile tying the well to the seismic section.",
     "Clamping and noise decide data quality; source positions define what the survey images."),
    # -- fishing, milling and intervention --------------------------------------------------------------------------
    ('overshot', 'fishing', 'Overshot',
     'A fishing tool that slides over the outside of lost pipe and grips it with a grapple so it can be pulled.',
     "A fishing tool with an internal grapple that goes over the outside of a fish - a lost section of pipe - and grips it.",
     "Run on the fishing string to the top of the fish.",
     "The grapple slides over the fish and grips as the string is lifted; it can be released by rotating.",
     "A weight increase in hook load when the fish is caught; overpull while working it free.",
     "The grapple size must match the fish's outside diameter; the top of the fish may need dressing first."),
    ('spear', 'fishing', 'Spear',
     'A fishing tool that goes inside lost pipe and grips it from within.',
     "A fishing tool whose grapple engages the inside of a fish, the opposite of an overshot.",
     "Run into the bore of the fish.",
     "The grapple enters the fish and expands against its wall when the string is lifted; it can be released.",
     "Hook load rising when the fish is caught.",
     "The fish's inside diameter must be clear; a spear catches where an overshot cannot fit outside."),
    ('junk_basket', 'fishing', 'Junk basket',
     'A tool that catches small debris - bit cones, broken parts - in a basket for retrieval.',
     "A tool with a basket or fingers that catches small junk from the hole bottom; reverse-circulation types use flow to draw the junk in.",
     "Above the bit or mill on a cleanout run, or alone on a fishing trip.",
     "Circulation or coring action lifts the junk into the basket, where fingers hold it.",
     "A fishing trip in the drilling record.",
     "Junk left on bottom damages the next bit; the basket is checked at surface."),
    ('junk_mill', 'fishing', 'Junk mill and taper mill',
     'Tools faced with hard carbide to grind up junk or open a restriction in casing.',
     "Mills dressed with crushed tungsten carbide: junk mills with a flat face to grind debris, taper mills with a cone to open restrictions.",
     "Run on drill pipe or coiled tubing to the junk or the restriction.",
     "Rotation and weight grind the metal into cuttings carried out by the mud.",
     "Milling in the drilling record: high torque, low progress, metal at the shakers.",
     "Milling is slow and the cuttings must be carried; magnets at surface catch the swarf."),
    ('fishing_magnet', 'fishing', 'Fishing magnet',
     'A strong magnet run to pick up small steel junk from the hole.',
     "A tool with a permanent magnet or electromagnet face used to retrieve small ferrous junk.",
     "Run to the bottom where steel debris lies.",
     "The magnet holds the debris against its face as the tool is pulled.",
     "A fishing trip in the drilling record: a run to bottom with little rotation, and steel debris on the magnet at surface.",
     "It catches only steel; anything else needs a basket."),
    ('impression_block', 'fishing', 'Impression block',
     'A block with a soft face set down on a fish to take an imprint of its top.',
     "A tool with a soft lead or similar face set down on the top of a fish so its shape is printed on the face.",
     "Run before fishing, when the top of the fish is unknown.",
     "Setting down weight presses the fish's outline into the soft face.",
     "A short trip; the impression is read at surface.",
     "The imprint decides which fishing tool to run."),
    ('washpipe', 'fishing', 'Washpipe (washover)',
     'Large-bore pipe with a cutting shoe that cuts around a stuck fish to free it.',
     "Large-diameter pipe with a rotary shoe that is run over a stuck fish to cut away whatever holds it.",
     "Over the fish, washing down along its length.",
     "Rotation and circulation let the shoe cut the cuttings, cement or formation around the fish.",
     "Rotating with high torque in the drilling record over a fish.",
     "Clearance between the washpipe and the hole and fish is tight; washpipe can itself become stuck."),
    ('pipe_cutter', 'fishing', 'Pipe cutter (chemical, jet and mechanical)',
     'Cuts tubing, casing or drill pipe downhole so the free part can be recovered.',
     "A tool that severs pipe downhole: a chemical cutter, an explosive jet cutter, or a mechanical cutter with knives.",
     "Run inside the pipe to just above the stuck point found by a freepoint tool.",
     "Chemical cutters spray a reactive agent; jet cutters fire a circular shaped charge; mechanical cutters cut with rotating knives.",
     "A hook load change when the pipe parts.",
     "The cut must be above the stuck point; well temperature limits some cutter types."),
    ('freepoint_tool', 'fishing', 'Freepoint indicator',
     'Measures stretch and twist in stuck pipe to find where it is free and where it is stuck.',
     "A wireline tool that anchors inside the pipe and measures stretch and torque as the pipe is pulled and turned at surface.",
     "Run inside stuck pipe at stations.",
     "Above the stuck point the pipe stretches and twists under load; below it, it does not.",
     "The free-point depth for the cut or back-off.",
     "Hole friction makes the transition gradual; stations close together refine it."),
    ('section_mill', 'fishing', 'Section mill',
     'Expandable cutter arms that mill away a full length of casing for a sidetrack or an abandonment plug.',
     "A tool whose arms open to cut through the casing and then mill a length of it away completely.",
     "Run inside the casing to the planned section depth.",
     "Pressure opens the knives; rotation cuts through the casing and then mills it downward.",
     "A slow milling interval with metal swarf in the returns.",
     "Swarf handling at surface and hole cleaning are the work; the section is then cemented."),
    ('tractor', 'fishing', 'Downhole tractor (wireline and coiled tubing)',
     'A powered tool that pulls wireline or coiled tubing tools along horizontal hole where gravity cannot.',
     "A tool with wheels or grippers, powered through the wireline or by the fluid, that drives the tool string along horizontal or highly deviated wells.",
     "At the bottom of wireline or coiled tubing strings in laterals.",
     "Motors drive wheels or tracks against the casing; the tension at surface shows its pull.",
     "Logging and perforating passes in laterals that would otherwise need pipe.",
     "Debris, casing deformation and high doglegs stop it."),
    # -- well testing -----------------------------------------------------------------------------------------------
    ('dst', 'testing', 'Drill stem test tools',
     'A temporary completion on the drill string - packers, valves, gauges - that flows and shuts in a zone to test it.',
     "A string of test tools run on drill pipe: packers to isolate the zone, a tester valve to open and close it, and pressure recorders.",
     "Across the zone to test, before a permanent completion.",
     "The packers seal; the valve opens the zone to flow and closes it for build-ups; the recorders log pressure through each period.",
     "Pressure against time: flow periods, build-ups, the reservoir's pressure and permeability.",
     "The packer seat and the valve operation decide whether the test is valid; recorders are run in pairs.",
     "The well-test detector reads build-ups and drawdowns from pressure series; a DST recorder's data read as a file goes through the same checks."),
]


def _load_sources() -> Dict[str, list]:
    try:
        with open(SOURCES_FILE, encoding='utf-8') as f:
            return {c['id']: c['sources'] for c in json.load(f)}
    except (OSError, ValueError):
        return {}


def _build() -> Dict[str, dict]:
    src = _load_sources()
    out: Dict[str, dict] = {}
    for row in _T:
        tid, cat, name, one, what, where, how, reads, watch = row[:9]
        prog = row[9] if len(row) > 9 else _PROGRAM[cat]
        sources = src.get(tid, [])
        figures = [dict(r, publisher=s.get('publisher'), title=s.get('title'), url=s['url'])
                   for s in sources for r in (s.get('ranges') or [])]
        out[tid] = {'id': tid, 'category': cat, 'category_name': CATEGORY_NAMES[cat], 'name': name, 'one_line': one,
                    'tutorial': {'what': what, 'where': where, 'how': how, 'reads': reads, 'watch': watch, 'program': prog},
                    'sources': [{k: s.get(k) for k in ('url', 'title', 'publisher', 'kind')} for s in sources],
                    'figures': figures,
                    'figures_note': ("each figure is the cited page's own, quoted as read on " + SOURCES_FETCHED
                                     + "; most are one maker's product, not a limit for the class") if figures
                                    else "no published figure on the pages checked; supply your model's datasheet in the fleet"}
    return out


TAXONOMY: Dict[str, dict] = _build()

# the gauge library's classes, to the taxonomy class each belongs to
GAUGE_CLASS_TO_TAXONOMY = {'QUARTZ_PT_GAUGE': 'pdg', 'PIEZORESISTIVE_PT_GAUGE': 'pdg', 'VIBRATING_WIRE_GAUGE': 'pdg',
                           'THERMOCOUPLE_STRING': 'pdg', 'FIBER_DTS': 'fiber_dts_das', 'SURFACE_INTERFACE': 'pdg'}


def classes(category: Optional[str] = None, query: str = '') -> List[dict]:
    """The classes, in catalogue order, optionally one category, optionally filtered by words in the name or one-line."""
    q = [w for w in re.split(r'\s+', (query or '').lower()) if w]
    out = []
    for t in TAXONOMY.values():
        if category and t['category'] != category:
            continue
        hay = (t['name'] + ' ' + t['one_line'] + ' ' + t['id']).lower()
        if all(w in hay for w in q):
            out.append(t)
    return out


def tool_class(tid: str) -> dict:
    if tid not in TAXONOMY:
        raise KeyError(f'no tool class {tid!r}; `gea tools --action classes` lists them')
    return TAXONOMY[tid]


def summary() -> dict:
    by_cat = {c: sum(1 for t in TAXONOMY.values() if t['category'] == c) for c, _ in CATEGORIES}
    return {'classes': len(TAXONOMY), 'categories': [{'id': c, 'name': n, 'classes': by_cat[c]} for c, n in CATEGORIES],
            'sources': len({s['url'] for t in TAXONOMY.values() for s in t['sources']}),
            'figures': sum(len(t['figures']) for t in TAXONOMY.values()),
            'classes_with_figures': sum(1 for t in TAXONOMY.values() if t['figures']),
            'sources_fetched': SOURCES_FETCHED}


def tutorial_text(tid: str) -> str:
    """The pop-open as plain text: for the CLI and the help."""
    t = tool_class(tid)
    lines = [t['name'], t['one_line'], '']
    for p in PARTS:
        lines.append(PART_TITLES[p] + ': ' + t['tutorial'][p])
    if t['figures']:
        lines.append('')
        lines.append('Published figures (' + t['figures_note'] + '):')
        for f in t['figures']:
            lines.append(f"  - {f['what']}: \"{f['quote']}\" [{f['unit']}] - {f['publisher']}, {f['url']}")
    else:
        lines.append('')
        lines.append('Published figures: ' + t['figures_note'] + '.')
    lines.append('')
    lines.append('Sources (fetched ' + SOURCES_FETCHED + '):')
    for s in t['sources']:
        lines.append(f"  - {s['publisher']}: {s['title']} - {s['url']}")
    return '\n'.join(lines)


# -- the fleet: the operator's own tools, each tied to a class and to its datasheet ----------------------------------
FLEET_FILE = 'fleet.json'
FLEET_FIELDS = ('class_id', 'maker', 'model', 'datasheet', 'serial', 'od_in', 'note')


def fleet_path(workspace_path: str) -> str:
    return os.path.join(workspace_path, 'config', FLEET_FILE)


def fleet_list(workspace_path: str) -> List[dict]:
    p = fleet_path(workspace_path)
    if not os.path.isfile(p):
        return []
    with open(p, encoding='utf-8') as f:
        return json.load(f).get('tools', [])


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def fleet_add(workspace_path: str, class_id: str, maker: str, model: str, datasheet: str,
              serial: str = '', od_in: Optional[float] = None, note: str = '', actor: str = 'system',
              ratings: Optional[dict] = None) -> dict:
    """Add one of the operator's tools. The class must be in the taxonomy and the datasheet citation - a document
    title, revision or URL - must be given: the fleet carries the maker's numbers only as the datasheet states them."""
    if class_id not in TAXONOMY:
        raise ValueError(f'no tool class {class_id!r}; `gea tools --action classes` lists them')
    if not (maker or '').strip() or not (model or '').strip():
        raise ValueError('a fleet tool needs its maker and model')
    if not (datasheet or '').strip():
        raise ValueError('a fleet tool needs its datasheet citation (title, revision or URL): the ratings it carries are the datasheet\'s')
    tools = fleet_list(workspace_path)
    tid = _slug(f'{maker} {model} {serial}'.strip())
    if any(t['id'] == tid for t in tools):
        raise ValueError(f'already in the fleet: {tid}')
    rec = {'id': tid, 'class_id': class_id, 'class_name': TAXONOMY[class_id]['name'], 'maker': maker.strip(), 'model': model.strip(),
           'serial': serial.strip(), 'od_in': od_in, 'datasheet': datasheet.strip(), 'note': note.strip(),
           'ratings': {k: v for k, v in (ratings or {}).items() if v not in (None, '')},
           'ratings_source': 'the datasheet cited' if ratings else None, 'added_by': actor}
    tools.append(rec)
    p = fleet_path(workspace_path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'schema': 1, 'tools': tools}, f, indent=1)
    os.replace(tmp, p)
    return rec


def fleet_remove(workspace_path: str, tool_id: str) -> dict:
    tools = fleet_list(workspace_path)
    keep = [t for t in tools if t['id'] != tool_id]
    if len(keep) == len(tools):
        raise ValueError(f'not in the fleet: {tool_id}')
    gone = next(t for t in tools if t['id'] == tool_id)
    p = fleet_path(workspace_path)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump({'schema': 1, 'tools': keep}, f, indent=1)
    os.replace(p + '.tmp', p)
    return gone
