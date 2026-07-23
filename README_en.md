# psi-agent

> [中文](README.md)

A microkernel framework for assembling AI agents from composable socket-connected components.

You write Python functions and Markdown. The framework handles socket communication, tool calling, SSE streaming, cron scheduling, and conversation persistence, and provides a Web management console.

## Why

- **Simple composition**: ai, session, channel — three standalone processes connected through sockets. No global config, no database
- **Workspace is the agent**: Drop Python functions in `workspace/` for tools, write a system prompt for personality, add a cron for scheduled tasks
- **Streaming interactions**: REPL/CLI show AI reasoning (dimmed) and final response in real time
- **One-command launch**: `psi-agent run config.yml` starts AI + Session + Channel from a single YAML
- **Web management console**: `psi-agent gateway` starts a REST API + Web Console SPA for visual management

## Architecture

```
User ←→ Channel (REPL/CLI/Telegram/Feishu) ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
User ←→ Web UI → HTTP → Gateway ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
```

AI layer is stateless. Session maintains conversation history. Channel is a pure UI client — the three core components are independent processes communicating via the OpenAI Chat Completions HTTP/SSE protocol over sockets. Gateway provides an HTTP bridge for the Web UI, internally reusing Channel sockets to communicate with Session.

For developers: start components independently, mix and match for debugging and customization. For users: `psi-agent run config.yml` launches everything in one command, and `psi-agent gateway` provides a visual web console for managing everything.

## Quick Start

**Requires Python >= 3.14**

### Option 1: Start individually

Three terminals, three commands:

```bash
# Install
uv sync

# Terminal 1: Start AI backend (--provider/--model/--api-key/--base-url are optional, fall back to PSI_AI_* env vars)
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

REPL controls: `Enter` to insert a newline, `Alt+Enter` (or `Escape+Enter`) to send, `Ctrl+D` to exit.

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
  api_key: sk-xxxx                 # AI params are optional: omit to fall back to PSI_AI_* env vars
  base_url: https://api.openai.com/v1

- type: session
  workspace: ./examples/a-simple-bash-only-workspace  # optional, defaults to .
  session_id: mychat         # optional, UUID auto-generated
  channel_socket: ./channel.sock
  ai_socket: ./ai.sock

- type: channel
  name: repl                        # cli / repl / telegram / feishu
  session_socket: ./channel.sock
```

Servers (ai, session) run forever; channel components exit when done — CLI exits after the response, REPL runs until Ctrl+D.

> **Note**: `psi-agent run` does not exit automatically (ai/session run forever) — use `Ctrl+C` to stop. Batch mode always enables DEBUG-level logging; per-component `verbose` fields in the YAML are ignored.

### Option 3: Gateway Web Console

Start the Gateway and manage everything in your browser:

```bash
uv run psi-agent gateway                         # Random port on 127.0.0.1 (default)
uv run psi-agent gateway --listen http://127.0.0.1:8080   # Specify a listen address
```

Open the printed address to see a Material Design 3 Web Console. From the UI you can:

- **Connect LLMs**: Choose from 50+ providers, enter your API key
- **Create sessions**: Pick a workspace directory, start a Session
- **Chat**: Markdown + LaTeX rendering, file upload/download, streaming output
- **Manage**: Sidebar session switching, double-click rename, delete with confirmation
- **Automatic titles**: AI generates session titles after first conversation

The `--listen` value must include the `http://` prefix; bare `IP:PORT` is interpreted as a Unix socket path.

Gateway also supports system tray icon (`--tray --icon icon.png`), auto browser open (`--browser`), native webview window (`--webview`), and custom socket path prefix (`--socket-path psi`, controlling the `/tmp/{prefix}/ais/...` and `/tmp/{prefix}/channels/...` layout for AI/Session Unix sockets).

### Option 4: Run with Nix

With Nix installed (flakes enabled), no need to set up Python, node, or dependencies by hand:

```bash
nix run github:genuineknowledge/psi-agent -- gateway --listen http://127.0.0.1:8080
```

The flake is headless (no native `--webview`/`--tray` window features); the Web Console still works in a regular browser. The runtime tools it needs — `uv`, `node`/`npx`, `bash`, `git` — are baked into the executable's PATH, so workspace tools/skills that spawn subprocesses can find them.

Flake outputs:

- `packages.default` (= `psi-agent`): executable with runtime tools on PATH, the `nix run` entry point
- `packages.psi-agent-unwrapped`: the plain Python environment without PATH injection
- `packages.psi-agent-spa`: the Vue frontend built on its own
- `devShells.default`: development environment (see "Development")

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
| `./ai.sock` (bare filesystem path, relative or absolute) | Unix socket |
| `http://127.0.0.1:8080` | TCP |
| `\\.\pipe\name` (Windows) | Named Pipe |

AI and Session components are transport-agnostic — handled uniformly by `_sockets.py`.

