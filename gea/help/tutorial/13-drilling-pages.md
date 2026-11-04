# 13. The drilling pages
Who: office
Where: #/drillstring
Takes: 45 minutes
> Each engineering page reads the drilling record, takes what the operator declares with its source, and writes a client report with one button.

## How every page works
The same shape each time: pick the well; the tiles at the top; the charts; a **declare** form (the operator role) for what the record cannot know - with a **source** box for where the figure came from; **Write the report**. Every page has a worked example under **Scene** (Volve wells from the public record) to look at before declaring your own.

## Steps
1. **Drill string**: **Declare a run** - the components from the bit up (class from the tool library, maker, model, serial, OD, length), the run's span on the recorder's clock, and for a motor a catalogued power section or its own figures. The page shows the build diagram, each component's hours and footage against its service interval, and the motor's differential, stalls and stability from the start of the run to its end. A component past its interval raises a `motor.due` alert.
2. **Rig time**: what the rig did each second, read from the hook load, the block, the bit, the pumps and the rotary - connections weight to weight, trips by speed, time lost against the declared targets or the pad's own best quarter.
3. **Torque & drag**: declare the string, the mud and the friction factors; the page draws pick-up, slack-off and rotating hook loads against bit depth with the rig's own points over them, and the friction factors that fit.
4. **Hydraulics & ECD**: declare the string, the hole, the Fann readings, the mud weight, the flow rate and the nozzles; the standpipe pressure part by part, the ECD against the pore pressure and fracture gradient, surge and swab.
5. **Pore pressure**: how the well is read (sonic, resistivity or the d-exponent, the normal compaction trend, the shale cutoff) and its measured pressures (an MDT station, a leak-off test); the mud-weight window against depth.
6. **Casing & kick tolerance**: each string as `what:od:lb_ft:grade:top-shoe:hole:mud` - the page checks burst, collapse and tension by API's formulas and the kick each open hole can take.
7. **Collision**: every well against every other by the ISCWSA separation factor, the travelling cylinder, each well's uncertainty from its survey program and the Earth's field at the well.
8. **Plan vs actual**: under **The plan**, **Attach** the directional plan with its azimuth reference and source; at each survey station, how far the well is from the plan against the survey's own uncertainty, the bit projected, the arc back.
9. **Simulation**: the well drilled again on the screen - **Play** runs the record on the recorder's clock while the mud-log panel grows with the hole.

## You know it worked when
Each page you declared on shows no *declare it* placeholder in its tiles, and its report is under the well's reports with the declarations and their sources on the last page.

Help: drillstring, rig-time, torque-drag, hydraulics, pore-pressure, casing, collision, plan, simulation
