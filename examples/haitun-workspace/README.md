# haitun-workspace 🐬

A consolidated psi-agent workspace whose agent is **Haitun (海豚)**. It combines:

- a de-branded OpenClaw-style system-prompt engine (all config kept **inside** the workspace),
- full **Fusion Flow** workflow authoring (node runtime + `flow_manage` + `flows/`),
- the hermes domain skill set + curated skills, and
- clean async file/shell tools, Serper web search, and environment-configured
  iFLYTEK STT/TTS tools.

See `AGENTS.md` for the full layout and conventions.

## Run

Three terminals:

```bash
# 1) AI backend
uv run psi-agent ai \
  --provider openai --model <model> --api-key <key> \
  --base-url <url> --session-socket /tmp/ai.sock

# 2) Session (this workspace)
uv run psi-agent session \
  --workspace examples/haitun-workspace \
  --ai-socket /tmp/ai.sock --channel-socket /tmp/ch.sock

# 3) REPL
uv run psi-agent channel repl --session-socket /tmp/ch.sock
```

## Notes

- **First run** triggers a short onboarding (from `BOOTSTRAP.md`). Delete `BOOTSTRAP.md` to
  skip it.
- **Fusion Flow** needs Node.js. First use: `cd examples/haitun-workspace/skills/fusion-flow && npm install`.
  Generated flows go under `flows/<task-slug>/`; reusable templates under `flows/curated/`.
  For stateful sub-agent sessions, copy `bin/env.stateful.template` to
  `skills/fusion-flow/.env` and fill in the paths.
- **Serper search** needs psi-agent installed with the `mcp` extra and `uvx` on PATH.
- Never put API keys in this workspace or in generated `.flow.ts` / `.env` files.

## Fusion Memory

Haitun consumes an operator-provisioned Fusion Memory MCP service over
**Streamable HTTP**. Configure the launcher or deployment secret store with:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_TOKEN="<operator-issued-bearer-token>"
export FUSION_MEMORY_WORKSPACE_ID="haitun"
export FUSION_MEMORY_SESSION_ID="<current-session-id>"
```

`FUSION_MEMORY_MCP_URL` is the remote endpoint; TLS is terminated by its
reverse proxy. `FUSION_MEMORY_TOKEN` authenticates to the MCP service and must
never be committed, logged, printed, or placed in workspace files. Workspace
and session IDs provide request context only.

The bearer token defines the user identity. Memory is shared across sessions
and workspaces belonging to the same user. Different users, including users
with different tokens, are isolated. Haitun never accepts a client-provided
user ID for memory scope.

The service operator owns provisioning, token creation and revocation, reverse
proxy configuration, and service storage. The operator also supervises the MCP,
model, and history services with `systemd` so they survive SSH disconnects and
restart after process failures. Haitun only consumes the remote service; it
does not create local memory services or fall back to another transport.

Before calling any memory tool, follow the workspace consent policy. Without
required consent or when the remote service is unavailable, continue without
durable memory. See `skills/fusion-memory-setup/SKILL.md` for configuration and
operator health guidance.

## Smoke test

```bash
uv run python examples/haitun-workspace/systems/system.py
```

## Windows 安装包

`.github/workflows/pyinstaller.yml` 的 `haitun-inno-setup` job 会自动构建 Windows 安装程序：

1. PyInstaller 生成的 `psi-agent.exe` 被拷贝进本目录
2. `haitun.iss`（Inno Setup 脚本）将整个 workspace 打包为安装程序
3. 安装后通过 `haitun agent.vbs` 启动 `psi-agent gateway --tray --icon haitun.ico`

产物为 GitHub artifact `haitun-agent-installer`（`Haitun Agent Setup.exe`）。

> `haitun agent.vbs` 启动前会读取本目录下的 `.env`（若存在），把其中的 `KEY=VALUE` 注入 `psi-agent.exe` 的运行环境（跳过空行 / `#` 注释，剥离值两端成对引号）。

> 安装包自带一份 MSYS2（位于 `{app}\msys64`，含 bash/git/curl/ssh、以及 ucrt64 的 nodejs/npm/uv，保留 pacman）。`haitun agent.vbs` 会把 `msys64\usr\bin` 与 `msys64\ucrt64\bin` 加到 `PATH` 最前，因此 `bash`、`node`、`npm`、`uv` 等在 Windows 上开箱即用，无需另装 Git Bash / Node。
