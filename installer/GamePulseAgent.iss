; Per-user installer for the GamePulse clip agent (no admin rights).
; Build: ISCC /DAppVersion=0.1.0 /DDistDir=..\dist\GamePulseAgent installer\GamePulseAgent.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #define DistDir "..\dist\GamePulseAgent"
#endif

[Setup]
AppId={{6F1C2B0E-4E1A-4C7B-9C55-3B9E2A7D4A10}
AppName=GamePulse Agent
AppVersion={#AppVersion}
AppPublisher=GamePulse
AppPublisherURL=https://gamepulsecoach.com
DefaultDirName={localappdata}\Programs\GamePulseAgent
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=GamePulseAgent-Setup-{#AppVersion}
OutputDir=..\dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
UninstallDisplayName=GamePulse Agent
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\Portuguese.isl"

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\GamePulse Agent"; Filename: "{app}\GamePulseAgent.exe"

[Registry]
; Start with Windows for this user only
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GamePulseAgent"; ValueData: """{app}\GamePulseAgent.exe"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\GamePulseAgent.exe"; Description: "Iniciar o GamePulse Agent"; Flags: nowait postinstall

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM GamePulseAgent.exe /F"; Flags: runhidden; RunOnceId: "StopAgent"
