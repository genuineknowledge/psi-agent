---
name: tmux
description: "用 `tmux` 创建/控制持久终端会话来跑长期命令行任务 —— 服务器、构建、训练、REPL、交互式 TUI、需要活过单次命令的东西。LOAD whenever 用户要:起一个后台/长跑的命令行进程并能反复回来看它、attach 到一个已有会话、开多个窗格/窗口、给会话喂键、抓当前屏幕内容(capture-pane)、或让一个进程在命令返回后继续活着。核心是 detach 跑 + `send-keys` 喂命令 + `capture-pane` 读屏这套非交互配方,最适合 agent 的一次性 bash。全部通过 bash 工具跑本机 `tmux`,不封装 Python tool、不新增依赖、不改 pyproject / nuitka / pyinstaller。⚠️ Windows 无原生 tmux:走 WSL 里的 tmux(见下),装不了 WSL 时退回工作区自带的 `background_start`/`background_stop`。NOT for 一条几秒就返回的普通命令(直接 bash),NOT for 后台跑但不需要交互/看屏的进程(用 background_start)。"
category: coding
---

# tmux —— 持久终端会话(via `tmux` CLI)

用 [`tmux`](https://github.com/tmux/tmux)(终端复用器)创建**活过单次命令**的会话:进程在
detach 的会话里一直跑,你随时 attach 回去、给它喂键、抓屏看输出。agent 只用 **bash** 工具
跑本机 `tmux`,**不封装 Python tool、不新增依赖、不改 pyproject / nuitka / pyinstaller**——
和 `node-inspect-debugger`、`codex`、`himalaya` 等 CLI-wrapper skill 一样。

默认用**中文**回答,除非用户明显用别的语言。

## 何时用(和 bash / background_* 的边界)

| 场景 | 用什么 |
|------|--------|
| 一条几秒~几十秒就返回的命令 | 直接 **`bash`** 工具(默认 30s 超时) |
| 后台跑个进程、**不需要**交互也不用看实时屏 | **`background_start`** / `background_stop`(工作区自带,跨平台) |
| 长跑进程,还要**反复回来看屏、给它喂命令、多窗格** | **`tmux`**(本 skill) |
| 交互式 TUI / REPL(vim、python、psql、top…)要脚本化驱动 | **`tmux`** send-keys + capture-pane |
| 一个服务要活着,同时你继续干别的、之后再查 | **`tmux`**(detach 会话) |

一句话:**要"会话+能回来看/喂键"就 tmux;只要"后台跑完拿结果"就 background_*;
要"跑一下立刻拿输出"就 bash。**

## ⚠️ agent 的铁律:永不 attach,一切非交互

`tmux attach` 会占住一个**交互式 TTY 实时会话** —— 在 agent 的一次性、非交互 bash 里会
**挂死/超时**(和 `node inspect` 交互 REPL 同一个坑)。所以 agent **绝不 `tmux attach`**,
全部靠这三板斧非交互操作一个 detach 的会话:

1. **`new-session -d`** 后台建会话(`-d` = detached,不占 TTY,立刻返回)。
2. **`send-keys`** 往会话里喂命令/按键(带 `Enter` 才真正执行)。
3. **`capture-pane -p`** 把当前屏幕内容打到 stdout 给你读。

需要人来真正 attach 交互时,把 attach 命令**给用户在他自己终端里跑**,别在 bash 工具里 attach。

## 核心配方(agent 就用这套)

会话名用**稳定、可预测**的名字(如 `build`、`train`、`srv`),这样后续调用能靠名字找回它。

### 1. 建会话并起长跑进程

```bash
# 建一个 detached 会话 "srv",在里面跑一个长跑服务
tmux new-session -d -s srv 'python -m http.server 8000'

# 或者:先建空会话,再喂命令(更灵活,能分步喂)
tmux new-session -d -s build
tmux send-keys -t srv 'npm run build 2>&1 | tee build.log' Enter
```

- `-d` detached,**立刻返回**,进程在会话里继续跑。
- `-s <name>` 会话名。已存在同名会话会报 `duplicate session` → 先 `has-session` 判断。
- `send-keys` 的字符串是**要敲的键**;命令末尾**必须**再跟一个字面量 `Enter`(等价回车),
  否则命令只是被输入、不执行。特殊键:`C-c`(Ctrl-C)、`C-d`、`Escape`、`Space` 等。

### 2. 读屏(拿输出)—— `capture-pane`

```bash
tmux capture-pane -t srv -p              # 打印当前可见屏到 stdout
tmux capture-pane -t srv -p -S -1000     # 往回多抓 1000 行历史(-S = start line)
tmux capture-pane -t srv -p -S -         # 抓整个 history(可能很长)
```

- `-p` = 打到 stdout(不加 `-p` 是存进 buffer);`-t <target>` 目标会话/窗格。
- 长跑进程要**轮询**:`send-keys` 之后 `sleep` 一两秒再 `capture-pane`,别指望立刻有输出。
- 只关心尾部时,capture 后自己 `| tail -n 40`。

### 3. 判断会话/进程还在不在

```bash
tmux has-session -t srv 2>/dev/null && echo alive || echo gone   # 会话是否存在
tmux list-sessions                                                # 列所有会话(别名 ls)
tmux list-panes -t srv -F '#{pane_pid} #{pane_current_command}'   # 窗格里在跑啥
```

### 4. 给交互式程序喂命令(REPL / TUI)

```bash
tmux new-session -d -s py 'python3'
tmux send-keys -t py '2 + 40' Enter
sleep 1
tmux capture-pane -t py -p | tail -n 5     # 看到 42
tmux send-keys -t py 'exit()' Enter        # 退出 REPL
```

停一个卡住的前台进程:`tmux send-keys -t <s> C-c`(发 Ctrl-C),别急着杀会话。

### 5. 多窗口 / 多窗格(一个会话里跑多个东西)

```bash
tmux new-window   -t srv -n logs 'tail -f build.log'   # 新窗口(独立全屏)
tmux split-window -t srv -h 'htop'                     # 横向切一个窗格
# 定位到具体窗口/窗格:-t srv:logs 或 -t srv:0.1(会话:窗口.窗格)
tmux capture-pane -t srv:logs -p
```

### 6. 收尾 —— 结束会话

```bash
tmux kill-session -t srv        # 结束单个会话(连同里面的进程)
tmux kill-server                # ⚠️ 结束所有会话(会波及别人/别的任务,慎用)
```

**跑完/不用了就 `kill-session`**,别把会话和进程漏在后台。**不要**随手 `kill-server`——
它会干掉这台机上**所有** tmux 会话,可能误伤用户或别的任务。

## ⚠️ Windows:没有原生 tmux —— 替代方案

tmux 是 Unix 程序,**Windows 上没有原生版本**(Git-Bash / MSYS2 里也没有)。先探测:

```bash
command -v tmux >/dev/null && tmux -V || echo "no native tmux"
```

按下面顺序选路,**命中即用**:

### 首选:WSL 里的 tmux(真 tmux 语义)

装了 WSL 的 Windows 机可以在 Linux 发行版里跑真正的 tmux,行为和 macOS/Linux 完全一致。

```bash
command -v wsl >/dev/null && echo "wsl present"        # 先确认有 wsl
wsl -e tmux -V                                          # WSL 里的 tmux 版本
wsl -e tmux new-session -d -s srv 'python3 -m http.server 8000'
wsl -e tmux send-keys -t srv 'echo hi' Enter
wsl -e tmux capture-pane -t srv -p
wsl -e tmux kill-session -t srv
```

- 规律:把上面每条 `tmux …` 命令前面加 `wsl -e` 即可,其余参数原样。
- WSL 里若没装 tmux,引导用户在发行版内装(**用户级操作,agent 不代跑**):
  Ubuntu/Debian `sudo apt install tmux`、Fedora `sudo dnf install tmux`、Alpine `apk add tmux`。
- ⚠️ **路径与进程跨边界**:WSL 里的 tmux 看到的是 **Linux 文件系统**;Windows 盘在
  `/mnt/c/...`。会话里跑的进程活在 WSL 命名空间,Windows 侧 `tasklist` 看不到。想操作
  Windows 上的文件,路径要转成 `/mnt/c/Users/...`。
- 一个 WSL 里起的 tmux server 常驻;跨多次 `wsl -e` 调用**会话是保持的**(同一个 server),
  所以 `new-session` 建的 `srv` 下次 `wsl -e tmux capture-pane -t srv` 还能找到。

### 退路:装不了 WSL —— 用工作区自带的 `background_start` / `background_stop`

WSL 也没有、又要"进程活过单次命令"时,退回工作区**原生的跨平台后台进程工具**(Windows 上
走 PowerShell / Git-Bash,无需任何外部依赖):

```
background_start(command="python -m http.server 8000", process_id="srv")
  → 返回 process_id,进程 detached 继续跑
background_list()                        # 看还活着的后台进程(process_id/pid/alive/command)
background_stop(process_id="srv")        # 结束它
```

**能力对照(说清楚缺什么,别假装等价):**

| 能力 | tmux / WSL-tmux | background_start |
|------|-----------------|------------------|
| 进程活过单次命令 | ✅ | ✅ |
| 跨平台、零外部依赖 | ❌(要 tmux/WSL) | ✅ |
| 读实时屏 `capture-pane` | ✅ | ❌(只能靠进程自己重定向到日志文件,再 bash 读) |
| 给进程喂键 / 驱动 TUI/REPL | ✅ `send-keys` | ❌ |
| 多窗口 / 多窗格 | ✅ | ❌ |
| attach 回去人工接管 | ✅(用户终端) | ❌ |

所以:**需要看屏/喂键/多窗格/交互 → 只能 tmux(Windows 上即 WSL-tmux);只是要个进程在后台
默默跑完、之后拿结果 → background_start 更省事且跨平台。** 退回 background_start 时,让被跑
命令把输出重定向到日志文件(`… > run.log 2>&1`),之后用 bash 读 `run.log` 当作"看屏"。

## 常见坑

| 症状 | 原因 / 处理 |
|------|-------------|
| `tmux: command not found` | 未安装。macOS `brew install tmux`;Linux 包管理器装;**Windows 无原生版** → 走上面 WSL,或退 `background_start` |
| bash 工具**挂死/超时** | 在一次性 bash 里跑了 `tmux attach` / 裸交互命令 → **永不 attach**,用 `new-session -d` + `send-keys` + `capture-pane` |
| `send-keys` 命令没执行 | 忘了末尾的字面量 `Enter` → 命令串后必须再跟一个参数 `Enter` |
| `duplicate session: <name>` | 同名会话已存在 → 先 `tmux has-session -t <name>` 判断,复用或改名或 `kill-session` |
| `capture-pane` 输出是空/旧的 | 命令还没产出 / 抓早了 → `send-keys` 后 `sleep` 一两秒再抓;抓历史加 `-S -1000` |
| `can't find session` / `no server running` | 会话已结束或 server 没起 → `list-sessions` 看现状;WSL 下确认是**同一个** `wsl -e` server |
| `no current client` / `open terminal failed: not a terminal` | 在非 TTY 环境跑了需要 client 的命令(如 attach)→ 只用 `-d` 建会话 + `-t` 定目标,别依赖"当前 client" |
| WSL 里 `tmux -V` 报没装 | WSL **发行版内**没装 tmux(和 Windows 侧无关)→ 引导用户在发行版里 `apt/dnf/apk` 装 |
| WSL 里看不到 Windows 文件 | Windows 盘在 `/mnt/c/...`;WSL 与 Windows 是两套文件系统/进程空间 → 路径转 `/mnt/<盘符>/...` |
| 会话/进程漏在后台 | 忘了收尾 → 跑完 `kill-session -t <name>`;别用 `kill-server`(会杀光所有会话) |
| 想读的是 scrollback 更早的内容 | 默认只抓可见屏 → `capture-pane -p -S -<N>`(或 `-S -` 抓全部 history) |

## 相关 skill

- **只要后台跑完拿结果、不用交互**:工作区自带 `background_start` / `background_stop`(见上表)。
- **调试 Node 运行时取值**:`skills/node-inspect-debugger/SKILL.md`。
- **委派整段编码任务给别的 agent**:`skills/codex/SKILL.md`、`skills/claude-code/SKILL.md`。
