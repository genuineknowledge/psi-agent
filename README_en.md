# psi-agent

> [中文](README.md)

A microkernel framework for assembling AI agents from Python sockets.

You write Python functions and Markdown. The framework handles socket communication, tool calling, SSE streaming, cron scheduling, conversation persistence, and a Web management console for you.

## Why

- **Simple composition**: ai, session, channel — three standalone processes connected through sockets. No central config, no database
- **Workspace is the agent**: Drop Python functions in `workspace/` for tools, write a system prompt for personality, add a cron for scheduled tasks
- **Streaming interactions**: REPL shows AI reasoning (dimmed) and final response in real time
- **One-command launch**: `psi-agent run config.yml` starts AI + Session + Channel from a single YAML
- **Web management console**: `psi-agent gateway` starts a REST API + Web Console SPA for visual management

## Architecture

```
User ←→ Channel (REPL/CLI/Telegram/Feishu/Web UI) ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
```

AI layer is stateless. Session maintains conversation history. Channel is a pure UI client. All three are independent processes communicating via the OpenAI Chat Completions HTTP/SSE protocol over sockets.

For developers: start components independently, mix and match for debugging and customization. For users: `psi-agent run config.yml` launches everything in one command, and `psi-agent gateway` provides a visual web console for managing everything.

## Quick Start

> Requires Python >= 3.14

### Option 1: Start individually

Three terminals, three commands:

```bash
# Install
uv sync

# Terminal 1: Start AI backend (--api-key optional, reads PSI_AI_API_KEY env if omitted)
uv run psi-agent ai \
  --provider openai \
  --session-socket ./ai.sock \
  --model gpt-4o-mini \
  --api-key sk-xxxx \
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

### Option 2: YAML batch launch

Write a `config.yml` and launch everything with one command:

```bash
uv run psi-agent run config.yml
```

`config.yml` format:

```yaml
- type: ai
  provider: openai
  session_socket: ./ai.sock
  model: gpt-4o-mini
  api_key: sk-xxxx                 # omit to fall back to PSI_AI_API_KEY env var
  base_url: https://api.openai.com/v1

- type: session
  workspace: ./examples/a-simple-bash-only-workspace  # optional, defaults to .
  channel_socket: ./channel.sock
  ai_socket: ./ai.sock

- type: channel
  name: repl                        # cli / repl / telegram / feishu
  session_socket: ./channel.sock
```

Servers (ai, session) run forever; channel components exit as needed (CLI exits after response, REPL runs until Ctrl+D).

### Option 3: Gateway Web Console

Start the Gateway and manage everything in your browser:

```bash
uv run psi-agent gateway
```

Open the printed address (random port on 127.0.0.1 by default) to see a Material Design 3 Web Console. From the UI you can:

- **Connect LLMs**: Choose from 50+ providers, enter your API key
- **Create sessions**: Pick a workspace directory, start a Session
- **Chat**: Markdown + LaTeX rendering, file upload/download, streaming output
- **Manage**: Sidebar session switching, double-click rename, delete with confirmation
- **Auto titles**: AI generates session titles after first conversation

> **Security note**: Gateway listens on `127.0.0.1` by default. Do not use `--listen 0.0.0.0` — this would expose arbitrary directory listing.

Gateway also supports system tray icon (`--tray icon.png`) and disabling auto browser open (`--no-browser`).

## CLI Overview

```
psi-agent
├── run                       # YAML batch launch (psi-agent run config.yml)
├── ai                        # Unified AI backend (50+ providers)
├── gateway                   # Lifecycle management + REST API + Web Console
├── session                    # Session + workspace management
└── channel
    ├── repl                   # Interactive REPL
    ├── cli                    # One-shot message
    ├── telegram               # Telegram bot
    └── feishu                 # Feishu bot
```

## Transports

All components auto-detect transport type via address prefix:

| Address Format | Transport |
|----------------|-----------|
| `./ai.sock` (bare filesystem path) | Unix socket |
| `http://127.0.0.1:8080` | TCP |
| `\\.\pipe\name` (Windows) | Named Pipe |

AI and Session components are transport-agnostic — handled uniformly by `_sockets.py`.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `PSI_AI_PROVIDER` | AI provider (openai / anthropic / gemini ...) |
| `PSI_AI_MODEL` | Model name |
| `PSI_AI_API_KEY` | API key |
| `PSI_AI_BASE_URL` | Upstream base URL |
| `PSI_TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `PSI_TELEGRAM_PROXY` | Telegram SOCKS5 proxy |
| `PSI_FEISHU_APP_ID` | Feishu app ID |
| `PSI_FEISHU_APP_SECRET` | Feishu app secret |

CLI args take precedence over environment variables. All parameters are optional and fall back to env vars when omitted.

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

### Tools

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

- Supported parameter types: `str`, `int`, `float`, `bool`, `list[X]`, `X | None`
- Google-style docstrings (`Args:` section) are automatically converted to tool descriptions
- Tools are hot-reloaded before every agent turn — modify files without restarting

### System Prompt

Define two optional async functions in `systems/system.py`:

```python
async def system_prompt_builder() -> str:
    """Construct the system prompt. Returns a string."""
    return "You are a helpful assistant."

