# Haitun Agent Inno Setup Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate `inno-setup` GitHub Actions job that bundles the PyInstaller-built `psi-agent.exe` with the `haitun-workspace` folder into a Windows installer, emitted as an artifact.

**Architecture:** A committed VBS launcher and Inno Setup `.iss` script live inside `examples/haitun-workspace/`. A new CI job (`needs: pyinstaller`, `runs-on: windows-latest`) downloads the Windows exe artifact, drops it into the workspace, installs Inno Setup via chocolatey, compiles the `.iss`, and uploads `Haitun Agent Setup.exe`.

**Tech Stack:** GitHub Actions, Inno Setup 6 (ISCC), VBScript, chocolatey.

**Note on testing:** These artifacts (YAML, `.vbs`, `.iss`) have no pytest harness. Local verification = file existence + YAML parse validity. The definitive integration test is the GitHub Actions run itself (cannot run locally — Windows + ISCC required). Each task ends with a commit.

---

### Task 1: Move the icon into the workspace

**Files:**
- Move: `haitun.ico` → `examples/haitun-workspace/haitun.ico`

- [ ] **Step 1: Move the file with git**

```bash
git mv haitun.ico examples/haitun-workspace/haitun.ico
```

- [ ] **Step 2: Verify new location and old removed**

Run: `ls -la examples/haitun-workspace/haitun.ico && ls haitun.ico 2>&1 || echo "root copy gone (expected)"`
Expected: the workspace path lists the ~1MB file; the root `haitun.ico` is gone.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: move haitun.ico into haitun-workspace"
```

---

### Task 2: Create the VBS launcher

**Files:**
- Create: `examples/haitun-workspace/haitun agent.vbs`

- [ ] **Step 1: Write the file**

Create `examples/haitun-workspace/haitun agent.vbs` with EXACTLY this content:

```vbs
Set objFSO = CreateObject("Scripting.FileSystemObject")
Set objShell = CreateObject("WScript.Shell")
objShell.CurrentDirectory = objFSO.GetParentFolderName(WScript.ScriptFullName)
objShell.Run "psi-agent.exe gateway --tray haitun.ico", 0, False
```

Rationale: line 3 forces the working directory to the script's own folder, so `psi-agent.exe`, `haitun.ico`, and the workspace files resolve correctly no matter how the `.vbs` is launched. `0` runs hidden; `False` returns without waiting.

- [ ] **Step 2: Verify content**

Run: `cat "examples/haitun-workspace/haitun agent.vbs"`
Expected: the 4 lines above, exactly.

- [ ] **Step 3: Commit**

```bash
git add "examples/haitun-workspace/haitun agent.vbs"
git commit -m "feat: add Haitun Agent VBS launcher"
```

---

### Task 3: Create the Inno Setup script

**Files:**
- Create: `examples/haitun-workspace/haitun.iss`

- [ ] **Step 1: Write the file**

Create `examples/haitun-workspace/haitun.iss` with EXACTLY this content:

```iss
; Inno Setup script for Haitun Agent.
; Packages the entire haitun-workspace (including psi-agent.exe, copied in at build time).

#define MyAppName "Haitun Agent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Zhenzhi Company, Inc."
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

Notes:
- `Source: "*"` + `recursesubdirs createallsubdirs` packages the whole workspace tree (systems/, tools/, skills/, …) plus `psi-agent.exe`. Source paths are relative to the `.iss` directory (`examples/haitun-workspace/`).
- `[Run]` uses `shellexec` because `.vbs` is not a directly executable image.
- `WizardStyle=modern` (dropped the non-standard `dynamic` token from the original wizard output).

- [ ] **Step 2: Verify content**

Run: `cat examples/haitun-workspace/haitun.iss`
Expected: the content above, exactly.

- [ ] **Step 3: Commit**

```bash
git add examples/haitun-workspace/haitun.iss
git commit -m "feat: add Inno Setup script for Haitun Agent installer"
```

---

### Task 4: Update the workspace .gitignore

**Files:**
- Modify: `examples/haitun-workspace/.gitignore` (lines 29, 31 — remove `haitun.ico` and `haitun agent.vbs`)

Context: committed launcher assets (`haitun.ico`, `haitun agent.vbs`) should not be gitignored. `psi-agent.exe` and `psi-agent` stay ignored (build artifacts).

- [ ] **Step 1: Remove the `haitun.ico` line**

Delete the line containing exactly `haitun.ico` from `examples/haitun-workspace/.gitignore`.

- [ ] **Step 2: Remove the `haitun agent.vbs` line**

