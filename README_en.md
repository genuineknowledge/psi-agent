# psi-agent

> [中文](README.md)

A micro-framework for assembling AI agents from Python sockets. No config files, no database — three components start independently and plug together over Unix sockets.

## Why

- **Simple composition**: ai, session, channel — three standalone processes connected through sockets. No central config, no database dependency
- **Workspace is the agent**: Drop Python functions in `workspace/` for tools, write a system prompt for personality, add a cron for scheduled tasks
- **Streaming interactions**: REPL shows AI reasoning (dimmed) and final response in real time

## Quick Start

Three terminals, three commands:

```bash
# Install
uv sync

# Terminal 1: Start AI backend
uv run psi-agent ai openai-completions \
  --session-socket ./ai.sock \
  --model tencent/hy3-preview:free \
  --api-key sk-or-v1-xxxx \
  --base-url https://openrouter.ai/api/v1

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

## CLI Overview

```
psi-agent
├── ai
│   ├── openai-completions    # OpenAI-compatible passthrough
│   └── anthropic-messages    # Anthropic→OpenAI conversion
├── session                    # Session + workspace management
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
├── skills/                   # */SKILL.md skill documentation
│   └── bash-expert/
│       └── SKILL.md
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

## License

MIT
