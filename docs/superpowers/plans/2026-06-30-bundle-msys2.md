# 把 MSYS2 打包进 Windows 安装程序 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `haitun-inno-setup` CI job 里装一份 MSYS2（base + 常用工具，保留 pacman），打包进安装程序，并让 VBS 把它加到 PATH，使工作区 `bash` 工具在 Windows 上开箱即用。

**Architecture:** CI 用 `msys2/setup-msys2` 装 MSYS2 → robocopy 复制进 `examples/haitun-workspace/msys64` → 删包缓存瘦身 → 现有 ISCC 递归 glob 自动打包到 `{app}\msys64`。VBS 启动前把 `msys64\usr\bin` 与 `msys64\ucrt64\bin` prepend 到 PATH 并设 `CHERE_INVOKING=1`；`bash.py` 与 `.iss` 均不改。

**Tech Stack:** GitHub Actions、`msys2/setup-msys2`、Inno Setup、VBScript、robocopy/pwsh。

**关于测试：** 按用户要求**不加自动化测试**。这些产物（YAML / VBS / gitignore / 文档）无 pytest 适用面；本地验证 = 静态检查（YAML 可解析、文件内容、grep）。最终验证靠 CI：`haitun-inno-setup` job 在加入 MSYS2 后仍能编译出安装包。每个任务以提交收尾。

**分支：** 已在 `bundle-msys2`（基于 `origin/main`）。

---

### Task 1: VBS 加 PATH + CHERE_INVOKING

**Files:**
- Modify: `examples/haitun-workspace/haitun agent.vbs`（在 .env 块之后、`objShell.Run` 之前插入）

- [ ] **Step 1: 编辑文件**

把文件末尾这段：

```vbs
End If

objShell.Run "psi-agent.exe gateway --tray --icon haitun.ico", 0, False
```

替换为：

```vbs
End If

' Prepend the bundled MSYS2 to PATH: usr\bin (bash/git POSIX tools) + ucrt64\bin (node/uv native tools).
strUsrBin = objFSO.BuildPath(strDir, "msys64\usr\bin")
strUcrtBin = objFSO.BuildPath(strDir, "msys64\ucrt64\bin")
objShell.Environment("Process")("PATH") = strUsrBin & ";" & strUcrtBin & ";" & objShell.Environment("Process")("PATH")
' Keep bash -lc in the current working directory instead of cd-ing to $HOME.
objShell.Environment("Process")("CHERE_INVOKING") = "1"

objShell.Run "psi-agent.exe gateway --tray --icon haitun.ico", 0, False
```

说明：放在 .env 加载之后，保证即使 `.env` 覆盖了 `PATH`，`msys64\usr\bin` 与 `msys64\ucrt64\bin` 仍在最前。`Environment("Process")` 修改的是当前进程环境，`objShell.Run` 启动的子进程继承之。

- [ ] **Step 2: 验证内容**

Run: `cat "examples/haitun-workspace/haitun agent.vbs"`
Expected: 末尾出现 `strUsrBin`、`strUcrtBin`、`PATH` prepend、`CHERE_INVOKING` 四处新增，且 `objShell.Run` 仍是最后一行。

- [ ] **Step 3: 验证 If/Do/Loop 结构未被破坏**

Run: `grep -cE "^\s*(If |Do Until|End If|Loop)" "examples/haitun-workspace/haitun agent.vbs"`
Expected: `11`（与改动前一致——新增代码不含分支语句）。

- [ ] **Step 4: 提交**

```bash
git add "examples/haitun-workspace/haitun agent.vbs"
git commit -m "feat: prepend bundled MSYS2 to PATH in launcher VBS"
```

---

### Task 2: CI 在 haitun-inno-setup job 内装并打包 MSYS2

**Files:**
- Modify: `.github/workflows/pyinstaller.yml`（在 copy exe 步骤后、`choco install innosetup` 前插入三步）

- [ ] **Step 1: 编辑文件**

把这段：

```yaml
      - shell: cmd
        run: copy dist-exe\psi-agent.exe "examples\haitun-workspace\psi-agent.exe"
      - run: choco install innosetup --no-progress
```

替换为：

```yaml
      - shell: cmd
        run: copy dist-exe\psi-agent.exe "examples\haitun-workspace\psi-agent.exe"
      - uses: msys2/setup-msys2@v2
        id: msys2
        with:
          msystem: MSYS
          release: true
          update: true
          install: >-
            bash coreutils grep sed gawk findutils diffutils which
            git openssh curl rsync tar gzip less nano
            mingw-w64-ucrt-x86_64-nodejs mingw-w64-ucrt-x86_64-uv
      - shell: pwsh
        run: |
          robocopy "${{ steps.msys2.outputs.msys2-location }}" "examples\haitun-workspace\msys64" /E /NFL /NDL /NJH /NJS /NP
          if ($LASTEXITCODE -ge 8) { exit 1 } else { exit 0 }
      - shell: pwsh
        run: Remove-Item -Recurse -Force "examples\haitun-workspace\msys64\var\cache\pacman\pkg\*" -ErrorAction SilentlyContinue
      - run: choco install innosetup --no-progress
```

