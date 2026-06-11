# psi-agent

> [中文](README.md)

A micro-framework for assembling AI agents from Python sockets.

You write Python functions and Markdown. The framework handles socket communication, tool calling, SSE streaming, and cron scheduling for you.

## Why

- **Simple composition**: ai, session, channel — three standalone processes connected through sockets. No central config, no database
- **Workspace is the agent**: Drop Python functions in `workspace/` for tools, write a system prompt for personality, add a cron for scheduled tasks
- **Streaming interactions**: REPL shows AI reasoning (dimmed) and final response in real time

## Architecture

```
User ←→ Channel (REPL/CLI) ──Unix socket── Session ──Unix socket── AI (OpenAI/Anthropic)
```

## Quick Start

> Requires Python >= 3.14

Beginner-friendly one-shot setup:

```bash
uv sync
uv run psi-agent init
uv run psi-agent doctor
uv run psi-agent run --message "Summarize what you can do in one sentence"
```

`psi-agent init` creates `~/.psi-agent/config.toml` and a default workspace at
`~/.psi-agent/workspaces/default`. Set the API key environment variable shown by
`psi-agent init` before running a real model request.

Three terminals, three commands:

```bash
# Install
uv sync

# Check your setup
uv run psi-agent doctor --workspace ./examples/fusion-flow-workspace

# Terminal 1: Start AI backend (--api-key optional, reads OPENAI_API_KEY env if omitted)
uv run psi-agent ai openai-completions \
  --session-socket ./ai.sock \
  --model gpt-4o-mini \
  --api-key $OPENAI_API_KEY \
  --base-url https://api.openai.com/v1

# Terminal 2: Start Session
uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock

# Terminal 3: Interactive REPL
uv run psi-agent channel repl --session-socket ./channel.sock
```

REPL controls: `Enter` for newline, `Alt+Enter` to send, `Ctrl+D` to exit.

Or one-shot:

```bash
uv run psi-agent channel cli \
  --session-socket ./channel.sock \
  --message "List files in the current directory"
```

External frameworks can call `psi-agent run` directly. Put model credentials in
`~/.psi-agent/config.toml` so callers do not store API keys:

```toml
default_profile = "fusion"

[profiles.fusion]
ai = "openai-completions"      # or "anthropic-messages"
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY" # recommended; reads the key from your shell env
# api_key = "..."              # supported, but api_key_env is safer
```

```bash
uv run psi-agent run \
  --workspace ./examples/a-simple-bash-only-workspace \
  --message "Summarize what you can do in one sentence" \
  --profile fusion
```

### Fusion Flow workspace

`examples/fusion-flow-workspace` packages the Fusion Flow skill bundle as a
psi-agent workspace skill. Use it when natural-language user requests should
author or run `.flow.ts` multi-agent workflows:

```bash
uv run psi-agent run \
  --workspace ./examples/fusion-flow-workspace \
  --profile fusion \
  --message "Build a parallel code-review workflow with security, performance, and readability reviewers."
```

The workspace loads `skills/fusion-flow/SKILL.md`, writes generated flows under
`skills/fusion-flow/examples/`, and uses `FLOW_ENGINE=psi` with a separate
executor workspace to avoid recursive self-calls.

## CLI Overview

```
psi-agent
├── ai
│   ├── openai-completions    # OpenAI-compatible passthrough
│   └── anthropic-messages    # Anthropic→OpenAI conversion
├── session                    # Session + workspace management
├── run                        # One-shot workspace-backed agent call
├── init                       # Starter config and default workspace setup
├── doctor                     # Setup checks and next-step guidance
└── channel
    ├── repl                   # Interactive REPL
    └── cli                    # One-shot message
```

## Define Your Own Agent

A workspace is an agent:

```
my-workspace/
├── tools/                    # One .py file = one tool
│   └── bash.py               # async def bash(command: str) -> str
├── skills/                   # */SKILL.md skill docs (enumerated by system_prompt_builder)
├── schedules/                # Cron-triggered tasks
│   └── daily-report/
│       └── TASK.md           # YAML header (name, cron) + Markdown body
└── systems/
    └── system.py             # async def system_prompt_builder() -> str
```

A tool is just an async function — **function name = filename**, parameter types auto-map to JSON Schema:

```python
# tools/bash.py
import anyio

async def bash(command: str) -> str:
    """Execute a bash command.
    Args:
        command: The command to run.
    """
    result = await anyio.run_process(["/bin/bash", "-c", command])
    return result.stdout.decode().strip()
```

Schedule bodies are sent to the AI as messages when triggered:

```markdown
---
name: daily-report
cron: "0 12 * * *"
---
Generate a daily progress report.
```

See `examples/a-simple-bash-only-workspace/` for a complete example.

## Development

```bash
uv run ruff check .          # lint
uv run ruff format --check . # format
uv run ty check              # types
uv run pytest -v             # tests
```

## Author

Hao Zhang <hzhangxyz@outlook.com>

## License

GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE.md).
