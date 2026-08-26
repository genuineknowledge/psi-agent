; Inno Setup script for HaiTun Agent component installers.
;
; One script builds three packages:
;   - COMPONENT_APP    -> HaiTun_Agent_App_Setup.exe (app only)
;   - COMPONENT_MSYS   -> msys-setup.exe (environment only)
;   - (default)        -> HaiTun_Agent_Setup.exe (full install)

#define MyAppName "HaiTun Agent"
#define MyAppVersion "1.0.10"
#define MyMsysVersion "env-1"
#define MyAppPublisher "Hefei Zhenzhi Artificial Intelligence Application Software Co., Ltd"
#define MyAppExeName "haitun.exe"

[Setup]
AppId={{234DFAA2-39F9-4E4C-92C7-680728ADDA4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\app\haitun.ico
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
#ifdef COMPONENT_MSYS
OutputBaseFilename=msys-setup
#else
#ifdef COMPONENT_APP
OutputBaseFilename=HaiTun_Agent_App_Setup
#else
OutputBaseFilename=HaiTun_Agent_Setup
#endif
#endif
SetupIconFile=haitun.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinesesimplified.LegalPageCaption=许可协议与隐私保护政策
chinesesimplified.LegalPageDesc=安装前请阅读并同意以下协议
chinesesimplified.LegalIntro=请点击下方链接阅读协议全文。勾选即表示您已阅读并同意两份协议的全部内容。
chinesesimplified.LegalTerms=《Haitun Agent 软件许可及服务协议》
chinesesimplified.LegalPrivacy=《Haitun Agent 隐私保护政策》
chinesesimplified.LegalAgree=我已阅读并同意上述协议
english.LegalPageCaption=License Agreement and Privacy Policy
english.LegalPageDesc=Please read and accept the agreements before installing
english.LegalIntro=Click the links below to read the full text. Checking the box means you have read and accepted both agreements. (Chinese only.)
english.LegalTerms=Haitun Agent Software License and Service Agreement
english.LegalPrivacy=Haitun Agent Privacy Policy
english.LegalAgree=I have read and agree to the agreements above

#ifndef COMPONENT_MSYS
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
#endif

[Files]
#ifdef COMPONENT_MSYS
Source: "..\..\examples\haitun-workspace\msys64\*"; DestDir: "{app}\msys64"; Flags: ignoreversion recursesubdirs createallsubdirs
#else
; .env 由 CI 打包前从 GitHub Secret SERPER_API_KEY 注入到 examples\haitun-workspace\.env，随 workspace 一并安装到 {app}\app。
Source: "..\..\examples\haitun-workspace\*"; DestDir: "{app}\app"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "msys64"
Source: "haitun.ico"; DestDir: "{app}\app"
Source: "haitun.exe"; DestDir: "{app}\app"
#ifdef COMPONENT_APP
#else
Source: "..\..\examples\haitun-workspace\msys64\*"; DestDir: "{app}\msys64"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
#endif
Source: "rollback.cmd"; DestDir: "{app}"
Source: "rollback.ps1"; DestDir: "{app}"
Source: "rollback-state.json"; DestDir: "{app}"; Flags: onlyifdoesntexist
; 协议页要读的三个文件。dontcopy = 只打进安装包供向导页临时解出, 不装到 {app}
; —— 产品内那份走 spa-v2/dist（vite 会把 public/* 拷进去）, 装两份必有一份过时。
; 这三个是 scripts/gen_legal_html.py 的产物, 改 docs/ 下的 md 后需重新生成。
Source: "..\..\src\psi_agent\gateway\spa-v2\public\terms.html"; Flags: dontcopy
Source: "..\..\src\psi_agent\gateway\spa-v2\public\privacy.html"; Flags: dontcopy
Source: "..\..\src\psi_agent\gateway\spa-v2\public\legal.css"; Flags: dontcopy

#ifndef COMPONENT_MSYS
[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"; IconFilename: "{app}\app\haitun.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\app\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\app\haitun.ico"
#endif

[UninstallDelete]
Type: filesandordirs; Name: "{app}\*"
Type: filesandordirs; Name: "{app}"

[Code]
{ ---- 协议页 ----
  许可协议导言写明「您在本软件安装过程中勾选同意本协议, 即视为您同时同意隐私保护政策」,
  所以是一个勾选框覆盖两份, 而非各勾一个 —— 这也是不能用内置 LicenseFile 的原因:
  它是单选钮, 且一次只挂一份文件。 }
var
  LegalPage: TWizardPage;
  LegalAgreeCheck: TNewCheckBox;
  LegalFilesExtracted: Boolean;
  PrevPageID: Integer;
  PendingStateJSON: String;

procedure OpenLegalDoc(const FileName: String);
var
  ResultCode: Integer;
begin
  if not LegalFilesExtracted then
  begin
    ExtractTemporaryFile('legal.css');
    ExtractTemporaryFile('terms.html');
    ExtractTemporaryFile('privacy.html');
    LegalFilesExtracted := True;
  end;
  if not ShellExec('open', ExpandConstant('{tmp}\') + FileName,
                   '', '', SW_SHOWNORMAL, ewNoWait, ResultCode) then
    MsgBox('无法打开协议文件，请检查系统默认浏览器设置。', mbError, MB_OK);
end;

procedure LegalTermsClick(Sender: TObject);
begin
  OpenLegalDoc('terms.html');
end;

procedure LegalPrivacyClick(Sender: TObject);
begin
  OpenLegalDoc('privacy.html');
end;

procedure UpdateNextButtonState;
begin
  WizardForm.NextButton.Enabled := LegalAgreeCheck.Checked;
end;

procedure LegalAgreeClick(Sender: TObject);
begin
  UpdateNextButtonState;
end;

function CreateLegalLink(const Caption: String; ATop: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(LegalPage);
  Result.Parent := LegalPage.Surface;
  Result.Caption := Caption;
  Result.Top := ATop;
  Result.Left := ScaleX(8);
  Result.Cursor := crHand;
  Result.Font.Color := clBlue;
  Result.Font.Style := [fsUnderline];
end;

procedure CreateLegalPage;
var
  Intro: TNewStaticText;
  TermsLink: TNewStaticText;
  PrivacyLink: TNewStaticText;
begin
  LegalPage := CreateCustomPage(wpWelcome,
    ExpandConstant('{cm:LegalPageCaption}'), ExpandConstant('{cm:LegalPageDesc}'));

  Intro := TNewStaticText.Create(LegalPage);
  Intro.Parent := LegalPage.Surface;
  Intro.AutoSize := False;
  Intro.WordWrap := True;
  Intro.Left := 0;
  Intro.Top := 0;
  Intro.Width := LegalPage.SurfaceWidth;
  Intro.Height := ScaleY(34);
  Intro.Caption := ExpandConstant('{cm:LegalIntro}');

  TermsLink := CreateLegalLink(ExpandConstant('{cm:LegalTerms}'), ScaleY(48));
  TermsLink.OnClick := @LegalTermsClick;
  PrivacyLink := CreateLegalLink(ExpandConstant('{cm:LegalPrivacy}'), ScaleY(72));
  PrivacyLink.OnClick := @LegalPrivacyClick;

  LegalAgreeCheck := TNewCheckBox.Create(LegalPage);
  LegalAgreeCheck.Parent := LegalPage.Surface;
  LegalAgreeCheck.Left := 0;
  LegalAgreeCheck.Top := ScaleY(108);
  LegalAgreeCheck.Width := LegalPage.SurfaceWidth;
  LegalAgreeCheck.Height := ScaleY(20);
  LegalAgreeCheck.Caption := ExpandConstant('{cm:LegalAgree}');
  LegalAgreeCheck.OnClick := @LegalAgreeClick;
end;

procedure InitializeWizard;
begin
  LegalFilesExtracted := False;
  PrevPageID := -1;
  PendingStateJSON := '';
  CreateLegalPage;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = LegalPage.ID then
    UpdateNextButtonState
  else if PrevPageID = LegalPage.ID then
    WizardForm.NextButton.Enabled := True;
  PrevPageID := CurPageID;
end;

{ ---- 目录换新与回滚状态 ---- }

function ReadTextFileTrim(const FileName: String): String;
var
  S: AnsiString;
begin
  Result := '';
  if FileExists(FileName) and LoadStringFromFile(FileName, S) then
    Result := Trim(S);
end;

function ComponentDir(const Name: String): String;
begin
  Result := ExpandConstant('{app}\') + Name;
end;

function SwapComponent(const Name: String): Boolean;
var
  Cur, Backup: String;
  i: Integer;
begin
  Cur := ComponentDir(Name);
  Backup := Cur + '.backup';
  if not DirExists(Cur) then
  begin
    Result := True;
    Exit;
  end;
  i := 1;
  while i <= 5 do
  begin
    if DirExists(Backup) then
      DelTree(Backup, True, True, True);
    if not DirExists(Backup) then
      i := 6
    else
    begin
      Sleep(1000);
      i := i + 1;
    end;
  end;
  if DirExists(Backup) then
  begin
    Result := False;
    Exit;
  end;
  Result := RenameFile(Cur, Backup);
  if not Result then
  begin
    Sleep(1000);
    Result := RenameFile(Cur, Backup);
  end;
end;

function WriteStateFile(const Content: String): Boolean;
var
  Path, Tmp: String;
begin
  ForceDirectories(ExpandConstant('{app}'));
  Path := ExpandConstant('{app}\rollback-state.json');
  Tmp := Path + '.tmp';
  Result := SaveStringToFile(Tmp, Content, False);
  if Result then
  begin
    if FileExists(Path) then
      DeleteFile(Path);
    Result := RenameFile(Tmp, Path);
  end;
end;

function WriteNoneStateFile: Boolean;
begin
  Result := WriteStateFile(
    '{' + #13#10 +
    '  "schema_version": 1,' + #13#10 +
    '  "last_update": "",' + #13#10 +
    '  "status": "none",' + #13#10 +
    '  "updated_at": "",' + #13#10 +
    '  "app": { "from": "", "to": "" },' + #13#10 +
    '  "msys": { "from": "", "to": "" }' + #13#10 +
    '}');
end;

function BuildStateJSON(const UpdateKind, AppTo, MsysTo: String): String;
var
  AppFrom, MsysFrom: String;
begin
  AppFrom := ReadTextFileTrim(ComponentDir('app') + '\haitun-version.txt');
  MsysFrom := ReadTextFileTrim(ComponentDir('msys64') + '\msys-version.txt');
  Result := '{' + #13#10 +
    '  "schema_version": 1,' + #13#10 +
    '  "last_update": "' + UpdateKind + '",' + #13#10 +
    '  "status": "pending",' + #13#10 +
    '  "updated_at": "' + GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '",' + #13#10 +
    '  "app": { "from": "' + AppFrom + '", "to": "' + AppTo + '" },' + #13#10 +
    '  "msys": { "from": "' + MsysFrom + '", "to": "' + MsysTo + '" }' + #13#10 +
    '}';
end;

function InstallsApp: Boolean;
begin
#ifdef COMPONENT_APP
  Result := True;
#else
#ifdef COMPONENT_MSYS
  Result := False;
#else
  Result := True;
#endif
#endif
end;

function InstallsMsys: Boolean;
begin
#ifdef COMPONENT_MSYS
  Result := True;
#else
#ifdef COMPONENT_APP
  Result := False;
#else
  Result := True;
#endif
#endif
end;

function UpdateKindName: String;
begin
#ifdef COMPONENT_APP
  Result := 'app';
#else
#ifdef COMPONENT_MSYS
  Result := 'msys';
#else
  Result := 'all';
#endif
#endif
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Root, AppTo, MsysTo: String;
  FreshInstall: Boolean;
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;
  SetCurrentDir(ExpandConstant('{tmp}'));
  Root := ExpandConstant('{app}');

  if (FileExists(Root + '\haitun.exe') or FileExists(Root + '\psi-agent.exe')) and
     not DirExists(Root + '\app') then
  begin
    Result := '检测到旧版本安装结构。请先在“设置 -> 应用”中卸载旧版 HaiTun Agent，再运行新安装包。';
    Exit;
  end;

  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM haitun.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM psi-agent.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM HaiTun-Agent-Setup.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM HaiTun-Agent-App-Setup.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM msys-setup.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM HaiTun_Agent_Setup.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM HaiTun_Agent_App_Setup.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);

#ifdef COMPONENT_MSYS
  AppTo := '';
  MsysTo := '{#MyMsysVersion}';
#else
  AppTo := '{#MyAppVersion}';
#ifdef COMPONENT_APP
  MsysTo := '';
#else
  MsysTo := '{#MyMsysVersion}';
#endif
#endif

  FreshInstall := not DirExists(ComponentDir('app')) and
                  not DirExists(ComponentDir('msys64'));
  if not FreshInstall then
  begin
    PendingStateJSON := BuildStateJSON(UpdateKindName, AppTo, MsysTo);
    if not WriteStateFile(PendingStateJSON) then
    begin
      Result := '无法写入回滚状态文件，请检查磁盘空间后重试。';
      Exit;
    end;
  end;

  if InstallsApp and not SwapComponent('app') then
  begin
    if (not DirExists(ComponentDir('app') + '.backup')) and
       (not DirExists(ComponentDir('msys64') + '.backup')) then
      WriteNoneStateFile;
    Result := '无法备份旧版海豚目录，请关闭海豚后重试。';
    Exit;
  end;
  if InstallsMsys and not SwapComponent('msys64') then
  begin
    if (not DirExists(ComponentDir('app') + '.backup')) and
       (not DirExists(ComponentDir('msys64') + '.backup')) then
      WriteNoneStateFile;
    Result := '无法备份旧版环境目录，请关闭海豚后重试。';
    Exit;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DoneJSON, Exe: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if DirExists(ComponentDir('app') + '.backup') or
       DirExists(ComponentDir('msys64') + '.backup') then
    begin
      if Length(PendingStateJSON) > 0 then
      begin
        DoneJSON := PendingStateJSON;
        if StringChange(DoneJSON, '"status": "pending"', '"status": "done"') > 0 then
          WriteStateFile(DoneJSON);
      end;
    end
    else if Length(PendingStateJSON) > 0 then
    begin
      WriteNoneStateFile;
    end;

    Exe := ComponentDir('app') + '\haitun.exe';
    if FileExists(Exe) then
      ShellExec('open', Exe, '', '', SW_SHOWNORMAL, ewNoWait, ResultCode);
  end;
end;

function PrepareToUninstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  NeedsRestart := False;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM haitun.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM psi-agent.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;
