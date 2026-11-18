; VYUK installer — Wave 10 (v5.65) Leaf 1, PJ-61.
;
; Inno Setup 6 script (requires Inno Setup 6.3 or later; Inno 5
; cannot run the uninstall [Code] below). Compiles on Windows
; only — see WINDOWS_BUILD_CHECKLIST.md. This VM cannot compile
; it and no .exe is claimed from it.
;
; Wizard flow (Aura-matched intent: deep blue/teal art + colours):
;   branded Welcome (wizard_image.bmp) -> licence (EULA.txt)
;   -> Installing progress -> Finished with "Launch VYUK".
; Dir, program-group and Ready pages are disabled: this is a
; per-user, one-screen-then-progress install, not a questionnaire.
; The Finished page stays, because done-and-launch is the point.
;
; Version: AppVersion 6.3.0 tracks core/version.py VERSION (Gate B
; correction: the ISS number had drifted to 5.65.0 while the
; runtime moved on; the runtime version itself was not changed).
;
; Paths are relative to this script's directory (installer/), so
; "..\" is the tree root. The frozen exe is the ONLY input that
; does not exist before the freeze step (dist\vyuk\vyuk.exe +
; dist\vyuk\_internal\* are produced by installer\vyuk.spec);
; every other Source below must already exist in the tree, and
; the test suite (test_v565_leaf1.py) verifies that.
;
; Deliberately absent:
; * [UninstallDelete] for data/ — user data is NEVER deleted by
;   a blanket directive. PF-24: the uninstaller asks (custom
;   page, [Code] below) and deletes data/ only on the explicit
;   delete-my-data choice. The default is keep.
; * Any Source under data/ — user data is created by the app at
;   first run and is never shipped inside the installer.

[Setup]
AppId={{372B5186-6FF8-481E-A5FA-EEF593A5BC5F}
AppName=VYUK
AppVersion=6.3.0
AppVerName=VYUK 6.3.0
AppPublisher=NovaAI
DefaultDirName={userpf}\VYUK
DefaultGroupName=VYUK
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
LicenseFile=..\EULA.txt
SetupIconFile=..\gui\assets\vyuk.ico
WizardStyle=modern
WizardImageFile=wizard_image.bmp
WizardSmallImageFile=wizard_small.bmp
OutputDir=Output
OutputBaseFilename=VYUK-Setup-6.3.0
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=VYUK

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Frozen app (produced by installer\vyuk.spec — see the header
; note: the only inputs that do not pre-exist in the tree).
Source: "..\dist\vyuk\vyuk.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\vyuk\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Install integrity manifest + signature (from the spec datas
; output — Gate B, WIR-07 — so the packaged app can verify
; itself against core/integrity.py).
Source: "..\dist\vyuk\install_manifest.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\vyuk\install_manifest.json.sig"; DestDir: "{app}"; Flags: ignoreversion
; Shipped documents (tree files, verified by the test suite).
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\EULA.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\VYUK"; Filename: "{app}\vyuk.exe"
Name: "{group}\Uninstall VYUK"; Filename: "{uninstallexe}"
Name: "{autodesktop}\VYUK"; Filename: "{app}\vyuk.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\vyuk.exe"; Description: "{cm:LaunchProgram,VYUK}"; Flags: nowait postinstall skipifsilent

[Code]
var
  UninstallDataPage: TInputOptionPage;
  UninstallDeleteData: Boolean;

procedure InitializeUninstall();
begin
  { PF-24: ask, don't assume. Default is KEEP (index 0); data is
    deleted only if the user explicitly picks index 1 below.
    The page creation is guarded: if this Inno build's uninstall
    wizard ever refuses the page, the uninstall still proceeds
    and the safe default (keep) holds — data is never deleted
    without an explicit choice. Windows validation of the page
    rendering is owed (see WINDOWS_BUILD_CHECKLIST.md). }
  UninstallDeleteData := False;
  UninstallDataPage := nil;
  try
    UninstallDataPage := CreateInputOptionPage(
      wpWelcome,
      'Remove your VYUK data?',
      'VYUK stores your data separately from the program files.',
      'Choose what happens to your data when VYUK is removed. ' +
      'Keeping it means a later reinstall finds your history, ' +
      'settings and voices where you left them.',
      True, False);
    UninstallDataPage.Add(
      'Keep my data (recommended)');
    UninstallDataPage.Add(
      'Delete my data (history, settings, voices, logs)');
    UninstallDataPage.SelectedValueIndex := 0;
  except
    UninstallDataPage := nil;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  { Read the choice only if the page actually exists; a missing
    page means no explicit delete choice was ever made. }
  if UninstallDataPage <> nil then
  begin
    if UninstallDataPage.SelectedValueIndex = 1 then
      UninstallDeleteData := True;
  end;
  if not UninstallDeleteData then
    Exit;
  { Explicit delete choice only. Two locations, because the
    frozen layout resolves the app's data dir under _internal
    (core/config.py builds DATA_DIR from the frozen module's
    location), while a source-style install keeps data/ beside
    the exe. Both are user data; neither is a program file. }
  DelTree(ExpandConstant('{app}\data'), True, True, True);
  DelTree(ExpandConstant('{app}\_internal\data'), True, True, True);
end;
