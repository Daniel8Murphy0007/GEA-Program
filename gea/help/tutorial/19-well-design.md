# 19. The engineering checks: torque and drag, hydraulics, pore pressure, casing
Who: office
Where: #/torquedrag
Pages: #/hydraulics, #/porepressure, #/casing
Takes: 45 minutes
> Four models, each run on what you declare and then set against what the rig measured: the hook load, the standpipe pressure, the mud-weight window, the casing's ratings.

## Steps
1. Each page has a worked example under **Scene** - a Volve well from the public record. Look at it before declaring your own; every figure you then declare takes a **source**.
2. Open **Torque & drag**: declare the string, the mud and the friction factors. The page draws the pick-up, slack-off and rotating hook loads against bit depth with the rig's own points over them, and gives the friction factors that fit those points.
3. Open **Hydraulics & ECD**: declare the string, the hole, the Fann readings, the mud weight, the flow rate and the nozzles. The page gives the standpipe pressure part by part, the ECD against the pore pressure and the fracture gradient, and surge and swab for a trip speed.
4. Open **Pore pressure**: say how the well is read - sonic, resistivity or the d-exponent - with the normal compaction trend and the shale cutoff, and enter its measured pressures: a formation test, a leak-off test, a kick. The page draws the mud-weight window against depth with the measurements on it.
5. Open **Casing & kick tolerance**: each string as `what:od:lb_ft:grade:top-shoe:hole:mud`. The page checks burst, collapse and tension by API's formulas and gives the kick each open hole can take.
6. On each page, **Write the report**. A model's line is a model's line: the report prints the rig's points beside it and says where they part.

## You know it worked when
The Torque & drag chart shows the rig's own pick-up points lying along the modelled line with a friction factor named, and each page you declared on shows no *declare it* placeholder in its tiles.

## The same from a terminal
`gea torque-drag --workspace C:\site`, `gea hydraulics --workspace C:\site`, `gea pore-pressure --workspace C:\site`, `gea casing --workspace C:\site`.

Help: torque-drag, hydraulics, pore-pressure, casing
