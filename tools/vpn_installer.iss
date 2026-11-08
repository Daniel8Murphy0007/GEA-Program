; This Source Code Form is subject to the terms of the Mozilla Public
; License, v. 2.0. If a copy of the MPL was not distributed with this
; file, You can obtain one at https://mozilla.org/MPL/2.0/.
;
; GEA-VPN-Setup-<version>.exe - GEA-VPN, the client program of the site's private network, as a Windows program of
; its own: built from the kit (tools/build_vpn.py --win64) by Inno Setup 6 in the build workflow:
;
;   ISCC.exe /DAppVersion=0.83.0 /DKitDir=dist\gea-vpn-0.83.0-win64 /DOutDir=dist tools\vpn_installer.iss
;
; What it does (administrator rights, once): copies the kit under Program Files; installs WireGuard for Windows from
; the installer in the kit when the computer has none; makes the data folder for administrators only; registers the
; program as the scheduled task GEA-VPN (as SYSTEM, at start-up) and starts it; puts GEA-VPN in the Start menu (the
; page at http://127.0.0.1:8795/). Nothing connects until the office's connect information is given to the page.
; Uninstalling disconnects the tunnel, removes the task and the program, and leaves the data folder.
;
; Silent install, for a site's software deployment (the CI test runs exactly this):
;   GEA-VPN-Setup-0.83.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

#ifndef AppVersion
  #error AppVersion is not defined: ISCC /DAppVersion=0.83.0 /DKitDir=... /DOutDir=... tools\vpn_installer.iss
#endif
#ifndef KitDir
  #error KitDir is not defined: the kit folder made by tools/build_vpn.py --win64
#endif
#ifndef OutDir
  #define OutDir "dist"
#endif

[Setup]
; GEA-VPN's own identity in Add/Remove Programs, the same in every version, for good
AppId={{5B7E2C1A-4D0F-4A7B-9C3E-2F6A8D1E7B42}
AppName=GEA-VPN
AppVersion={#AppVersion}
AppVerName=GEA-VPN {#AppVersion}
AppPublisher=ENRGYONE (Daniel T. Murphy)
AppPublisherURL=https://github.com/Daniel8Murphy0007/GEA-Program
AppSupportURL=https://github.com/Daniel8Murphy0007/GEA-Program/issues
AppUpdatesURL=https://github.com/Daniel8Murphy0007/GEA-Program/releases
AppCopyright=(c) 2026 Daniel T. Murphy / ENRGYONE - Mozilla Public License 2.0
VersionInfoVersion={#AppVersion}
VersionInfoProductName=GEA-VPN
VersionInfoDescription=GEA-VPN setup
DefaultDirName={autopf}\GEA-VPN
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=GEA-VPN-Setup-{#AppVersion}
SetupIconFile={#KitDir}\vpn.ico
UninstallDisplayIcon={app}\vpn.ico
UninstallDisplayName=GEA-VPN {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Tasks]
Name: "desktopicon"; Description: "A GEA-VPN shortcut on the desktop"; GroupDescription: "Shortcuts:"

[InstallDelete]
; an upgrade starts from a clean Python and a clean program (the task is stopped first, in PrepareToInstall)
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\wireguard"

[Dirs]
; the data folder: administrators and SYSTEM only - the connect information holds this computer's key
Name: "{commonappdata}\GEA-VPN"; Permissions: admins-full system-full

[Files]
Source: "{#KitDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GEA-VPN"; Filename: "http://127.0.0.1:8795/"; IconFilename: "{app}\vpn.ico"; Comment: "The site's private network on this computer: the connect information, the tunnel, the hub and the office"
Name: "{autodesktop}\GEA-VPN"; Filename: "http://127.0.0.1:8795/"; IconFilename: "{app}\vpn.ico"; Comment: "The site's private network on this computer"; Tasks: desktopicon

[Run]
; WireGuard for Windows, from the installer the kit carries, when the computer has none
Filename: "msiexec.exe"; Parameters: "/i ""{app}\wireguard\wireguard-amd64-0.5.3.msi"" DO_NOT_LAUNCH=1 /qn /norestart"; StatusMsg: "Installing WireGuard for Windows..."; Flags: waituntilterminated; Check: NeedsWireGuard
; the program as the scheduled task GEA-VPN (SYSTEM, at start-up), started now
Filename: "{app}\python\python.exe"; Parameters: "-m gea_vpn install --data ""{commonappdata}\GEA-VPN"" --port 8795"; WorkingDir: "{app}"; StatusMsg: "Starting GEA-VPN..."; Flags: runhidden waituntilterminated
Filename: "http://127.0.0.1:8795/"; Description: "Open GEA-VPN now"; Flags: postinstall shellexec nowait skipifsilent

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: "-m gea_vpn disconnect --data ""{commonappdata}\GEA-VPN"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "disconnect"
Filename: "{app}\python\python.exe"; Parameters: "-m gea_vpn uninstall-service"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "untask"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\wireguard"

[Code]
function NeedsWireGuard(): Boolean;
begin
  Result := (not FileExists(ExpandConstant('{pf}\WireGuard\wireguard.exe'))) and FileExists(ExpandConstant('{app}\wireguard\wireguard-amd64-0.5.3.msi'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  rc: Integer;
begin
  { an upgrade: the running task holds python.exe open; stop it and remove it before the files are replaced }
  Exec('schtasks.exe', '/End /TN GEA-VPN', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Exec('schtasks.exe', '/Delete /TN GEA-VPN /F', '', SW_HIDE, ewWaitUntilTerminated, rc);
  Result := '';
end;
