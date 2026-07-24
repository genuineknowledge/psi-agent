---
name: python-debugpy
description: "Debug Python programs two ways — the standard-library pdb REPL for interactive/post-mortem inspection, and debugpy for remote DAP (Debug Adapter Protocol) attach over a socket. Use whenever a task needs to step through Python code, inspect variables/stack at a crash, set breakpoints, or drive a debug session on a running/remote process (containers, servers, another venv). Covers non-interactive pdb via `-c`/commands file, post-mortem `pdb.pm()`, `python -m debugpy --listen/--connect/--wait-for-client/--pid`, SSH-tunnelled remote attach, and driving a minimal DAP client from bash with anyio (already a core dep — no new packages). NOT for Node.js (use node-inspect) or static analysis (use python-static-analysis)."
category: coding
---

# Python 调试：pdb REPL + debugpy 远程 (DAP)

用这个技能调试 Python 程序，两条路互补：

1. **pdb** —— 标准库自带的交互式调试器。零依赖，随处可用。适合本地单进程：
   下断点、单步、看变量/调用栈、崩溃后事后剖析（post-mortem）。
2. **debugpy** —— 微软出的调试后端，把被调试进程变成一个 **DAP（Debug Adapter
   Protocol）服务端**，让调试客户端通过 socket 远程连接。适合：容器里、远程主机、
   另一个 venv/Python 版本、已在运行的进程（按 PID attach）、被调试进程不能停在终端的场景。

**心智模型**：pdb 是"我就在这台机器、这个终端里，直接停下来看"。debugpy 是"进程在别处
（或不能占用终端），我从外面连过去看"。能用 pdb 解决就别上 debugpy——后者是为跨进程/跨机器而生。

除非用户明显用其它语言，一律用中文回复。

## 依赖与边界（重要）

- **pdb 是标准库**，`python` 在哪儿它就在哪儿，**永不需要安装**。
- **debugpy 注入的是"被调试的目标程序"进程，不是 agent 本体。** 目标程序常跑在它自己的
  venv / Python 版本 / 容器里，所以 debugpy 要**装进目标那个环境**，而不是 psi-agent 的依赖清单。
  因此本技能**不改** `pyproject.toml` / `nuitka` / `pyinstaller` —— 那些只管 agent 自身运行时
  import 的库。给目标环境装 debugpy 用临时/就地方式（见下）：
  ```bash
  # 首选：不污染目标环境的临时装法（目标用 uv 管理时）
  uv run --with debugpy python -m debugpy --listen 5678 --wait-for-client target.py
  # 或就地装进目标环境
  python -m pip install debugpy        # 目标的 python/venv
  ```
- **驱动 DAP 客户端不需要新依赖。** 仓库已有 `anyio`（核心依赖），用它写一次性 DAP 客户端脚本
  即可异步收发协议消息（见"从 bash 驱动 DAP 客户端"）。**不要**为此往 `pyproject.toml` 加包。
  真要加运行期第三方库时才需异步库，并同步 `pyproject.toml` + `.github/workflows/nuitka.yml`
  （`--include-package`）+ `.github/workflows/pyinstaller.yml`（`--collect-submodules`）三处——
  但本技能不涉及。

## 何时用哪条路

| 场景 | 用 |
|------|-----|
| 本地脚本崩了要看栈/变量 | pdb 事后剖析 `pdb.pm()` |
| 想在某行停下单步 | pdb `breakpoint()` / `break` |
| 进程在容器/远程主机 | debugpy `--listen` + SSH 隧道 |
| 进程已在运行，不能重启 | debugpy `--pid <PID>` attach |
| 目标在另一个 venv/Python 版本 | debugpy（装进那个环境） |
| Web 服务/长驻进程不能占终端 | debugpy `--listen`，不 `--wait-for-client` |
| Node.js 程序 | 不是本技能，用 node-inspect |
| 只是想查静态问题/类型 | 不是本技能，用 python-static-analysis |

## Part 1 — pdb REPL

### 关键：agent 是非交互的，别裸奔 `python -m pdb`

pdb 是**交互式**的——直接 `python -m pdb foo.py` 会停在 `(Pdb)` 提示符等你敲命令，
而 agent 通过 `bash` 工具跑的是**非交互**子进程，会话拿不到 stdin，直接挂死或超时。
所以用 pdb 时**必须把命令预先喂给它**，三种可靠姿势：

**(a) heredoc 喂命令（最常用）**——把要执行的 pdb 命令按行送进 stdin：
```bash
python -m pdb target.py <<'PDB'
break target.py:42
continue
print(some_var)
where
continue
quit
PDB
```
最后务必以 `continue`（跑完）或 `quit` 收尾，否则进程停在提示符不退出。

