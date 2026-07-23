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
**Streamable HTTP**. Before starting Haitun, the process starter manually
configures the endpoint and token-map path:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_TOKEN_MAP_FILE="/absolute/path/to/memory_tokens.json"
```

`FUSION_MEMORY_MCP_URL` is the remote endpoint; TLS is terminated by its
reverse proxy. The map is keyed by Feishu `open_id`; each entry requires the
operator-issued `token`. `workspace_id` is provenance only and may be empty or
omitted, in which case it defaults to `haitun`. Keep the map outside this
workspace and source control, and never log, print, or return token values.

Map membership enables durable memory for that user. On the user's first
message after Haitun starts, the workspace automatically initiates
`memory_health` and starts a passive writer for that trusted
`feishu-<open_id>` Session. Completed user/assistant turns are persisted through
`memory_add_batch`; the same token shares memory across Sessions, while tokens
for different users remain isolated. Model-visible `<feishu_context>` never
selects credentials.

Users absent from the map can chat normally but receive no bearer token,
connector, passive writer, checkpoint, or durable memory. Duplicate token
assignments reject the map, and token-map mode never falls back to
`FUSION_MEMORY_TOKEN`. When no map path is configured, the legacy single-user
token/workspace/session variables remain compatible.

Passive persistence stores only completed ordinary chat turns. Schedule,
heartbeat, compaction, tool-only, and incomplete rows are excluded. Unchanged
history files are not reparsed each polling interval. Removing a map entry
stops that Session's watcher and closes its cached MCP client.
Validated map snapshots are cached by file signature and refreshed only when
the file changes. Each active turn renews a five-minute watcher lease; idle
watcher/client resources are reclaimed and restart on the next message.

This workspace-level isolation assumes the Feishu Channel, Gateway, Session
runtime and management tools, host shell, and token-map file are trusted.
`feishu-<open_id>` is a routing convention rather than a cryptographic
principal. Protecting against callers that can forge Session IDs, invoke
cross-Session operations, or read the token map requires runtime authorization
and a privileged credential broker outside this example workspace.

The service operator owns provisioning, token creation and revocation, reverse
proxy configuration, and service storage. The operator also supervises the MCP,
model, and history services with `systemd` so they survive SSH disconnects and
restart after process failures. Haitun only consumes the remote service; it
does not create local memory services or fall back to another transport. MCP
outages do not block chat, and the background writer retries independently.
Use `memory_health` for explicit status and
`skills/fusion-memory-setup/SKILL.md` for operator recovery guidance.

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
