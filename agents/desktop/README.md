# agents/desktop — 桌面版 (ToC) 能力包 🐬

**这是 `agents/feishu` 的抽取产物**: 保留通用能力并加入 desktop 专用的本地长期记忆，
去掉依赖飞书身份的工具、技能、提示词和事件源。内核对两个包都解析出全部 6 个 hook。

它的 agent 是 **Haitun (海豚)**, 组成:

- a de-branded OpenClaw-style system-prompt engine (all config kept **inside** the workspace),
- an embedded, same-process Fusion Memory runtime isolated by normalized workspace path,
- full **Workflow** authoring (the formal language defined by
  `FusionFlow.g4`, hosted by the `workflow` skill, with an explicit
  TypeScript fallback under `fusion-flow-legacy`, plus `flow_manage` + `flows/`),
- the hermes domain skill set + curated skills, and
- clean async file/shell tools, Serper web search, and environment-configured
  iFLYTEK STT/TTS tools.

See `AGENTS.md` for the full layout and conventions.

The Haibao ChatBI MCP Adapter, public tools, and Skill are bundled in this workspace. They
require an operator-provisioned private MCP server, which is not bundled or claimed to be
deployed in production. See [`docs/haibao-integration.md`](docs/haibao-integration.md) for
configuration, behavior, and production gates.

`HAIBAO_MCP_TOKEN` is process-global: one Haitun process/workspace deployment is one configured
Haibao principal and security boundary. It does not forward per-session identity. Never use one
token/process to serve users who require distinct authorization; deploy a separate Haitun
process, container, or workspace with a distinct token for each principal or distinct
authorization cohort.

## Run

Three terminals:

```bash
# 1) AI backend
uv run psi-agent ai \
  --provider openai --model <model> --api-key <key> \
  --base-url <url> --session-socket /tmp/ai.sock

# 2) Session (this workspace)
uv run psi-agent session \
  --workspace agents/desktop \
  --ai-socket /tmp/ai.sock --channel-socket /tmp/ch.sock

# 3) REPL
uv run psi-agent channel repl --session-socket /tmp/ch.sock
```

## Notes

- **First run** triggers a short onboarding (from `BOOTSTRAP.md`). Delete `BOOTSTRAP.md` to
  skip it.
- **Workflow** is the default for new workflows. Its formal G4 language uses the
  bundled Python parser/compiler and checked `run_flow` executor for Agent and Program Steps;
  Human waits continue through `run_flow_resume`. No separate setup is required. The existing
  `fusion-flow-legacy` Node/Fuclaw runtime remains available for explicit `.flow.ts` work:
  first use `cd agents/desktop/skills/fusion-flow-legacy && npm install`.
  One-off flows go under `flows/<task-slug>/`; saved reusable declarations go under
  `flows/workflows/<slug>/`. `flows/curated/` remains only as a compatibility catalog for
  `flow_manage` and legacy assets.
  For stateful sub-agent sessions, copy `bin/env.stateful.template` to
  `skills/fusion-flow-legacy/.env` and fill in the paths.
- **Serper search** needs psi-agent installed with the `mcp` extra and `uvx` on PATH.
- **Haibao ChatBI** needs the required operator-provisioned private MCP server and the three
  deployment-managed variables documented in `docs/haibao-integration.md`. The bundled Adapter,
  tools, and Skill do not provide the private service or database onboarding.
- Never put API keys in this workspace or in generated `.flow.ts` / `.env` files.
  The same rule applies to instruction payloads and generated `.workflow` / `.g4` files.

## Fusion Memory

Desktop Fusion Memory runs inside the existing Session process. Sessions using the same normalized workspace path share durable evidence; different workspaces remain isolated. The default files are `<workspace>/.fusion-memory/evidence.jsonl` and `<workspace>/.fusion-memory/memory.sqlite3`. JSONL is authoritative, while SQLite is a rebuildable FTS/dense index.

Only ordinary user/assistant chat text confirmed by the successful after-turn hook is ingested. Trigger, schedule, heartbeat, system/tool rows, reasoning, transfer markers, compaction output, and incomplete turns stay out of the journal. Histories created before this hook was active are not guessed or backfilled because their rows have no finish provenance. The runtime adds no extra process. Model or SQLite failures degrade without failing the completed chat.

Use `memory_search` for raw evidence and provenance, `memory_answer_context` for a bounded evidence pack, and `memory_add` only to promote existing source span IDs. Vector requests read `DASHSCOPE_API_KEY`; LLM configuration uses `FUSION_MEMORY_MODEL_*` or a complete `PSI_AI_*` fallback. Secrets are launcher-managed and never written to memory files.

## Smoke test

```bash
uv run python agents/desktop/systems/system.py
```

## Windows 安装包

`.github/workflows/pyinstaller.yml` 的 `haitun-inno-setup` job 会自动构建 Windows 安装程序：

1. PyInstaller 生成的 `psi-agent.exe` 被拷贝进本目录
2. Inno Setup（`.github/inno-setup/haitun.iss`）将整个 workspace 打包为安装程序
3. 安装后通过 `haitun.exe`（由 `.github/inno-setup/haitun.c` 编译）启动 Gateway，**显式**传入：
   - `--default-agent {app}`（安装目录即能力包：tools / skills / system）
   - `--default-workspace {Desktop}/haitun交付`（用户文件区；运行时解析桌面路径）

产物为 GitHub artifact `haitun-agent-installers`：完整包
`HaiTun_Agent_Setup.exe`、海豚组件包 `HaiTun_Agent_App_Setup.exe`、环境组件包
`msys-setup.exe`，以及 `haitun-version.txt` / `msys-version.txt`。

> `haitun.exe` 启动前会读取本目录下的 `.env`（若存在），把其中的 `KEY=VALUE` 注入 `psi-agent.exe` 的运行环境（跳过空行 / `#` 注释，剥离值两端成对引号）。

> 安装包自带一份 MSYS2（位于 `{app}\msys64`，含 bash/git/curl/ssh、以及 ucrt64 的 nodejs/npm/uv，保留 pacman）。`haitun.exe` 会把 `msys64\usr\bin` 与 `msys64\ucrt64\bin` 加到 `PATH` 最前，因此 `bash`、`node`、`npm`、`uv` 等在 Windows 上开箱即用，无需另装 Git Bash / Node。
