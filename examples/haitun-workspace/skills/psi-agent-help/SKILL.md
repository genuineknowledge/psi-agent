---
name: psi-agent-help
description: Configure, use, and understand psi-agent, including first-run onboarding, workspace structure, tools, skills, and common starter workflows.
category: agent
---

# psi-agent Help

Use this skill when the user asks for help, new-user guidance, onboarding, what you can do,
how to use this workspace, available tools, available skills, or common task paths.

Reply in Chinese unless the user clearly uses another language. Keep the answer practical:
show the user what they can do next, not just abstract architecture.

## Quick Orientation For This Workspace

This workspace is `haitun-workspace`, a self-contained psi-agent workspace. The agent's
configuration, tools, skills, schedules, prompt builder, and local notes all live inside the
workspace directory.

Important files and directories:

| Path | Purpose |
|------|---------|
| `AGENTS.md` | Workspace overview and operating notes. |
| `BOOTSTRAP.md` | First-run onboarding. While present, the agent should introduce the workspace before normal work. |
| `TOOLS.md` | Local setup and usage notes. It is guidance, not the actual tool registry. |
| `USER.md` | User profile and preferences, if the user chooses to save them. |
| `SOUL.md` | Persona and durable working style notes. |
| `IDENTITY.md` | Haitun identity details. |
| `HEARTBEAT.md` | Dynamic context re-read during prompt rebuilds. |
| `tools/` | Callable Python tools exposed to the agent. |
| `skills/` | Reusable task instructions. Each skill lives in a subdirectory with `SKILL.md`. |
| `flows/` | Fusion Flow workflow assets. |
| `schedules/` | Scheduled tasks, each with a `TASK.md`. |
| `systems/` | Prompt builder, prompt section constants, and future extension hooks. |

## Available Tool Groups

Explain tools by capability, because exact runtime availability may vary by session:

- File operations: `read`, `write`, `edit`
- Shell execution: `bash`, `powershell`
- Skill management: `skill_manage`
- Fusion Flow management: `flow_manage`
- Durable memory, if enabled: `memory_add`, `memory_search`, `memory_answer_context`
- Spreadsheet creation: `write_excel`
- Web/search tools, if configured in the current runtime

When helping a user, prefer saying what the tool group is good for and then offer a concrete
command-style request they can type.

## Skills

Skills are reusable instructions stored under `skills/<skill-name>/SKILL.md`. When a user task
matches a skill, read the skill file first and follow it.

Common useful skills in this workspace include:

- `psi-agent-help`: this help and onboarding guide.
- `code-review-checklist`: structured code review.
- `git-workflow`: safe git branching, commits, PRs, and conflict resolution.
- `python-async-basics`: Python async guidance.
- `python-static-analysis`: Python static analysis.
- `user-preferences-and-language`: user preference and language handling.
- `fusion-flow`: authoring and managing Fusion Flow workflows.
- `fusion-memory-setup`: setting up durable Fusion Memory.
- Domain skills for systems, data/text processing, ML, media, circuits, cryptanalysis, and other specialized work.

If the user asks for all skills, list the `skills/` directory before answering so the list is current.

## Common Starter Paths

Offer these examples when the user asks how to begin:

- `介绍这个工作区`: summarize the workspace structure and how to use it.
- `列出可用工具和技能`: inspect current tools and skills, then explain them.
- `帮我读这个文件并总结`: use file tools to inspect a file and produce a summary.
- `帮我检查这段代码`: use code-review guidance and relevant language skills.
- `帮我创建一个新技能`: use `skill_manage` and the local skill format.
- `帮我写一个 Fusion Flow`: use the `fusion-flow` skill and `flow_manage`.
- `帮我配置长期记忆`: read `fusion-memory-setup` and walk through setup.
- `帮我把表格生成 Excel`: use `write_excel`, not a markdown table.
- `新手指导`: provide this onboarding flow again.

## Starting The Agent

A typical local startup sequence has three components: AI provider, session, and channel.
Use the repository's current README or command help as the source of truth if commands have changed.

Example shape:

```bash
uv run psi-agent ai openai-completions \
  --session-socket ./ai.sock \
  --model <model> \
  --api-key <key> \
  --base-url <base-url>

uv run psi-agent session \
  --workspace ./examples/haitun-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock

uv run psi-agent channel repl \
  --session-socket ./channel.sock
```

Never ask the user to paste secrets into chat unless they explicitly choose to. Prefer environment
variables or local config files for credentials.

## Adding Tools

Create `tools/<name>.py` with an async `tool(...)` function and a clear docstring. Tool files are
part of the workspace and can be hot-reloaded by the session on user messages.

```python
import anyio

async def tool(file_path: str) -> str:
    """Read a file.

    Args:
        file_path: Path to the file.

    Returns:
        File content as a string.
    """
    return await anyio.Path(file_path).read_text()
```

## Adding Skills

Create `skills/<skill-name>/SKILL.md` with YAML frontmatter:

```markdown
---
name: my-skill
description: What this skill does and when to use it.
category: my-category
---

Skill instructions here.
```

Keep skill descriptions specific so the agent can decide when to read them.

## Adding Scheduled Tasks

Create `schedules/<task-name>/TASK.md` with frontmatter and task instructions:

```markdown
---
name: daily-summary
description: Generate a daily summary
cron: "0 9 * * *"
---

Task instructions here.
```

## Configuring The System Prompt

The main prompt code is in `systems/system.py`, with reusable sections in
`systems/prompt_sections.py`.

Normal customization should usually happen in workspace markdown files first:

- Use `BOOTSTRAP.md` for first-run onboarding.
- Use `AGENTS.md` for workspace-wide operating notes.
- Use `TOOLS.md` for local setup notes.
- Use `USER.md` and `SOUL.md` for user preferences and durable working style.
- Use skills for reusable task procedures.

Edit Python prompt code only when the trigger logic or prompt assembly itself needs to change.

## Hot Reload Notes

The session can reload many workspace assets on user messages:

- `tools/*.py`: tool registry updates.
- `skills/*/SKILL.md`: skill index and skill content updates.
- `schedules/*/TASK.md`: scheduler updates.

`systems/system.py` may require restarting the session after edits.

## Onboarding Response Pattern

When the user asks for help or new-user guidance, use this shape:

1. Start with a one-sentence orientation: this is `haitun-workspace`, a self-contained psi-agent workspace.
2. Show the main structure in 4-6 bullets.
3. Summarize tool groups and skill usage.
4. Give concrete starter phrases the user can type.
5. Offer one immediate next step, such as listing current skills, inspecting a file, creating a skill, or writing a flow.

Do not overwhelm the user with every implementation detail unless they ask for depth.