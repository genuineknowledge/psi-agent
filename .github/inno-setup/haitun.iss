; Inno Setup script for HaiTun Agent.
; Packages the entire haitun (including psi-agent.exe, copied in at build time).

#define MyAppName "HaiTun Agent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Hefei Zhenzhi Artificial Intelligence Application Software Co., Ltd"
#define MyAppExeName "haitun.exe"

[Setup]
AppId={{234DFAA2-39F9-4E4C-92C7-680728ADDA4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\haitun.ico
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=HaiTun Agent Setup
SetupIconFile=haitun.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\examples\haitun\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "haitun.ico"; DestDir: "{app}"
Source: "haitun.exe"; DestDir: "{app}"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\haitun.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\haitun.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: shellexec postinstall skipifsilent
