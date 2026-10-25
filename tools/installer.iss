; This Source Code Form is subject to the terms of the Mozilla Public
; License, v. 2.0. If a copy of the MPL was not distributed with this
; file, You can obtain one at https://mozilla.org/MPL/2.0/.
;
; GEA-Program-Setup-<version>.exe - the Windows installer, built from the install kit (tools/build_installer.py)
; by Inno Setup 6 in the build workflow:
;
;   ISCC.exe /DAppVersion=0.65.0 /DKitDir=dist\setup-src\gea-program-0.65.0-win64 /DOutDir=dist tools\installer.iss
;
; What it does: copies the kit for the signed-in user alone (no administrator rights, nothing outside the user's
; own folders), runs the kit's install.cmd (the package and its dependencies into the kit's own Python, from the
; kit's own wheels - no network), records the site workspace the user chose (and makes it, with the site name
; asked for, when the folder holds none yet), and adds the Start-menu shortcuts,
; a desktop shortcut and, when chosen, a start at sign-in. A newer setup installs over an older one in place; the
; uninstaller removes the program and what install.cmd added to it.
;
; What it never does: change, move or delete the site workspace (the wells, reports, records, accounts and keys).
; The workspace is a folder of its own, outside the program's folder; the setup will not take one inside it. An
; existing workspace is used exactly as it is, its name included (renamed on the Administration page, if at all).
;
; Silent install, for a site's software deployment (the CI test runs exactly this):
;   GEA-Program-Setup-0.67.1.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /WORKSPACE="C:\site" /SITENAME="Pad 3" /TASKS="desktopicon"
; (/SITENAME names a workspace the setup makes; a workspace that exists keeps its own name)

#ifndef AppVersion
  #error AppVersion is not defined: ISCC /DAppVersion=0.65.0 /DKitDir=... /DOutDir=... tools\installer.iss
#endif
#ifndef KitDir
  #error KitDir is not defined: the kit folder made by tools/build_installer.py, before install.cmd has run in it
#endif
#ifndef OutDir
  #define OutDir "dist"
#endif

