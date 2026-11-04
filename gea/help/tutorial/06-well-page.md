# 6. The well page: charts, drift, quality and well tests
Who: office
Where: #/wells
Takes: 30 minutes
> What the program says about a gauge's record and how to read it.

## Steps
1. Open **Wells** and click a well. The page is a stack of cards; each report has a **more...** to its full text and a **save to...** that copies it to the export root.
2. **Charts**: choose up to four channels, one per track. Point at the chart for every channel's value at that moment; drag across a stretch to zoom into it; the wheel zooms where you point; double-click shows everything. On a drilling recorder's well the axis switches between **Time** and **Depth**, the depth events are marked where they happened, and each section of recording is a button.
3. **Quality**: every sample carries a flag and the rule that set it - GOOD, RANGE (outside the engineering range), ROC (rate of change over the limit), FLATLINE, SPIKE, STALE, GAP. The limits come from the gauge's own datasheet in the gauge library; **Tools & ports** lists them.
4. **Drift**: the residual against the well model and the gauge's aging band. The labels mean what they say: *within the band* is not *correct*, and the report says which.
5. **Instruments**: a swapped gauge is a new instrument. **Record a swap** (tag, time, old and new serial, certificate id) when one is changed, or **Confirm** a candidate the step detector proposes. **File a certificate** (serial, laboratory, issued, valid until, accuracy, the document); a certificate in its last 60 days raises an alert.
6. **Well tests**: stable periods found by the criteria in `criteria.json` (Configuration tab); every rejected period names the rule. A test needs **two approvals** - the Approvals tab lists what is waiting; an approver signs, a second approver signs.
7. **Shut-ins and build-ups**: every shut-in in the record gets a first-look build-up analysis. Save the rock and fluid parameters (q, B, mu, h, phi, c_t, r_w) on the card; without them the result is the slope and p* only.

## You know it worked when
You can point at the chart and name the moment an alarm fired, and the drift card says which band the gauge is in and why.

Help: charts, quality, drift, instruments, well-tests, transients
