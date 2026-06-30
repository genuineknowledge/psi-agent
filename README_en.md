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
User ←→ Channel (REPL/CLI/Telegram/Feishu) ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
```

## Quick Start

> Requires Python >= 3.14

Three terminals, three commands:

```bash
# Install
uv sync

# Terminal 1: Start AI backend (--api-key optional, reads PSI_AI_API_KEY env if omitted)
uv run psi-agent ai \
  --provider openai \
  --session-socket ./ai.sock \
  --model gpt-4o-mini \
  --api-key $OPENAI_API_KEY \
  --base-url https://api.openai.com/v1

# Terminal 2: Start Session (--workspace optional, defaults to .)
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

## CLI Overview

```
psi-agent
├── run                       # Batch launch (psi-agent run config.yml)
├── ai                        # Unified AI backend (50+ providers)
├── gateway                   # Lifecycle management + REST API + Web Console
├── session                    # Session + workspace management
└── channel
    ├── repl                   # Interactive REPL
    ├── cli                    # One-shot message
    ├── telegram               # Telegram bot
    ├── feishu                 # Feishu bot
    └── feishu-setup           # Feishu scope-manifest setup helper
```

## Feishu Bot Setup

If you already have a Feishu/Lark scope manifest JSON like:

```json
{
  "scopes": {
    "tenant": ["im:message", "im:message:send_as_bot"],
    "user": ["im:chat.access_event.bot_p2p_chat:read"]
  }
}
```

you can generate a local setup bundle with:

```bash
uv run psi-agent channel feishu-setup ./scopes.json \
  --app-id cli_xxx \
  --output-dir ./feishu-setup
```

This command does not pretend to fully configure the Feishu Open Platform for you. Instead it:

- normalizes and classifies the scopes for the current `channel feishu` runtime
- optionally probes `app_access_token` and `tenant_access_token`
- writes `report.json`, `NEXT_STEPS.md`, and `start-feishu.ps1.example`

The generated `NEXT_STEPS.md` tells you which remaining steps still need to be done manually in the Feishu developer console.

## Define Your Own Agent

A workspace is an agent:

```
my-workspace/
├── tools/                    # One .py file = one or more tools (all non-_ async def)
│   └── bash.py               # async def bash(command: str) -> str
├── skills/                   # */SKILL.md skill docs (enumerated by system_prompt_builder)
├── schedules/                # Cron-triggered tasks
│   └── daily-report/
│       └── TASK.md           # YAML header (name, cron) + Markdown body
└── systems/
    └── system.py             # async def system_prompt_builder() -> str
```

A tool is just an async function — every non-`_` `async def` in the file is loaded as a tool:

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

MIT License. See [LICENSE](LICENSE.md).