说明：
- `setup-msys2` 全新装 MSYS2 并装上指定包（保留 pacman）；output `msys2-location` 是其根目录。
- robocopy 退出码 0~7 都算成功，故 `if ($LASTEXITCODE -ge 8) { exit 1 } else { exit 0 }` 归一化（否则 pwsh 把成功当失败）。
- 删 `var/cache/pacman/pkg/*`（已下载安装包缓存，约 100MB+）。
- ISCC 步骤无需改动：现有 `Source: "*"; recursesubdirs createallsubdirs` 会自动把 `examples/haitun-workspace/msys64` 打包到 `{app}\msys64`。

- [ ] **Step 2: 验证 YAML 可解析**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/pyinstaller.yml')); print('YAML OK')"`
Expected: `YAML OK`（无异常）。

- [ ] **Step 3: 验证两个 job 仍在且新步骤已加入**

Run: `uv run python -c "import yaml; d=yaml.safe_load(open('.github/workflows/pyinstaller.yml')); print(sorted(d['jobs'])); print([s.get('uses') or s.get('run','')[:30] for s in d['jobs']['haitun-inno-setup']['steps']])"`
Expected: jobs 为 `['haitun-inno-setup', 'pyinstaller']`，且 haitun-inno-setup 的 steps 列表里出现 `msys2/setup-msys2@v2` 和 robocopy。

- [ ] **Step 4: 提交**

```bash
git add .github/workflows/pyinstaller.yml
git commit -m "ci: install and bundle MSYS2 into the Haitun Agent installer"
```

---

### Task 3: .gitignore 忽略 msys64/

**Files:**
- Modify: `examples/haitun-workspace/.gitignore`

- [ ] **Step 1: 编辑文件**

把末尾这段：

```
haitun agent.lnk
psi-agent.exe
psi-agent
```

替换为：

```
haitun agent.lnk
psi-agent.exe
psi-agent
msys64/
```

- [ ] **Step 2: 验证**

Run: `grep -n "msys64/" examples/haitun-workspace/.gitignore`
Expected: 出现一行 `msys64/`。

- [ ] **Step 3: 提交**

```bash
git add examples/haitun-workspace/.gitignore
git commit -m "chore: gitignore generated msys64 bundle in haitun-workspace"
```

---

### Task 4: 文档同步

**Files:**
- Modify: `examples/haitun-workspace/README.md`（Windows 安装包段，补 MSYS2 说明）
- Modify: `examples/haitun-workspace/AGENTS.md`（tools 表里 bash 一行）

- [ ] **Step 1: 读取 README 安装包段确认锚点**

Run: `tail -12 examples/haitun-workspace/README.md`
Expected: 见到 `## Windows 安装包` 段与结尾的 `.env` 提示。

- [ ] **Step 2: 在 README 安装包段追加 MSYS2 说明**

把 README 末尾这一行（`.env` 提示）：

```markdown
> `haitun agent.vbs` 启动前会读取本目录下的 `.env`（若存在），把其中的 `KEY=VALUE` 注入 `psi-agent.exe` 的运行环境（跳过空行 / `#` 注释，剥离值两端成对引号）。
```

替换为（在其后追加一段）：

```markdown
> `haitun agent.vbs` 启动前会读取本目录下的 `.env`（若存在），把其中的 `KEY=VALUE` 注入 `psi-agent.exe` 的运行环境（跳过空行 / `#` 注释，剥离值两端成对引号）。

> 安装包自带一份 MSYS2（位于 `{app}\msys64`，含 bash/git/curl/ssh 等，保留 pacman）。`haitun agent.vbs` 会把 `msys64\usr\bin` 加到 `PATH` 最前，因此 `bash` 工具在 Windows 上开箱即用，无需另装 Git Bash。
```

- [ ] **Step 3: 更新 AGENTS.md 的 bash 行**

把 `examples/haitun-workspace/AGENTS.md` 中这一行：

```markdown
| `bash` | Shell commands (anyio, Windows-aware bash detection). |
```

替换为：

```markdown
| `bash` | Shell commands (anyio, Windows-aware bash detection). On Windows the installer bundles MSYS2 at `{app}\msys64`, added to PATH by the launcher, so bash works out-of-the-box. |
```

- [ ] **Step 4: 验证**

Run: `grep -n "msys64" examples/haitun-workspace/README.md examples/haitun-workspace/AGENTS.md`
Expected: README 与 AGENTS.md 各至少出现一处 `msys64`。

- [ ] **Step 5: 提交**

```bash
git add examples/haitun-workspace/README.md examples/haitun-workspace/AGENTS.md
git commit -m "docs: document bundled MSYS2 in haitun-workspace"
```

---

## Definition of Done

- [ ] VBS 在启动前 prepend `msys64\usr\bin` 与 `msys64\ucrt64\bin` 到 PATH 并设 `CHERE_INVOKING=1`
- [ ] `pyinstaller.yml` 的 `haitun-inno-setup` job 内：`setup-msys2` 装包 → robocopy 进 `examples/haitun-workspace/msys64` → 删 pacman 缓存；YAML 可解析
- [ ] `.gitignore` 忽略 `msys64/`
- [ ] README + AGENTS.md 记录自带 MSYS2
- [ ] `.iss` 与 `bash.py` 未改动
- [ ] （CI，非本地）下次 push 后 `haitun-inno-setup` job 成功编译出含 MSYS2 的 `haitun-agent-installer`
