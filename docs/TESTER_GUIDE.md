# HOW TO TEST GEA — FOLLOW EACH STEP EXACTLY

*You do not need to know anything about programming. Follow the steps in
order. Every click and every keystroke is written out. (Field-tested: the
command in Part 4 is the form that works on every Windows machine,
including ones where the install shows yellow "not on PATH" warnings —
those warnings are harmless with this guide.)*

---

## PART 1 — INSTALL PYTHON (one time only)

1. Open your web browser (Edge or Chrome).
2. Click in the address bar at the top, type exactly: **python.org/downloads**
   and press **Enter**.
3. Click the big yellow button that says **Download Python 3.x** (whatever
   number it shows is fine).
4. When the download finishes, click **Open file** on the download.
5. An install window opens. **STOP. Look at the bottom of that window.**
6. Click the small square checkbox next to **"Add python.exe to PATH"** so
   it shows a checkmark. *(If you forget this, the guide still works — the
   command in Part 4 is chosen so it does not matter.)*
7. Click **Install Now**.
8. If a box asks "Do you want to allow this app to make changes?" click **Yes**.
9. Wait for **Setup was successful**. Click **Close**.

## PART 2 — OPEN THE COMMAND WINDOW

10. Press the **Windows key** on your keyboard.
11. Type: **powershell**
12. Press **Enter**. A blue or black window with white text opens. You will
    type everything below into this window.

## PART 3 — INSTALL GEA (one time only)

13. Click inside the window. Type exactly this, then press **Enter**:

        pip install gea-program      (or, from a checkout of this repository:  pip install .)

14. Text will scroll for a minute or two. Wait until it stops and you see a
    line starting with **Successfully installed**. Yellow "WARNING ... not
    on PATH" lines may appear — **ignore them**, they are handled by the
    next step.

## PART 4 — RUN THE TEST

15. Type exactly this, then press **Enter**:

        python -m gea.cli survey --demo

16. In a few seconds a report appears that starts with:

        GEA SURVEY - one well, one honest answer

## PART 5 — YOU'RE DONE. CHECK THESE THREE THINGS:

17. The report shows real data from a deep borehole in Germany (the KTB
    scientific well, public archive).
18. Find the line starting with **CROSS-CHECK**. The program checks its own
    answer against the well's real measurement — it should say about **+0.7%**.
19. Find the section **"what this tool refused to guess."** That is on
    purpose. This program tells you when it doesn't know something instead
    of making it up.

## USING YOUR OWN WELL DATA (optional)

If you have a LAS well-log file, type (with your own file's location):

        python -m gea.cli survey C:\path\to\yourwell.las

If the file is missing the needed measurements, the program will say
exactly what is missing rather than guessing.

## USING THE DASHBOARD IN A BROWSER (optional)

The command window is not the only way. In the command window, type these two
lines (change `C:\gea-site` to any folder you like):

    gea workspace --path C:\gea-site --action init --name "My site"
    gea serve --workspace C:\gea-site

Then open **http://127.0.0.1:8765/** in your browser. The first time, it asks
you to create the administrator (your name and a password of 8 or more
characters). After that: **Wells** -> "Add a well from a file" -> choose your
historian CSV or LAS file -> "Add file well". Then **Home** -> "Refresh every
report". Every tile and every row opens the report behind it; **Jobs** shows
every run with its log if something fails; **Verification** -> "Run the
acceptance suite" runs the same 189-check gate from the page. Close the
command window to stop the service; your folder keeps everything.

To see live data without a rig: open a second command window and type
`gea wits0-sim --port 5001`. Then, in the dashboard, **Patch panel** -> "Add a
patch": name `floor`, protocol `wits0`, your well, "Load the example map",
"Add and start". Within a few seconds the patch shows CONNECTED and the
live values table fills with hookload, standpipe pressure, bit depth and
rate of penetration from the simulated floor.

## IF SOMETHING GOES WRONG

- If step 15 says **"python is not recognized"**: type the same command
  with `py` instead of `python`:

        py -m gea.cli survey --demo

- If step 13 says **"pip is not recognized"**: close the window, redo
  Part 1 and make sure the checkbox in step 6 is checked, then start
  again from Part 2.
- Anything else: take a photo of the whole screen and send it in.

## WHAT WE WANT TO HEAR FROM YOU

Did the install work on the first try? Was the report understandable?
Did anything confuse you? Send answers (and screen photos) to:
**daniel.murphy00@enrgyone.com**

---

*GEA-Program — ENRGYONE. The report's closing line is the product's
contract: every number with its basis, or not at all.*
