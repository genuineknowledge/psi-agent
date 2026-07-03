---
name: psi-agent-help
description: Configure, use, and understand psi-agent — the portable, component-based AI agent framework.
category: agent
---

# psi-agent Help

psi-agent is an open-source AI agent framework built around two core principles: **portability** and **composability**. An agent is fully defined by its workspace directory — copy the directory to move the agent anywhere.

## Architecture

psi-agent is made of four component types that communicate over Unix sockets via HTTP:

```
psi-channel-* → psi-session → psi-ai-*
                    ↑
                workspace/
```

| Component | Role |
|-----------|------|
| `psi-ai-<provider>` | LLM provider adapter (OpenAI, Anthropic, etc.) |
| `psi-session` | Core agent loop — tool calling, history, hot-reload |
| `psi-channel-<platform>` | Message channel (REPL, Telegram, Feishu, CLI, etc.) |
| `psi-workspace-*` | Workspace pack/mount/snapshot tools |

## Starting the Agent

```bash
# 1. Start the AI component (e.g. via OpenRouter)
uv run psi-agent ai openai-completions \
  --session-socket ./ai.sock \
  --model tencent/hy3-preview:free \
  --api-key sk-or-v1-xxxxx \
  --base-url https://openrouter.ai/api/v1

# 2. Start the session
uv run psi-agent session \
  --workspace ./workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock

# 3. Start a channel (REPL for local use)
uv run psi-agent channel repl \
  --session-socket ./channel.sock
```

## Workspace Structure

```
workspace/
├── tools/        # Python files, each defines async def tool(...)
├── skills/       # Subdirs with SKILL.md — loaded into system prompt index
├── schedules/    # Subdirs with TASK.md — cron-scheduled tasks
└── systems/
    └── system.py # System class: build_system_prompt(), compact_history()
```

## Adding Tools

Create `workspace/tools/<name>.py` with an `async def tool(...)` function:

```python
import anyio

async def tool(file_path: str) -> str:
    """Read a file.

    Args:
        file_path: Path to the file.

    Returns:
        File content as string.
    """
    return await anyio.Path(file_path).read_text()
```

Tools are hot-reloaded — changes take effect on the next user message, no restart needed.

## Adding Skills

Create `workspace/skills/<skill-name>/SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
description: What this skill does (shown in the skills index).
category: my-category
---

Skill instructions here...
```

Skills are indexed in the system prompt. The LLM reads the full SKILL.md on demand using the `read` tool.

## Adding Scheduled Tasks

Create `workspace/schedules/<task-name>/TASK.md`:

```markdown
---
name: daily-summary
description: Generate a daily summary
cron: "0 9 * * *"
---

Task instructions here...
```

## Configuring the System Prompt

Edit `workspace/systems/system.py`. The `System` class has two required methods:

- `async def build_system_prompt(model=None) -> str` — builds the three-tier prompt (stable / context / volatile)
- `async def compact_history(history, complete_fn, max_tokens) -> list` — compresses long conversation history

The `hermes-style-workspace` example in `examples/hermes-style-workspace/` is the recommended starting point.

## Hot Reload

psi-session watches for file changes on every user message:
- `tools/*.py` — reloads the tool registry
- `skills/*/SKILL.md` — rebuilds the system prompt
- `schedules/*/TASK.md` — updates the scheduler

`systems/system.py` does **not** hot-reload — restart the session after editing it.

## Workspace Portability

The workspace is self-contained. To move an agent:

```bash
cp -r ./workspace /new/location/workspace
```

To package it as a squashfs image:

```bash
sudo psi-agent workspace pack --input ./workspace --output ./workspace.squashfs --tag v1.0
```
