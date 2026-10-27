; This Source Code Form is subject to the terms of the Mozilla Public
; License, v. 2.0. If a copy of the MPL was not distributed with this
; file, You can obtain one at https://mozilla.org/MPL/2.0/.
;
; GEA-Reader-Setup-<version>.exe - GEA Reader, the program a site gives its clients to check a signed dossier, as a
; Windows program of its own: built from the reader kit (tools/build_reader.py) by Inno Setup 6 in the build workflow:
;
;   ISCC.exe /DAppVersion=0.69.0 /DKitDir=dist\gea-reader-0.69.0-win64 /DOutDir=dist tools\reader_installer.iss
;
; What it does: copies the kit for the signed-in user alone (no administrator rights), adds GEA Reader to the Start
; menu (and, when chosen, the desktop) with its own icon and taskbar identity, and "Check with GEA Reader" to the
; right-click menu of .zip files. Nothing of GEA-Program is installed, and the reader reaches no network.
;
; Silent install, for a client's software deployment (the CI test runs exactly this):
;   GEA-Reader-Setup-0.69.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TASKS="desktopicon,zipverb"

#ifndef AppVersion
  #error AppVersion is not defined: ISCC /DAppVersion=0.69.0 /DKitDir=... /DOutDir=... tools\reader_installer.iss
#endif
#ifndef KitDir
  #error KitDir is not defined: the reader kit folder made by tools/build_reader.py
#endif
#ifndef OutDir
  #define OutDir "dist"
#endif

[Setup]
; GEA Reader's own identity in Add/Remove Programs: not GEA-Program's, the same in every version, for good
AppId={{9DCF59D6-4FC4-4CE3-B1C6-8399A4502268}
AppName=GEA Reader
AppVersion={#AppVersion}
AppVerName=GEA Reader {#AppVersion}
AppPublisher=ENRGYONE (Daniel T. Murphy)
AppPublisherURL=https://github.com/Daniel8Murphy0007/GEA-Program
AppSupportURL=https://github.com/Daniel8Murphy0007/GEA-Program/issues
AppUpdatesURL=https://github.com/Daniel8Murphy0007/GEA-Program/releases
AppCopyright=(c) 2026 Daniel T. Murphy / ENRGYONE - Mozilla Public License 2.0
VersionInfoVersion={#AppVersion}
VersionInfoProductName=GEA Reader
VersionInfoDescription=GEA Reader setup
DefaultDirName={userpf}\GEA Reader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=GEA-Reader-Setup-{#AppVersion}
SetupIconFile={#KitDir}\reader.ico
UninstallDisplayIcon={app}\reader.ico
UninstallDisplayName=GEA Reader {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
ChangesAssociations=yes

[Tasks]
Name: "desktopicon"; Description: "A GEA Reader shortcut on the desktop"; GroupDescription: "Shortcuts:"
Name: "zipverb"; Description: "Add ""Check with GEA Reader"" to the right-click menu of .zip files"; GroupDescription: "Explorer:"

[InstallDelete]
; an upgrade starts from a clean Python and a clean program
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"

[Files]
Source: "{#KitDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GEA Reader"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea_reader"; WorkingDir: "{app}"; IconFilename: "{app}\reader.ico"; AppUserModelID: "ENRGYONE.GEAReader"; Comment: "Check a signed GEA dossier and read its reports"
Name: "{autodesktop}\GEA Reader"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea_reader"; WorkingDir: "{app}"; IconFilename: "{app}\reader.ico"; AppUserModelID: "ENRGYONE.GEAReader"; Comment: "Check a signed GEA dossier and read its reports"; Tasks: desktopicon

[Registry]
; for the signed-in user alone; removed with the program
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.zip\shell\GEAReader"; ValueType: string; ValueName: ""; ValueData: "Check with GEA Reader"; Flags: uninsdeletekey; Tasks: zipverb
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.zip\shell\GEAReader"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\reader.ico"""; Tasks: zipverb
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.zip\shell\GEAReader\command"; ValueType: string; ValueName: ""; ValueData: """{app}\python\pythonw.exe"" -m gea_reader ""%1"""; Tasks: zipverb

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea_reader"; WorkingDir: "{app}"; Description: "Open GEA Reader now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; what Python wrote beside the program while it ran (its byte-code caches)
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
