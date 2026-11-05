@echo off
rem GEA Driller - install on a Windows rig PC, for the signed-in user (no administrator rights):  install.cmd
rem What it does: a Python environment of its own under %LOCALAPPDATA%\GEA-Driller with the package and the trained
rem policy, the example config beside it (left alone when one is there), the data folder, and start-gea-driller.cmd
rem to put in the Startup folder. Needs Python 3.9 or newer on the PATH (python.org, "Add to PATH" ticked).
setlocal
set "PREFIX=%LOCALAPPDATA%\GEA-Driller"
echo == GEA Driller: installing into %PREFIX%
python -m venv "%PREFIX%\venv" || exit /b 1
"%PREFIX%\venv\Scripts\python.exe" -m pip install --upgrade pip >nul
for %%w in ("%~dp0gea_driller-*.whl") do "%PREFIX%\venv\Scripts\python.exe" -m pip install "%%~w" || exit /b 1
"%PREFIX%\venv\Scripts\python.exe" -m pip install pyserial >nul 2>&1
if not exist "%PREFIX%\config.json" "%PREFIX%\venv\Scripts\python.exe" -m gea_driller config --write "%PREFIX%\config.json"
if not exist "%PREFIX%\data" mkdir "%PREFIX%\data"
"%PREFIX%\venv\Scripts\python.exe" -m gea_driller selftest --seconds 8 || exit /b 1
"%PREFIX%\venv\Scripts\python.exe" -m gea_driller install --config "%PREFIX%\config.json"
echo.
echo == done. Next:
echo    1. edit %PREFIX%\config.json: the well and the site, the rig feed (tcp: the EDR's WITS0 host and port; serial: COM3),
echo       the pre-drill model, the tool, the limits; set output.dir to %PREFIX%\data
echo    2. double-click %PREFIX%\start-gea-driller.cmd (and put a shortcut to it in the Startup folder)
echo    3. the driller's screen: http://127.0.0.1:8790/live on this PC; GEA pulls the decisions from this PC's address
endlocal
