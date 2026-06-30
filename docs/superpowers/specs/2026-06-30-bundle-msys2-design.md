# 把 MSYS2 打包进 Haitun Agent Windows 安装程序

## 目标

把一份**可更新的 MSYS2**（base + 常用命令行工具，保留 pacman）打包进 Haitun Agent 的 Windows 安装程序，使工作区的 `bash` 工具在 Windows 上**开箱即用**，无需用户另外安装 Git Bash / MSYS2。

MSYS2 是 Windows 上的类 Unix 环境，提供 `bash`、`coreutils`、`git`、`curl`、`ssh` 等命令。当前 `tools/bash.py` 在 Windows 上只会查找用户自装的 Git Bash，找不到就无法使用 bash。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `.github/workflows/pyinstaller.yml` | 修改 | 在 `haitun-inno-setup` job 内新增：装 MSYS2 → 复制进工作区 → 瘦身 |
| `examples/haitun-workspace/haitun agent.vbs` | 修改 | 启动前把 `msys64\usr\bin` 与 `msys64\ucrt64\bin` 加到 PATH 最前，并设 `CHERE_INVOKING=1` |
| `examples/haitun-workspace/.gitignore` | 修改 | 新增 `msys64/`（CI 生成，不提交） |
| `examples/haitun-workspace/haitun.iss` | **不改** | 现有递归 glob 自动打包 `msys64` |
| `examples/haitun-workspace/tools/bash.py` | **不改** | 复用现有 `which` 查找逻辑（见下方说明） |
| `README.md` / `AGENTS.md` | 修改 | 文档同步 |

## ① CI：获取 MSYS2 并瘦身（在 `haitun-inno-setup` job 内）

现有 `haitun-inno-setup` job 顺序：checkout → download-artifact（psi-agent.exe）→ copy exe → choco innosetup → ISCC → upload。

在 **copy exe 之后、ISCC 之前**插入：

```yaml
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
```

要点：
- `msys2/setup-msys2@v2` 全新装一份 MSYS2，装上指定的包（含 ucrt64 的 nodejs/uv），**保留 pacman**；其 step output `msys2-location` 是 MSYS2 根目录（内含 `usr/`、`ucrt64/`、`etc/`、`var/` 等）。
- **robocopy 退出码 0~7 都算成功**，`>=8` 才是真失败，所以必须做 `if ($LASTEXITCODE -ge 8) { exit 1 } else { exit 0 }` 归一化，否则 pwsh 会把成功当失败。
- 瘦身只删 `var/cache/pacman/pkg/*`（已下载的安装包缓存，纯占地方，约 100MB+），pacman 本体与包数据库保留。
- MSYS2 这份树有上万个文件、几百 MB，所以放在同一个 job 内处理，不跨 job 传 artifact。

## ② VBS：改 PATH + 设 CHERE_INVOKING

在 `haitun agent.vbs` 中，**在 .env 加载之后、`objShell.Run` 之前**插入：

```vbs
' 把打包进来的 MSYS2 加到 PATH 最前面：usr\bin（bash/git 等 POSIX 工具）+ ucrt64\bin（node/uv 等原生工具）。
strUsrBin = objFSO.BuildPath(strDir, "msys64\usr\bin")
strUcrtBin = objFSO.BuildPath(strDir, "msys64\ucrt64\bin")
objShell.Environment("Process")("PATH") = strUsrBin & ";" & strUcrtBin & ";" & objShell.Environment("Process")("PATH")
' 让 bash -lc 留在当前工作目录，而不是跳到 $HOME。
objShell.Environment("Process")("CHERE_INVOKING") = "1"
```

- 放在 .env 加载之后：即便用户在 `.env` 里覆盖了 `PATH`，我们仍把 `msys64\usr\bin` 与 `msys64\ucrt64\bin` prepend 到最前，保证打包的 MSYS2 优先。
- `CHERE_INVOKING=1`：MSYS2 的 `/etc/profile` 在未设此变量时会 `cd` 到 `$HOME`；设上后 bash 留在被调用时的目录，命令对当前目录生效（符合 `bash` 工具的预期用法）。

## ③ `.iss` 不改（刻意）

现有 `[Files]` 的 `Source: "*"; Flags: ignoreversion recursesubdirs createallsubdirs` 会把工作区下的 `msys64` 子目录连同其全部子目录自动打包到 `{app}\msys64`。无需单独的 `[Files]` 条目（单独条目反而要用易错的 `Excludes` 防止重复打包）。卸载时 Inno 会随 `{app}` 一并删除。

## ④ `bash.py` 不改（刻意，需留痕）

> 这是一个刻意为之、容易被误当成"漏改"的点。

安装后 `{app}\msys64\usr\bin` 已被 VBS 加到 `psi-agent.exe` 的 PATH。`tools/bash.py` 现有的 `_find_bash()` 逻辑会因此自动命中打包的 MSYS2：

1. `shutil.which("git")` 找到 `{app}\msys64\usr\bin\git.exe`，由此推出 `git_root/usr`，候选里的 `git_root/bin/bash.exe`（即 `msys64\usr\bin\bash.exe`）存在 → 命中；
2. 即便该推断不命中，函数末尾的 `return shutil.which("bash")` 也会因 PATH 而找到 `msys64\usr\bin\bash.exe`。

优先级：用户若已装 Git Bash 则优先用之，否则用打包的 MSYS2。两种情况 bash 都可用，因此 `bash.py` 无需任何改动。

## ⑤ 文档与 .gitignore

- `examples/haitun-workspace/.gitignore` 新增 `msys64/`。
- README（安装包现在自带 MSYS2、bash 开箱即用）、根 `AGENTS.md` 或 workspace `AGENTS.md` 的 tools 说明、本设计文档同步。

## ⑥ 测试

按用户要求**不补充测试**。验证依赖 CI：`haitun-inno-setup` job 能在加入 MSYS2（更大负载）后成功编译出安装包。真正"Windows 上跑 bash"的路径无法在 CI 内执行，靠设计与 PATH 逻辑保证。

## 关键权衡与注意事项

- **可重定位**：MSYS2 运行时按 `msys-2.0.dll` 的位置推断根目录，故装到任意 `{app}\msys64` 都能用；base + 工具集不含会写死绝对路径的工具链。
- **PATH 污染（为简单付出的代价）**：`msys64\usr\bin` 进入 `psi-agent.exe` 的全局 PATH，意味着 `powershell` 工具及其它子进程也会看到 MSYS 版命令（如 MSYS 的 `find.exe` 可能盖过 Windows 的 `find.exe`）。这是"不改 bash.py、只改 VBS"方案的固有取舍，用户已确认接受。
- **体积**：安装包从 ~84MB 增至约 **300~500MB**（含 ucrt64 的 nodejs + uv，比纯 base 再 +100~150MB）；ISCC 编译变慢（msys64 上万文件）。
- **卸载残留**：Inno 卸载删除它安装的文件；用户后续用 pacman 新装的文件可能残留在 `{app}\msys64`，属次要问题。

## 非目标

- 不打包 mingw-w64 工具链（gcc/make 等）。
- 不修改 `bash.py` 或其它工具代码。
- 不补充自动化测试。
- 不做 MSYS2 的离线镜像/版本锁定（用 `setup-msys2` 的当期版本）。
