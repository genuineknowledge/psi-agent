# Haitun Agent Inno Setup 打包机制

## 目标

在 GitHub Actions (`pyinstaller.yml`) 中新增一个独立的 Inno Setup 打包 job，将 PyInstaller 生成的 `psi-agent.exe` 与 `haitun-workspace` 合并打包为一个 Windows 安装程序，作为 artifact 输出。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `haitun.ico` → `examples/haitun-workspace/haitun.ico` | 移动 | 图标文件，用于 tray icon、installer icon、shortcut icon |
| `examples/haitun-workspace/haitun agent.vbs` | 新建 | VBS 启动器，负责 launch `psi-agent gateway --tray haitun.ico` |
| `examples/haitun-workspace/haitun.iss` | 新建 | Inno Setup 安装脚本 |
| `examples/haitun-workspace/.gitignore` | 修改 | 移除 `haitun agent.vbs`、`haitun.ico` 行（现已提交） |
| `.github/workflows/pyinstaller.yml` | 修改 | 新增 `haitun-inno-setup` job，在 PyInstaller 之后运行 |

## haitun agent.vbs

```vbs
Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")

' Run from the script's own directory so psi-agent.exe / haitun.ico / .env resolve.
strDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strDir

' Load .env (if present) into this process environment; the child inherits it.
strEnvPath = objFSO.BuildPath(strDir, ".env")
If objFSO.FileExists(strEnvPath) Then
    Set objEnv = objShell.Environment("Process")
    Set objFile = objFSO.OpenTextFile(strEnvPath, 1, False)
    Do Until objFile.AtEndOfStream
        strLine = Trim(objFile.ReadLine)
        If Len(strLine) > 0 And Left(strLine, 1) <> "#" Then
            intPos = InStr(strLine, "=")
            If intPos > 1 Then
                strKey = Trim(Left(strLine, intPos - 1))
                strVal = Trim(Mid(strLine, intPos + 1))
                strFirst = Left(strVal, 1)
                If Len(strVal) >= 2 And (strFirst = """" Or strFirst = "'") And Right(strVal, 1) = strFirst Then
                    strVal = Mid(strVal, 2, Len(strVal) - 2)
                End If
                If Len(strKey) > 0 Then objEnv(strKey) = strVal
            End If
        End If
    Loop
    objFile.Close
End If

objShell.Run "psi-agent.exe gateway --tray haitun.ico", 0, False
```

- 设置工作目录到脚本所在目录，确保 `psi-agent.exe`、`haitun.ico`、`.env` 及 workspace 文件可被找到
- 若同目录存在 `.env`，按行解析 `KEY=VALUE`（跳过空行和 `#` 注释，剥离值两端成对引号），写入 `Environment("Process")`，子进程 `psi-agent.exe` 继承之。**不**做 `${VAR}` 插值 / `export` 前缀 / UTF-8（按 ANSI 读取）
- `0` = 隐藏窗口运行；`False` = 不等进程结束立即返回

## haitun.iss

全部静态定义，version 为 `1.0.0`。

```iss
#define MyAppName "Haitun Agent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Hefei Zhenzhi Artificial Intelligence Application Software Co., Ltd"
#define MyAppExeName "haitun agent.vbs"

[Setup]
AppId={{234DFAA2-39F9-4E4C-92C7-680728ADDA4A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=Haitun Agent Setup
SetupIconFile=haitun.ico
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\haitun.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\haitun.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: shellexec postinstall skipifsilent
```

关键设计决策：
- `Source: "*"` 覆盖整个 haitun-workspace（含 `psi-agent.exe`），`recursesubdirs` 递归打包子目录（systems/、tools/、skills/ 等）
- `SetupIconFile=haitun.ico` 位于 .iss 同目录
- 输出文件名固定为 `Haitun Agent Setup`
- ISCC 运行时通过 `/O` flag 将 Output 目录设在 workspace 外避免递归包含

## GitHub Actions: pyinstaller.yml 变更

新增独立 job `haitun-inno-setup`：

```yaml
  haitun-inno-setup:
    needs: pyinstaller
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v7
        with:
          name: psi-agent-pyinstaller-windows-latest
          path: dist-exe
      - run: copy dist-exe\psi-agent.exe examples\haitun-workspace\psi-agent.exe
        shell: cmd
      - run: choco install innosetup --no-progress
      - run: '& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /O"installer-output" "examples\haitun-workspace\haitun.iss"'
        shell: pwsh
      - uses: actions/upload-artifact@v7
        with:
          name: haitun-agent-installer
          path: installer-output/Haitun Agent Setup.exe
```

> 注：chocolatey 的 `innosetup` 包通过自身 installer 安装，**不会**把 `ISCC.exe` 加到 PATH。必须用完整路径 `${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe` 调用。

流程：
1. `needs: pyinstaller` — 等待所有 OS matrix 完成（确保 Windows artifact 已上传）
2. checkout 获取 haitun-workspace（含 .vbs、.iss、haitun.ico）
3. download-artifact 获取 `psi-agent.exe`
4. copy `psi-agent.exe` 到 workspace
5. choco 安装 Inno Setup（`iscc.exe`）
6. ISCC 编译（Output 到 `installer-output/` 独立目录）
7. upload-artifact 输出 `Haitun Agent Setup.exe`

## 依赖关系

```
pyinstaller (matrix: ubuntu/windows/macOS)
  └── upload-artifact: psi-agent-pyinstaller-{os} (per OS)
  
haitun-inno-setup (windows-latest)
  └── needs: pyinstaller (all matrix)
  └── download-artifact: psi-agent-pyinstaller-windows-latest
  └── upload-artifact: haitun-agent-installer
```

## 非目标

- 不做 .iss 的版本号动态推导（保持静态 `1.0.0`）
- 不打包成 MSI（仅 .exe 安装程序）
- 不设立签名机制