Protocol errors between components take two forms:

- **Non-streaming (HTTP level)**: request parsing failures return HTTP error status with a JSON body:
  ```json
  {"error": {"message": "...", "type": "...", "param": null, "code": 400}}
  ```
- **Streaming (SSE level)**: errors after HTTP 200 is committed are returned as ChatCompletionChunk (`[DONE]` varies by layer, see below):
  ```
  data: {"id": "error", "choices": [{"index": 0, "delta": {"content": "[Upstream Error]: ..."}, "finish_reason": "error"}]}
  ```
  `finish_reason="error"` is a psi-agent internal extension, not exposed externally. `[DONE]` is always sent by Gateway; the Session layer sends it only on success; the AI layer never sends `[DONE]` (stream end is indicated by response completion).

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

CLI args take precedence over environment variables. AI params (provider, model, api_key, base_url) and channel auth params are optional and fall back to env vars when omitted. Socket path params (--session-socket, --channel-socket, --ai-socket) are required.

## Define Your Own Agent

A workspace is an agent:

```
my-workspace/
├── tools/                    # Each non-_-prefixed .py file — all non-_ async def in it
│   └── bash.py               # async def bash(command: str) -> str
├── skills/                   # */SKILL.md skill docs (enumerated by system_prompt_builder)
├── schedules/                # Cron-triggered tasks
│   └── daily-report/
│       └── TASK.md           # YAML header (name, cron) + Markdown body
└── systems/
    └── system.py             # async def system_prompt_builder() / system_prompt_rebuild_checker()
```

### Tools

A tool is just an async function — in each non-`_`-prefixed `.py` file, every non-`_` `async def` is loaded as a tool:

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
- Each schedule is loaded independently — IO errors, YAML parsing issues, or cron validation failures only skip that schedule
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

- `--session-id` auto-generates a UUID when omitted; provide a custom ID to resume a session (only `[a-zA-Z0-9_-]` allowed)
- JSONL line-by-line format, atomic writes (temp file + `anyio.Path.replace`)
- **Turn-level atomicity**: user message is persisted immediately as a crash baseline; AI responses are persisted only on successful completion, skipped on error

```bash
# New session
uv run psi-agent session --session-id mychat --channel-socket ./channel.sock --ai-socket ./ai.sock

# Resume later
uv run psi-agent session --session-id mychat --channel-socket ./channel.sock --ai-socket ./ai.sock
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
| POST | `/feishu/route` | Idempotently route a Feishu open_id to its dedicated Session (spawn on first use) |
| GET | `/feishu/routes` | List Feishu open_id → Session routes |
| GET | `/titles` | Get all session titles |
| POST | `/titles` | Set session title |
| POST | `/titles/generate` | AI auto-generate title |
| GET | `/workspace/browse` | Browse directory (`?path=...`) |
| GET | `/workspace/cwd` | Get working directory |
| GET | `/openapi.json` | OpenAPI schema |
| GET | `/favicon.ico` | Favicon (available only with `--icon`; returns 404 otherwise) |

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
data: {"type": "reasoning", "text": "Let me think..."}
data: {"type": "text", "text": "Hello! "}
data: {"type": "blob", "name": "generated.png", "data": "base64..."}
data: {"type": "error", "error": "..."}
data: [DONE]
```

## Advanced: Telegram / Feishu Bot

### Telegram

```bash
uv run psi-agent channel telegram \
  --session-socket ./channel.sock \
  --bot-token $PSI_TELEGRAM_BOT_TOKEN \
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
  --app-id $PSI_FEISHU_APP_ID \
  --app-secret $PSI_FEISHU_APP_SECRET \
  --allowed-user-ids "ou_abc123"
```

- WebSocket long connection, SDK background thread + anyio portal bridge
- Card streaming: `stream.append()` updates Feishu cards incrementally
- Processing status emoji: `Typing` while processing, removed on completion, `CrossMark` on failure
- Supports text, images, files, and audio
- Doc comment replies: `--respond-to-comments` (on by default) — when the bot is @-mentioned in a document comment, reply to that comment with the agent's answer (requires subscribing to `drive.notice.comment_add_v1` in the Feishu console)

## Example Workspaces

The `examples/` directory contains several workspaces ranging from minimal to production-grade — from single-tool setups, scheduled tasks, and MCP integration, to multi-tier system prompts, persistent memory, and Telegram/Feishu bots. See the `examples/` directory for details.

## Development

```bash
uv run ruff check .          # lint
uv run ruff format --check . # format
uv run ty check              # types
uv run pytest -v             # tests
```

With Nix, `nix develop` drops you into a dev shell with Python 3.14, `uv`, and `node`, after which the `uv run ...` commands above work as usual.

## Contributors

<a href="https://github.com/genuineknowledge/psi-agent/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=genuineknowledge/psi-agent" />
</a>

## License

MIT License. See [LICENSE](LICENSE.md).
