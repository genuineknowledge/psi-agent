# haitun-workspace (海豚 / haitun agent 🐬)

A consolidated psi-agent workspace. Its persona is fixed: a **haitun agent** (always stated
in the system prompt). It merges the most useful parts of the other example workspaces:

- **Prompt engine** — a layered builder (stable prefix + cache boundary + dynamic
  suffix, skills index, bootstrap context files), with **all configuration kept inside this
  workspace** (there is no global config directory).
- **Fusion Flow** — full workflow-authoring capability (`flow_manage`, the bundled node
  runtime under `skills/fusion-flow/`, the `bin/` stateful-session shim, the `flows/`
  layout, and authoring guidance injected into the prompt).
- **Skills + file tools** — the full hermes-skills domain skill set plus selected curated
  skills, on top of clean async file/shell tools.

## No global config

**Nothing is read from `~/` — there is no global config directory.** The agent's identity,
user profile, and bootstrap files all live at the workspace root:

| File | Role |
|---|---|
| `SOUL.md` | Personality/values; augments the built-in haitun identity (top of prompt). |
| `USER.md` | User profile; injected into the dynamic suffix (below the cache boundary). |
| `IDENTITY.md` | Haitun identity details; loaded as a bootstrap context file. |
| `TOOLS.md` | Local, environment-specific notes; bootstrap context file. |
| `BOOTSTRAP.md` | First-run onboarding. **Delete it** to skip onboarding. Triggers the "Bootstrap Pending" section while present. |
| `HEARTBEAT.md` | Dynamic context, re-read every turn (below the cache boundary). |
| `AGENTS.md` | This file; also loaded as a bootstrap context file. |

## Environment variables (optional)

All are optional and only affect the dynamic suffix / runtime line:

| Variable | Purpose |
|---|---|
| `HAITUN_MODEL` | Override the model name shown in the runtime line. |
| `HAITUN_AGENT_ID` | Agent ID shown in the runtime line. |
| `HAITUN_CHANNEL` | Channel name shown in the runtime line. |
| `HAITUN_TIMEZONE` | Time zone for the date/time section (default `UTC`). |

## Tools (`tools/`)

| Tool | Notes |
|---|---|
| `bash` | Shell commands (anyio, Windows-aware bash detection). |
| `powershell` | Windows-native shell. |
| `read` / `write` / `edit` | Async file ops. |
| `skill_manage` | CRUD on `skills/<name>/SKILL.md` (agent-created skills are mutable). |
| `flow_manage` | CRUD + promote on Fusion Flow assets under `flows/`. |
| `search` (`search.py` + `_mcp.py`) | Serper web search via MCP. Requires the `mcp` extra and `uvx serper-mcp-server`; tools surface as `serper_*`. |

## Skills (`skills/`)

- `_universal` — always-relevant working discipline.
- The hermes domain skill set (cryptanalysis, image-segmentation, ml-inference, …).
- Selected curated skills (`psi-agent-help`, `code-review-checklist`, `python-async-basics`,
  `python-static-analysis`, `user-preferences-and-language`, `example-skill`).
- `fusion-flow` — the immutable Fusion Flow runtime skill (node-based). **Do not edit it.**

## Schedules (`schedules/`)

- `heartbeat` — every 30 min; the agent replies `HEARTBEAT_OK` when nothing needs attention.

## Prerequisites

- **Fusion Flow**: Node.js / `npm` / `npx`. First use: `cd skills/fusion-flow && npm install`.
- **Serper search**: install psi-agent with the `mcp` extra and have `uvx` available.

## ⚠️ Intentionally-kept un-wired code (future extension)

psi-agent's session loader only ever calls `system_prompt_builder()` (and an optional
`system_prompt_rebuild_checker()`), loads `tools/*.py`, and runs `schedules/*/TASK.md`. The
following are deliberately included as **future-extension hooks** and are **NOT** invoked by
the current framework — do not "clean them up" as dead code:

- `systems/system.py`: `System.compact_history()`, `System.after_turn()`, and the
  `_run_self_evolution_review` / self-evolution helpers.
- `systems/curator.py`, `systems/background_review.py`, `systems/threat_patterns.py`,
  `systems/prompt_constants.py` — standalone modules from the hermes-style design, kept for
  when matching hooks are wired into the framework. They are not imported by `system.py`.

## Smoke test

```bash
uv run python examples/haitun-workspace/systems/system.py   # prints the assembled prompt
```