**(b) `-c` 预置命令 + `.pdbrc`**——把常用命令写进 `.pdbrc`，或用 `-c`：
```bash
# 当前目录放 .pdbrc，pdb 启动时自动执行其中每行命令
printf 'break target.py:42\ncontinue\npp locals()\n' > .pdbrc
python -m pdb target.py <<<'continue'
```

**(c) 代码里下断点，配 `PYTHONBREAKPOINT`**——`breakpoint()` 命中时进 pdb；
非交互场景可用环境变量改行为或直接走 debugpy（见 Part 2）：
```bash
# 关掉断点（快速跳过所有 breakpoint()）
PYTHONBREAKPOINT=0 python target.py
```

### 事后剖析（post-mortem）——崩溃后看现场，最实用

程序抛未捕获异常后，直接进出错那一刻的栈，不用改代码、不用重跑：
```bash
# 让脚本崩溃后自动进入 post-mortem pdb（喂命令进去看）
python -m pdb -c continue target.py <<'PDB'
where
up
print(locals())
quit
PDB
```
`-c continue` 让 pdb 先正常跑脚本，崩溃时它自动停在异常帧，然后你喂的 `where`/`up`/`print`
就在**出错现场**执行。也可在代码里：
```python
import pdb, sys
try:
    main()
except Exception:
    pdb.post_mortem(sys.exc_info()[2])   # 或交互时直接 pdb.pm()
```

### 常用 pdb 命令速查

| 命令 | 作用 |
|------|------|
| `break file:line` / `b` | 下断点（可加条件 `b 42, x>10`） |
| `continue` / `c` | 继续运行到下一断点 |
| `next` / `n` | 下一行（不进函数） |
| `step` / `s` | 单步进入函数 |
| `return` / `r` | 跑到当前函数返回 |
| `where` / `w` / `bt` | 打印调用栈 |
| `up` / `down` / `u` `d` | 在栈帧间上下移动 |
| `print expr` / `pp expr` | 求值 / 美化打印 |
| `args` / `a` | 当前帧的参数 |
| `list` / `l` | 显示当前位置源码 |
| `!stmt` | 在当前帧执行任意 Python 语句 |
| `quit` / `q` | 退出调试器 |

### 调试测试

pytest 命中失败/断点时进 pdb：
```bash
pytest --pdb            # 失败即进 post-mortem
pytest --trace          # 每个测试开头就停
pytest -x --pdb -s      # 第一个失败就停，-s 放行 stdin
```
同样注意非交互问题——CI/agent 环境下 `--pdb` 需要能喂命令，或改用打日志/断言定位。

## Part 2 — debugpy 远程调试 (DAP)

### 服务端：让目标进程听候连接

`--listen` = 目标进程当**服务端**在 host:port 上等客户端连过来（最常用）：
```bash
# host 省略默认 127.0.0.1；--wait-for-client 让代码停在第一行等客户端
python -m debugpy --listen 5678 --wait-for-client target.py

# 调试模块而非文件
python -m debugpy --listen 5678 -m mypackage.main

# 长驻服务：不加 --wait-for-client，进程照常起，随时可 attach
python -m debugpy --listen 127.0.0.1:5678 -m uvicorn app:api
```

`--connect` = 反过来，目标进程当**客户端**主动连一个**已在监听**的调试端（用于目标在
NAT/防火墙后、只能出不能进的场景）。两端 verb 永远相反：一边 `--listen`，另一边就 `connect`。

**Attach 到已在运行的进程**（不重启目标）：
```bash
python -m debugpy --listen 5678 --pid 12345
```

### 安全：默认只绑 127.0.0.1，远程走 SSH 隧道

`--listen` 到 `0.0.0.0` 或公网 IP = **任何能连到该端口的人都能在目标进程里执行任意代码**。
所以：
- 默认只绑 `127.0.0.1`。
- 跨机器调试**别开公网端口**，用 SSH 端口转发把远端 5678 映到本地：
  ```bash
  # 远端容器/主机内：只绑本地
  python -m debugpy --listen 127.0.0.1:5678 --wait-for-client target.py
  # 本地：把远端 5678 隧道到本地 5678，客户端连 localhost:5678 即可
  ssh -N -L 5678:127.0.0.1:5678 user@remote-host
  ```

### 代码内 API（改脚本时用）

