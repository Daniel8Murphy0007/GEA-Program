# 18. The drill string, and the well drilled again
Who: office
Where: #/drillstring
Pages: #/simulation
Takes: 30 minutes
> Each run's string from the bit up with every component's hours against its service interval, and the whole well replayed on its own clock.

## Steps
1. Open **Drill string** and pick a well. Under **Scene**, look at the worked example first - a Volve well from the public record - to see a declared run.
2. **Declare a run** (the operator role): the components from the bit up - the class from the tool library, the maker, the model, the serial, the OD and the length - and the run's span on the recorder's clock. For a motor, a catalogued power section or its own figures. Give the source: the BHA sheet, the tally.
3. The page draws the build diagram and counts each component's hours and footage over the run against its service interval. A component past its interval raises a `motor.due` alert.
4. For a run with a motor: the differential pressure, the stalls and the stability from the start of the run to its end - what the power section was doing as it wore.
5. **Write the report** writes the run with its declarations and their sources.
6. Open **Simulation** and pick a well. **Play** runs the record on the recorder's clock - the whole of it in two minutes, or 4, 16 or 64 times faster: the bit moves down its own path in 3D while the mud-log panel beside it grows with the hole. The slider and the step buttons move through it; click the panel at a depth to take the bit to the moment it first got there.

## On the practice pad
On the public site ({lesson:practice-pad}) open **Simulation** on 16A(78)-32 and play it through its curve: the bit leaves the vertical near 5,900 ft and turns east. That well's recorder carried no bit depth, so the page marks it as reconstructed - exact on bottom, approximate over a trip.

## You know it worked when
The declared run shows no *declare it* placeholder in its tiles and each component has hours beside its interval; the Simulation's bit reaches the well's total depth at the end of the record.

## The same from a terminal
`gea drillstring --workspace C:\site`, `gea simulation --workspace C:\site`.

Help: drillstring, simulation
