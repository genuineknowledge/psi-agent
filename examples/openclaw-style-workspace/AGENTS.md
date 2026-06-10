# openclaw-style-workspace

This workspace demonstrates the OpenClaw-style system prompt mechanism implemented
in psi-agent.

## Architecture

The system prompt follows OpenClaw's `buildAgentSystemPrompt()` structure:

- **Stable prefix** (cached across turns): identity, tooling, guidance sections,
  skills, context files, workspace, sandbox info
- **Cache boundary**: `<!-- OPENCLAW_CACHE_BOUNDARY -->`
- **Dynamic suffix** (rebuilt each turn): channel guidance, heartbeats, silent
  replies, memory, user profile, datetime, runtime info

## Configuration

The following environment variables control the dynamic sections:

| Variable | Purpose |
|---|---|
| `OPENCLAW_CHANNEL` | Messaging platform (telegram/slack/discord/feishu/teams) |
| `OPENCLAW_PLATFORM` | Platform hint (telegram/slack/ios/macos/web) |
| `OPENCLAW_MODEL` | Override model name shown in runtime section |
| `OPENCLAW_AGENT_ID` | Agent ID shown in runtime section |
| `OPENCLAW_TIMEZONE` | User timezone (default: UTC) |
| `OPENCLAW_TTS_ENABLED` | Set to `1` to inject TTS guidance |
| `OPENCLAW_PROVIDER_HINT` | Provider-specific prompt contribution |
| `OPENCLAW_MODEL_ALIASES` | JSON map of model aliases, e.g. `{"fast":"claude-haiku-4-5"}` |
| `OPENCLAW_AUTHORIZED_SENDERS` | Comma-separated list of authorized sender IDs |

## Persistent files

| File | Purpose |
|---|---|
| `memory.md` | Agent's long-term memory (volatile, injected each turn) |
| `~/.openclaw/SOUL.md` | Agent identity (replaces default identity line) |
| `~/.openclaw/USER.md` | User profile (injected into dynamic suffix) |

## Mock sections

The following sections are implemented as mocks. Wire them up to enable real behaviour:

- **Sub-agent delegation** — implement ACP / sessions_spawn tool
- **MCP tools** — set `MCP_SERVER_URL` and implement mcp_call tool
- **LSP tools** — set `LSP_SERVER_CMD` and implement lsp_query tool
- **TTS/Voice** — set `TTS_API_KEY` and implement tts_speak tool
- **Image generation** — set `IMAGE_GEN_API_KEY` and implement image_gen tool
- **Web search** — set `WEB_SEARCH_API_KEY` in web_search.py

## Skills

Add skills in `skills/<name>/SKILL.md`. The system scans them and builds an
`<available_skills>` XML block in the stable prefix. A snapshot cache
(`.skills_prompt_snapshot.json`) prevents re-parsing unchanged files.

## Smoke test

```bash
cd examples/openclaw-style-workspace
python systems/system.py
```