[Setup]
; the AppId is the installation's identity in Add/Remove Programs: the same in every version, for good
AppId={{10447A6D-DAB5-409D-9E26-A0A8AC96E138}
AppName=GEA-Program
AppVersion={#AppVersion}
AppVerName=GEA-Program {#AppVersion}
AppPublisher=ENRGYONE (Daniel T. Murphy)
AppPublisherURL=https://github.com/Daniel8Murphy0007/GEA-Program
AppSupportURL=https://github.com/Daniel8Murphy0007/GEA-Program/issues
AppUpdatesURL=https://github.com/Daniel8Murphy0007/GEA-Program/releases
AppCopyright=(c) 2026 Daniel T. Murphy / ENRGYONE - Mozilla Public License 2.0
VersionInfoVersion={#AppVersion}
VersionInfoProductName=GEA-Program
VersionInfoDescription=GEA-Program setup
DefaultDirName={userpf}\GEA-Program
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=GEA-Program-Setup-{#AppVersion}
SetupIconFile={#KitDir}\gea.ico
UninstallDisplayIcon={app}\gea.ico
UninstallDisplayName=GEA-Program {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Tasks]
Name: "desktopicon"; Description: "A GEA-Program shortcut on the desktop"; GroupDescription: "Shortcuts:"
Name: "startlogon"; Description: "Start the control panel when I sign in to Windows (in the background; its icon in the notification area)"; GroupDescription: "Starting:"; Flags: unchecked

[InstallDelete]
; an upgrade starts from a clean Python and clean wheels: what install.cmd put there for the older version goes
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\wheels"
Type: filesandordirs; Name: "{app}\report-samples"
Type: files; Name: "{app}\INSTALLED.txt"

[Files]
Source: "{#KitDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GEA-Program\GEA-Program"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea launch"; WorkingDir: "{app}"; IconFilename: "{app}\gea.ico"; AppUserModelID: "ENRGYONE.GEAProgram"; Comment: "Open the GEA-Program control panel (starts it when it is not running)"
Name: "{autoprograms}\GEA-Program\Stop GEA-Program"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea launch --stop"; WorkingDir: "{app}"; IconFilename: "{app}\gea.ico"; Comment: "Stop the GEA-Program control panel"
Name: "{autoprograms}\GEA-Program\Verify GEA-Program (acceptance gate)"; Filename: "{cmd}"; Parameters: "/k ""{app}\verify.cmd"""; WorkingDir: "{app}"; Comment: "Run every check of the acceptance gate from this installation"
Name: "{autoprograms}\GEA-Program\Site workspace folder"; Filename: "{code:WorkspaceDir}"; Comment: "The site's data: wells, reports, records, accounts"
Name: "{autoprograms}\GEA-Program\Read me"; Filename: "{app}\README.txt"
Name: "{autoprograms}\GEA-Program\Uninstall GEA-Program"; Filename: "{uninstallexe}"; Comment: "Remove the program (the site workspace stays)"
Name: "{autodesktop}\GEA-Program"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea launch"; WorkingDir: "{app}"; IconFilename: "{app}\gea.ico"; AppUserModelID: "ENRGYONE.GEAProgram"; Comment: "Open the GEA-Program control panel"; Tasks: desktopicon
Name: "{userstartup}\GEA-Program"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea launch --background"; WorkingDir: "{app}"; IconFilename: "{app}\gea.ico"; Comment: "Start the GEA-Program control panel at sign-in"; Tasks: startlogon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m gea launch"; WorkingDir: "{app}"; Description: "Open the GEA-Program control panel now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; the control panel, its window and its icon are stopped before their files are removed
Filename: "{cmd}"; Parameters: "/c """"{app}\stop-dashboard.cmd"" --all"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopGEAProgram"

[UninstallDelete]
; what install.cmd and the program added inside the program's own folder - never anything outside it
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\INSTALLED.txt"
Type: files; Name: "{app}\install.log"
Type: files; Name: "{app}\site-workspace.txt"
Type: dirifempty; Name: "{app}"

[Code]
var
  WsPage: TInputDirWizardPage;
  NamePage: TInputQueryWizardPage;
  InstallFailed: Boolean;
  InstallFailedWhy: String;

function DefaultWorkspace(): String;
begin
  Result := ExpandConstant('{param:WORKSPACE|}');
  if Result = '' then Result := GetPreviousData('Workspace', '');
  if Result = '' then Result := GetEnv('GEA_WORKSPACE');
  if (Result = '') and FileExists('C:\site\workspace.json') then Result := 'C:\site';
  if Result = '' then Result := ExpandConstant('{localappdata}\GEA-Program\site');
end;

function WorkspaceDir(Param: String): String;
begin
  if WsPage <> nil then
    Result := RemoveBackslashUnlessRoot(Trim(WsPage.Values[0]))
  else
    Result := RemoveBackslashUnlessRoot(Trim(DefaultWorkspace()));
end;

function IsInside(const Child, Parent: String): Boolean;
var
  C, P: String;
begin
  C := AddBackslash(Lowercase(Child));
  P := AddBackslash(Lowercase(Parent));
  Result := (Copy(C, 1, Length(P)) = P);
end;

function IsFullPath(const S: String): Boolean;
begin
  Result := False;
  if Length(S) >= 3 then begin
    if (S[2] = ':') and (S[3] = '\') then Result := True;
  end;
  if Copy(S, 1, 2) = '\\' then Result := True;
end;

function IsDriveRoot(const S: String): Boolean;
begin
  Result := False;
  if Length(S) <= 3 then begin
    if Length(S) >= 2 then begin
      if S[2] = ':' then Result := True;
    end;
  end;
end;

{ why a folder cannot be the workspace, or an empty string - the program's folder is replaced by an upgrade and removed by the uninstaller }
function WorkspaceProblem(const Ws, App: String): String;
begin
  Result := '';
  if Ws = '' then
    Result := 'Choose a folder for the site workspace.'
  else if not IsFullPath(Ws) then
    Result := Ws + ' is not a full path (a drive letter or a \\server\share first).'
  else if IsDriveRoot(Ws) then
    Result := Ws + ' is the root of a drive; choose a folder on it (for example C:\site).'
  else if IsInside(Ws, App) then
    Result := Ws + ' is inside the program''s own folder (' + App + '), which an upgrade replaces and the uninstaller removes. Keep the site''s data in a folder of its own.'
  else if IsInside(App, Ws) then
    Result := 'The program''s own folder (' + App + ') is inside ' + Ws + '. Keep the site''s data and the program apart.';
end;

procedure InitializeWizard();
begin
  WsPage := CreateInputDirPage(wpSelectDir,
    'Site workspace',
    'Where GEA-Program keeps the site''s data',
    'The wells, reports, records, accounts and keys live in this folder. Setup never changes or deletes it, ' +
    'and uninstalling the program leaves it where it is. An existing workspace (for example C:\site) is used as it is; ' +
    'a new one is made the first time the control panel starts.',
    False, '');
  WsPage.Add('');
  WsPage.Values[0] := DefaultWorkspace();
  NamePage := CreateInputQueryPage(WsPage.ID,
    'Site name',
    'The name on every report',
    'There is no workspace in that folder yet: setup makes one. Its name heads every report and the control panel ' +
    '(for example the pad, the field or the client''s site). Left empty, the workspace is named after its folder. ' +
    'The name can be changed later on the Administration page.');
  NamePage.Add('Site name:', False);
  NamePage.Values[0] := ExpandConstant('{param:SITENAME|}');
end;

function WorkspaceExists(): Boolean;
begin
  Result := FileExists(AddBackslash(WorkspaceDir('')) + 'workspace.json');
end;

function SiteName(): String;
begin
  Result := '';
  if NamePage <> nil then Result := Trim(NamePage.Values[0]);
end;

{ why a site name cannot be used, or an empty string; empty is allowed - the workspace is then named after its folder }
function SiteNameProblem(const S: String): String;
begin
  Result := '';
  if Length(S) > 80 then
    Result := 'A site name of 80 characters at most, please: it heads every report.'
  else if Pos('"', S) > 0 then
    Result := 'A site name holds no quotation marks.';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (NamePage <> nil) and (PageID = NamePage.ID) then
    Result := WorkspaceExists();
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Why: String;
begin
  Result := True;
  if (WsPage <> nil) and (CurPageID = WsPage.ID) then begin
    Why := WorkspaceProblem(WorkspaceDir(''), RemoveBackslashUnlessRoot(WizardDirValue()));
    if Why <> '' then begin
      SuppressibleMsgBox(Why, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
  if (NamePage <> nil) and (CurPageID = NamePage.ID) then begin
    Why := SiteNameProblem(SiteName());
    if Why <> '' then begin
      SuppressibleMsgBox(Why, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'Workspace', WorkspaceDir(''));
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine +
            'Site workspace (setup never changes or deletes it):' + NewLine + Space + WorkspaceDir('') + NewLine;
  if WorkspaceExists() then
    Result := Result + Space + '(it exists and is used as it is)' + NewLine
  else if SiteName() <> '' then
    Result := Result + Space + '(made by setup, named ' + SiteName() + ')' + NewLine
  else
    Result := Result + Space + '(made by setup, named after its folder)' + NewLine;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + MemoTasksInfo;
end;

{ the control panel, its window and its icon stop before their files are replaced }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Rc: Integer;
  Stopper, Why: String;
begin
  Result := '';
  Why := WorkspaceProblem(WorkspaceDir(''), RemoveBackslashUnlessRoot(ExpandConstant('{app}')));
  if Why = '' then Why := SiteNameProblem(SiteName());
  if Why <> '' then begin
    Result := Why;
    exit;
  end;
  Stopper := ExpandConstant('{app}\stop-dashboard.cmd');
  if FileExists(Stopper) then
    Exec(ExpandConstant('{cmd}'), '/c ""' + Stopper + '" --all"', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Rc);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Rc: Integer;
  App: String;
begin
  if CurStep = ssInstall then begin
    { the workspace's own folder, so its Start-menu shortcut has a folder to open; an existing one is left as it is }
    if not DirExists(WorkspaceDir('')) then
      ForceDirectories(WorkspaceDir(''));
  end;
  if CurStep = ssPostInstall then begin
    App := ExpandConstant('{app}');
    WizardForm.StatusLabel.Caption := 'Installing the program''s packages into its own Python (about a minute; no network)...';
    if not Exec(ExpandConstant('{cmd}'), '/c ""' + App + '\install.cmd" > "' + App + '\install.log" 2>&1"', App, SW_HIDE, ewWaitUntilTerminated, Rc) then begin
      InstallFailed := True;
      InstallFailedWhy := 'install.cmd could not be run (' + SysErrorMessage(Rc) + ')';
    end else if Rc <> 0 then begin
      InstallFailed := True;
      InstallFailedWhy := 'install.cmd ended with exit code ' + IntToStr(Rc) + '; its log is ' + App + '\install.log';
    end;
    if not InstallFailed then begin
      WizardForm.StatusLabel.Caption := 'Recording the site workspace (and making it, when it is new)...';
      { records the workspace; makes it, with its name, when there is none there yet - an existing one is left as it is }
      if not Exec(App + '\python\python.exe', '-m gea launch --set-workspace "' + WorkspaceDir('') + '" --site-name "' + SiteName() + '"', App, SW_HIDE, ewWaitUntilTerminated, Rc) or (Rc <> 0) then begin
        InstallFailed := True;
        InstallFailedWhy := 'the site workspace ' + WorkspaceDir('') + ' could not be recorded or made (exit ' + IntToStr(Rc) + ')';
      end;
    end;
    if InstallFailed then
      SuppressibleMsgBox('GEA-Program was copied but is not ready: ' + InstallFailedWhy + '.' + #13#10#13#10 +
                         'Run setup again, or run install.cmd in ' + App + ' and read what it says.', mbError, MB_OK, IDOK);
  end;
end;

{ a silent install that did not finish says so in its exit code: 8 }
function GetCustomSetupExitCode: Integer;
begin
  if InstallFailed then Result := 8 else Result := 0;
end;
