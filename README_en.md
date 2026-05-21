# psi-agent

A microkernel-style Python agent framework. Three independent components — ai, session, channel — communicate via Unix domain sockets using OpenAI-compatible HTTP/SSE protocol.

## Design Philosophy

- **Microkernel**: Minimal core; capabilities defined by the workspace
- **Stateless components**: ai/session/channel are independent, composed via sockets
- **Fully async**: All IO uses anyio; never use raw asyncio or pathlib
- **Comprehensive logging**: loguru everywhere, every chunk traceable
- **Modern Python**: 3.14+, src-layout, no legacy baggage
- **Zero suppressions**: No per-file-ignores for ruff or ty, no noqa stacking

## Architecture

```
Channel (REPL/CLI) ←→ Session ←→ AI (OpenAI/Anthropic)
                   Unix socket  Unix socket
```

Each component starts independently and connects via socket paths. Session maintains a single in-memory history with no persistence.

## Quick Start

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
# Or one-shot CLI
uv run psi-agent channel cli \
  --session-socket ./channel.sock \
  --message "List the files in the current directory"
```

### REPL Controls

- `Enter` — Newline (multiline input)
- `Alt+Enter` — Send message
- `Ctrl+D` — Exit
- Reasoning content is displayed dimmed; normal content in default style

## CLI Structure

```
psi-agent
├── ai
│   ├── openai-completions    # OpenAI-compatible passthrough backend
│   └── anthropic-messages    # Anthropic→OpenAI conversion backend
├── session                    # Session + workspace management
└── channel
    ├── repl                   # Interactive REPL (Rich + prompt_toolkit)
    └── cli                    # One-shot CLI (Rich formatted output)
```

## Workspace Structure

```
workspace/
├── tools/           # *.py files, each defining a tool function
├── skills/          # */SKILL.md skill docs (enumerated by system_prompt_builder)
├── schedules/       # */TASK.md cron tasks (YAML header + cron + body)
└── systems/
    └── system.py    # async def system_prompt_builder() -> str
```

### Tool Definition

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

- Filename = function name
- Async function, non-private
- Parameter type annotations auto-mapped to JSON Schema types
- Google-style `Args:` parsed for parameter descriptions

### Scheduled Tasks

```markdown
---
name: daily-report
cron: "0 12 * * *"
---
Generate a daily progress report.
```

Triggered AI responses are stored and returned together with the next channel request.

## Development

```bash
uv run ruff check .          # lint
uv run ruff format --check . # format check
uv run ty check              # type checking
uv run pytest -v             # all tests
uv build                     # build
```

### Integration Tests

```bash
# Requires real API credentials — set environment variables
export PSI_TEST_OPENAI_API_KEY="sk-xxx"
export PSI_TEST_OPENAI_BASE_URL="https://api.llm.ustc.edu.cn/v1"
export PSI_TEST_OPENAI_MODEL="deepseek-v4-flash-ascend"
export PSI_TEST_ANTHROPIC_API_KEY="sk-xxx"
export PSI_TEST_ANTHROPIC_BASE_URL="https://api.llm.ustc.edu.cn/v1"
export PSI_TEST_ANTHROPIC_MODEL="deepseek-v4-flash-ascend"

uv run pytest -v
```

## Tech Stack

| Area | Technology |
|------|------------|
| Async | anyio |
| HTTP | aiohttp |
| CLI | tyro |
| REPL | prompt-toolkit + Rich |
| Logging | loguru |
| Lint/Format | ruff |
| Type checking | ty |
| Testing | pytest + pytest-asyncio |
| Build | uv + hatchling + hatch-vcs |
| Python | >= 3.14 |

## License

MIT
