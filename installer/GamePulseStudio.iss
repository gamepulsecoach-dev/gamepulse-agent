; Per-user installer for GamePulse Studio, the local clip exporter (no admin rights).
; Build: ISCC /DAppVersion=0.7.0 /DDistDir=..\dist\GamePulseStudio installer\GamePulseStudio.iss
; Until 0.6.x this was "GamePulse Agent": the AppId is unchanged so this installer
; upgrades it in place, and the old exe, shortcut and autostart entry are removed.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\GamePulseStudio"
#endif

[Setup]
AppId={{6F1C2B0E-4E1A-4C7B-9C55-3B9E2A7D4A10}
AppName=GamePulse Studio
AppVersion={#AppVersion}
AppPublisher=GamePulse
AppPublisherURL=https://gamepulsecoach.com
DefaultDirName={localappdata}\Programs\GamePulseStudio
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=GamePulseStudio-Setup-{#AppVersion}
OutputDir=..\dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
UninstallDisplayName=GamePulse Studio
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\Portuguese.isl"

[InstallDelete]
; Leftovers from "GamePulse Agent" (<= 0.6.x)
Type: files; Name: "{app}\GamePulseAgent.exe"
Type: files; Name: "{userprograms}\GamePulse Agent.lnk"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\GamePulse Studio"; Filename: "{app}\GamePulseStudio.exe"

[Registry]
; Start with Windows for this user only
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GamePulseStudio"; ValueData: """{app}\GamePulseStudio.exe"""; Flags: uninsdeletevalue
; Old autostart entry would launch the removed GamePulseAgent.exe
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "GamePulseAgent"; Flags: deletevalue

[Run]
Filename: "{app}\GamePulseStudio.exe"; Description: "Iniciar o GamePulse Studio"; Flags: nowait postinstall

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM GamePulseStudio.exe /F"; Flags: runhidden; RunOnceId: "StopStudio"

[Code]
// The old exe is not one of the files being replaced, so CloseApplications
// does not stop it: kill it before [InstallDelete] tries to remove it.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM GamePulseAgent.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