async def system_prompt_rebuild_checker() -> bool:
    """Called before every agent turn. Return True to rebuild the system prompt."""
    return False
```

- `builder` is lazily called on the first conversation turn
- `checker` runs before each turn, useful for auto-refreshing prompts when files change
- Both are optional; sensible defaults are used when absent

### Scheduled Tasks

Defined in `schedules/<name>/TASK.md` with YAML front matter specifying name and cron expression. The body is sent to the AI as a message when triggered:

```markdown
---
name: daily-report
cron: "0 12 * * *"
---
Generate a daily progress report.
```

- Each schedule has an independent CancelScope and supports hot-reload
- `croniter` parses cron expressions; invalid expressions skip that schedule
- Schedule triggers acquire the session lock and execute serially

### Skills

Create `SKILL.md` files under any directory in `skills/`:

```
skills/
└── my-skill/
    └── SKILL.md       # Arbitrary Markdown content
```

`system_prompt_builder` should, by convention, scan `skills/*/SKILL.md`, parse YAML front matter, and inject content into the system prompt. The psi-agent framework does not directly parse skill files — the workspace defines how to use them.

### Conversation History Persistence

Session automatically persists conversation history to `workspace/histories/{session_id}.jsonl`:

- `--session-id` auto-generates a UUID when omitted; provide a custom ID to resume a session
- JSONL line-by-line format, atomic writes (tempfile + `os.replace`)
- **Turn-level atomicity**: only successfully completed turns are persisted; rollback on errors

```bash
# New session
uv run psi-agent session --session-id mychat --channel-socket ./c.sock --ai-socket ./ai.sock

# Resume later
uv run psi-agent session --session-id mychat --channel-socket ./c.sock --ai-socket ./ai.sock
```

## Gateway REST API

Gateway exposes the following REST endpoints (see [Gateway layer docs](src/psi_agent/gateway/AGENTS.md) for details):

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ais` | Create AI instance |
| DELETE | `/ais/{ai_id}` | Delete AI |
| GET | `/ais` | List all AIs |
| POST | `/sessions` | Create Session |
| DELETE | `/sessions/{session_id}` | Delete Session |
| GET | `/sessions` | List all Sessions |
| POST | `/sessions/{session_id}/chat` | Web UI chat (SSE stream) |
| GET | `/sessions/{session_id}/history` | Get conversation history |
| GET | `/titles` | Get all session titles |
| POST | `/titles` | Set session title |
| POST | `/titles/generate` | AI auto-generate title |
| GET | `/workspace/browse` | Browse directory (`?path=...`) |
| GET | `/workspace/cwd` | Get working directory |
| GET | `/openapi.json` | OpenAPI schema |
| GET | `/favicon.ico` | Favicon (requires `--tray`) |

### Web Console Chat Protocol

`POST /sessions/{session_id}/chat` accepts a list of Chunks and returns an SSE stream:

**Request:**
```json
{
  "chunks": [
    {"type": "text", "text": "Hello!"},
    {"type": "blob", "name": "image.png", "data": "base64..."}
  ]
}
```

**Response (SSE):**
```
data: {"type": "text", "text": "Hello! "}
data: {"type": "blob", "name": "generated.png", "data": "base64..."}
data: [DONE]
```

## Advanced: Telegram / Feishu Bot

### Telegram

```bash
uv run psi-agent channel telegram \
  --session-socket ./channel.sock \
  --bot-token $TELEGRAM_BOT_TOKEN \
  --allowed-user-ids 123456789 \
  --proxy socks5://127.0.0.1:1080
```

- Supports text, photos, and documents
- Streaming output: incremental `edit_text` for a typewriter effect
- User whitelist: `--allowed-user-ids` optional
- SOCKS5 proxy: `--proxy` CLI arg or `PSI_TELEGRAM_PROXY` env var

### Feishu

```bash
uv run psi-agent channel feishu \
  --session-socket ./channel.sock \
  --app-id $FEISHU_APP_ID \
  --app-secret $FEISHU_APP_SECRET \
  --allowed-user-ids "ou_abc123"
```

- WebSocket long connection, SDK background thread + anyio portal bridge
- Card streaming: `stream.append()` updates Feishu cards incrementally
- Processing status emoji: `Typing` while processing, removed on completion, `CrossMark` on failure
- Supports text, images, files, and audio

## Example Workspaces

The `examples/` directory contains several workspaces ranging from minimal to production-grade — from single-tool setups, scheduled tasks, and MCP integration, to multi-tier system prompts, persistent memory, and Telegram/Feishu bots. See the `examples/` directory for details.

## Development

```bash
uv run ruff check .          # lint
uv run ruff format --check . # format
uv run ty check              # types
uv run pytest -v             # tests
```

## Contributors

<a href="https://github.com/genuineknowledge/psi-agent/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=genuineknowledge/psi-agent" />
</a>

## License

MIT License. See [LICENSE](LICENSE.md).