Delete the line containing exactly `haitun agent.vbs` from `examples/haitun-workspace/.gitignore`.

- [ ] **Step 3: Verify**

Run: `grep -nE "haitun.ico|haitun agent.vbs|psi-agent.exe" examples/haitun-workspace/.gitignore`
Expected: only `psi-agent.exe` remains; the other two are gone.

- [ ] **Step 4: Commit**

```bash
git add examples/haitun-workspace/.gitignore
git commit -m "chore: un-ignore haitun.iss launcher assets in haitun-workspace"
```

---

### Task 5: Add the `inno-setup` job to pyinstaller.yml

**Files:**
- Modify: `.github/workflows/pyinstaller.yml` (append a new job after line 48)

- [ ] **Step 1: Append the new job**

Add the following to the end of `.github/workflows/pyinstaller.yml` (a new top-level entry under `jobs:`, sibling to `pyinstaller`):

```yaml

  inno-setup:
    needs: pyinstaller
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v7
        with:
          name: psi-agent-pyinstaller-windows-latest
          path: dist-exe
      - shell: cmd
        run: copy dist-exe\psi-agent.exe "examples\haitun-workspace\psi-agent.exe"
      - run: choco install innosetup --no-progress
      - shell: pwsh
        run: '& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /O"installer-output" "examples\haitun-workspace\haitun.iss"'
      - uses: actions/upload-artifact@v7
        with:
          name: haitun-agent-installer
          path: installer-output/Haitun Agent Setup.exe
```

Details:
- `needs: pyinstaller` waits for the matrix job (incl. the Windows leg that uploaded `psi-agent-pyinstaller-windows-latest`).
- `download-artifact` restores `psi-agent.exe` into `dist-exe/`.
- The `copy` step (cmd shell) drops the exe into the workspace next to the `.iss`.
- chocolatey's `innosetup` installs via its own installer and does NOT add `ISCC.exe` to PATH, so we invoke it by full path under `${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe`.
- `/O"installer-output"` writes the compiled installer to a directory outside the workspace, avoiding any chance of self-inclusion on re-runs.

- [ ] **Step 2: Verify the YAML still parses**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/pyinstaller.yml')); print('YAML OK')"`
Expected: `YAML OK` (no exception).

- [ ] **Step 3: Verify both jobs are present**

Run: `uv run python -c "import yaml; d=yaml.safe_load(open('.github/workflows/pyinstaller.yml')); print(sorted(d['jobs']))"`
Expected: `['inno-setup', 'pyinstaller']`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/pyinstaller.yml
git commit -m "ci: build Haitun Agent installer via Inno Setup job"
```

---

### Task 6: Document the installer build

**Files:**
- Modify: `examples/haitun-workspace/README.md` (append a short "Windows 安装包" section)

Context: AGENTS.md Definition-of-Done item 1 requires doc sync for new behavior/CI. Add a brief note describing how the installer is produced.

- [ ] **Step 1: Read the current README tail to match style**

Run: `tail -20 examples/haitun-workspace/README.md`
Expected: see existing heading style / language (Chinese).

- [ ] **Step 2: Append the section**

Append to `examples/haitun-workspace/README.md`:

```markdown

## Windows 安装包

`.github/workflows/pyinstaller.yml` 的 `inno-setup` job 会自动构建 Windows 安装程序：

1. PyInstaller 生成的 `psi-agent.exe` 被拷贝进本目录
2. `haitun.iss`（Inno Setup 脚本）将整个 workspace 打包为安装程序
3. 安装后通过 `haitun agent.vbs` 启动 `psi-agent gateway --tray haitun.ico`

产物为 GitHub artifact `haitun-agent-installer`（`Haitun Agent Setup.exe`）。
```

- [ ] **Step 3: Verify**

Run: `tail -12 examples/haitun-workspace/README.md`
Expected: the new section is present.

- [ ] **Step 4: Commit**

```bash
git add examples/haitun-workspace/README.md
git commit -m "docs: document Haitun Agent Windows installer build"
```

---

## Definition of Done

- [ ] `examples/haitun-workspace/` contains `haitun.ico`, `haitun agent.vbs`, `haitun.iss`
- [ ] `.gitignore` no longer ignores `haitun.ico` / `haitun agent.vbs`; still ignores `psi-agent.exe`
- [ ] `pyinstaller.yml` has a second job `inno-setup` (`needs: pyinstaller`, windows-latest) that parses as valid YAML
- [ ] README documents the installer build
- [ ] (CI, not local) On the next push, the `inno-setup` job produces the `haitun-agent-installer` artifact
