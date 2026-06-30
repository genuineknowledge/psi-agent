# haitun-workspace 🐬

A consolidated psi-agent workspace whose agent is a **dolphin (海豚)**. It combines:

- a de-branded OpenClaw-style system-prompt engine (all config kept **inside** the workspace),
- full **Fusion Flow** workflow authoring (node runtime + `flow_manage` + `flows/`),
- the hermes domain skill set + curated skills, and
- clean async file/shell tools plus Serper web search.

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

## Smoke test

```bash
uv run python examples/haitun-workspace/systems/system.py
```

## Windows 安装包

`.github/workflows/pyinstaller.yml` 的 `haitun-inno-setup` job 会自动构建 Windows 安装程序：

1. PyInstaller 生成的 `psi-agent.exe` 被拷贝进本目录
2. `haitun.iss`（Inno Setup 脚本）将整个 workspace 打包为安装程序
3. 安装后通过 `haitun agent.vbs` 启动 `psi-agent gateway --tray haitun.ico`

产物为 GitHub artifact `haitun-agent-installer`（`Haitun Agent Setup.exe`）。

> `haitun agent.vbs` 启动前会读取本目录下的 `.env`（若存在），把其中的 `KEY=VALUE` 注入 `psi-agent.exe` 的运行环境（跳过空行 / `#` 注释，剥离值两端成对引号）。