```python
import debugpy
debugpy.listen(5678)          # 起 DAP 服务端，等价 --listen
debugpy.wait_for_client()     # 阻塞直到客户端连上（等价 --wait-for-client）
debugpy.breakpoint()          # 到这行主动断下（客户端会停在此）
```
能用 CLI（`python -m debugpy ...`）就别改脚本——CLI 的好处正是不用往代码里塞调试 import。

### 从 bash 驱动 DAP 客户端（无 IDE 时）

agent 没有 VS Code 这类 DAP 前端。两种办法连上 debugpy 服务端：

**首选：交互式客户端。** 如果宿主装了支持 DAP-attach 的调试前端（如 VS Code + Python 扩展、
nvim-dap），直接用 `"request": "attach"` + `"connect": {"port": 5678}` 连。多数交付场景到这步
就把断点/单步交给前端。

**脚本化：用 anyio 写一次性 DAP 客户端**（仓库已有 `anyio`，**不加新依赖**）。DAP 走 TCP，
消息是 `Content-Length: N\r\n\r\n<json>` 的帧格式。最小骨架——连上、初始化、设断点、continue、
读事件：
```python
# dap_probe.py —— 一次性脚本，用 uv run python dap_probe.py 跑
import anyio, json

async def send(stream, seq, cmd, args=None):
    body = json.dumps({"seq": seq, "type": "request",
                       "command": cmd, "arguments": args or {}}).encode()
    await stream.send(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)

async def recv(stream, buf):
    while b"\r\n\r\n" not in buf[0]:
        buf[0] += await stream.receive()
    head, buf[0] = buf[0].split(b"\r\n\r\n", 1)
    n = int(dict(h.split(b": ") for h in head.split(b"\r\n"))[b"Content-Length"])
    while len(buf[0]) < n:
        buf[0] += await stream.receive()
    msg, buf[0] = buf[0][:n], buf[0][n:]
    return json.loads(msg)

async def main():
    stream = await anyio.connect_tcp("127.0.0.1", 5678)
    buf = [b""]
    seq = 1
    await send(stream, seq, "initialize", {"adapterID": "debugpy"}); seq += 1
    await send(stream, seq, "attach", {}); seq += 1
    await send(stream, seq, "setBreakpoints",
               {"source": {"path": "target.py"}, "breakpoints": [{"line": 42}]}); seq += 1
    await send(stream, seq, "configurationDone", {}); seq += 1
    for _ in range(20):                     # 读若干条响应/事件
        print(await recv(stream, buf))

anyio.run(main)
```
这只是骨架——真正逐步调试要按 DAP 时序处理 `stopped` 事件、发 `stackTrace`/`scopes`/
`variables`/`continue`。**除非任务确实需要脚本化远程单步，否则优先 pdb 或交互式前端**，
别为一次性调试造复杂 DAP 客户端。

## 推荐流程

1. **先判断在哪调**：同机同进程能停终端 → pdb；跨进程/跨机/不能占终端 → debugpy。
2. **崩溃类问题优先 post-mortem**：`python -m pdb -c continue`，直接落在出错帧，成本最低。
3. **要下断点单步**：本地喂命令给 pdb（heredoc/`.pdbrc`）；远程用 debugpy `--listen` +（需要时）
   SSH 隧道，交给交互式前端或脚本化 DAP 客户端。
4. **给目标装 debugpy 用临时/就地方式**（`uv run --with debugpy` 或目标环境 `pip install`），
   **不碰 psi-agent 的 `pyproject.toml`/打包配置**。
5. **收尾**：pdb 会话确保以 `continue`/`quit` 退出，debugpy 服务端调完关闭端口，SSH 隧道 `Ctrl-C`。

## 常见坑

- **`python -m pdb` 挂死**：非交互环境没喂命令，进程停在 `(Pdb)` 等 stdin。→ 用 heredoc/`.pdbrc`
  预置命令，并以 `continue`/`quit` 收尾。
- **debugpy `--wait-for-client` 后"卡住不动"**：这是**预期**——它在等客户端连。要么连客户端，
  要么去掉该 flag 让进程照常跑（长驻服务用后者）。
- **连不上远端 5678**：多半是只绑了 `127.0.0.1`（对，别改成 0.0.0.0），应走 SSH 隧道把端口映到本地。
- **绑 `0.0.0.0`/公网 = 远程代码执行风险**：不要。仅在可信隔离网络且明确需要时才开，优先隧道。
- **`--pid` attach 失败**：目标与 debugpy 的 Python 版本/位数要匹配，且需相应权限（同用户/可 ptrace）。
- **想在 agent 依赖里装 debugpy**：不必要——它属于被调试的目标环境，不是 agent 运行时。

