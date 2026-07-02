; Local Windows installer for the integrated Haitun bundle.

#ifndef AppStage
  #error AppStage preprocessor define is required. Pass /DAppStage=...
#endif

#ifndef AppOutputDir
  #error AppOutputDir preprocessor define is required. Pass /DAppOutputDir=...
#endif

#ifndef MyAppVersion
  #define MyAppVersion "dev"
#endif

#define MyAppName "Haitun Agent"
#define MyAppPublisher "psi-agent"
#define MyAppExeName "haitun agent.vbs"

[Setup]
AppId={{234DFAA2-39F9-4E4C-92C7-680728ADDA4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\haitun.ico
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#AppOutputDir}
OutputBaseFilename=Haitun Agent Setup
SetupIconFile={#AppStage}\haitun.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppStage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\haitun.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\haitun.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: shellexec postinstall skipifsilent
