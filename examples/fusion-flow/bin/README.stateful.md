# psi 引擎接线（常驻会话 shim）

`FLOW_ENGINE=psi` 时，Fusion Flow 的运行时 bundle 会发出**老式**调用：

```
psi-agent run --workspace <W> --message <M> --output-format text [--model ...] [--ai ...] ...
```

但当前 psi-agent CLI 的 `run` 子命令已经是 **YAML 批量启动器**——它只接受一个位置参数
（config 文件路径），不认上面那些 flag。裸调的结果是：

```
╭─ Missing argument ─────────────────────────────────────╮
│ Missing value for argument 'config'. Expected 1 values.│
╰────────────────────────────────────────────────────────╯
exit=2
```

这就是「psi 引擎与当前 psi-agent CLI 不兼容」的真相：不是 CLI 坏了，是 bundle 走了老接口。

## 解决：把调用交给 shim 翻译

`bin/session_shim.py` 把上面那套老式 `run` 翻译成新三层架构：

```
ai --provider <name> --session-socket <S>          # 共享 AI backend（每个 run 起一次）
session --workspace <W> --channel-socket <C> --ai-socket <S>   # 每个角色一个常驻 session
channel cli --session-socket <C> --message <prompt>            # 每轮把 prompt 送进去
```

附带好处：同一角色（同 system prompt → 同 session key）跨多轮**复用同一个 session**，
既保持记忆、又省掉每轮重灌 system 的 token。

## 接线步骤

1. 复制模板 `bin/env.stateful.template` 到你运行 `npx tsx` 的目录（workDir）下的 `.env`
   （bundle 用 dotenv 从 CWD 加载）。
2. 按你的系统填好，关键三行：

   **POSIX（macOS / Linux）**
   ```
   FLOW_ENGINE=psi
   FLOW_PSI_WORKSPACE=/abs/path/to/executor-workspace
   FLOW_PSI_COMMAND=python3
   FLOW_PSI_COMMAND_ARGS=/abs/path/to/fusion-flow-workspace/bin/session_shim.py
   ```

   **Windows**（`python3` 常不存在用 `python`；路径用正斜杠）
   ```
   FLOW_ENGINE=psi
   FLOW_PSI_WORKSPACE=C:/abs/path/to/executor-workspace
   FLOW_PSI_COMMAND=python
   FLOW_PSI_COMMAND_ARGS=C:/abs/path/to/fusion-flow-workspace/bin/session_shim.py
   FUSION_SHIM_STATE_DIR=C:/Users/<you>/AppData/Local/Temp/fusion-shim-run
   ```

3. `PSI_CMD`（shim 内部起 psi-agent 用的命令前缀）：
   - 在 psi-agent 仓库工作树内：留默认 `uv run --no-sync psi-agent`
   - psi-agent 已全局安装：设为 `psi-agent`

4. 跑完 flow 后回收常驻进程/socket：
   ```
   python bin/session_cleanup.py            # 读 $FUSION_SHIM_STATE_DIR
   ```

## 自检

不接真实 AI，仅验证 shim 能解析 bundle 的调用：

```bash
python - <<'PY'
import importlib.util
s = importlib.util.spec_from_file_location("shim", "bin/session_shim.py")
m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
argv = ["run","--workspace","/tmp/ws","--message","ROLE\n\n---\n\nHi","--output-format","text"]
a = m._parse_args(argv); sys_, prompt = m._split_message(a["message"])
print("workspace:", a["workspace"], "| system:", repr(sys_), "| prompt:", repr(prompt))
print("psi_cmd:", m._psi_cmd())
PY
```

预期打印出正确拆分的 workspace / system / prompt，以及可执行的 psi 命令前缀。

## Windows 注意

- 端点用命名管道 `\\.\pipe\psi-shim-<tag>-<name>`（psi-agent 的 `_sockets` 层只认这个前缀）。
- `PSI_CMD` 若填带反斜杠的绝对路径（`C:\tools\psi-agent.exe`），shim 已用 `shlex.split(posix=False)`
  避免反斜杠被当转义吃掉。
- git-bash：claude 引擎才需要；psi 引擎不需要 git-bash。
