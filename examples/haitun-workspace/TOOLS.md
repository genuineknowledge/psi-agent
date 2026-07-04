# TOOLS.md — Local Notes & MCP Configuration

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to
your setup. It is usage guidance, not availability.

## MCP Server Configuration

Each MCP server reads its config from a `MCP_<PREFIX>_CONFIG` environment variable (JSON format).
Set these in your shell profile or a `.env` file.

### PW — Playwright Browser Automation

Requires Node.js and `@playwright/mcp`:

```bash
# Install once:
npm install @playwright/mcp
npx playwright install chromium

# Configure:
# On Windows (PowerShell):
$env:MCP_PW_CONFIG='{"command":"npx","args":["@playwright/mcp"]}'
# On Linux/macOS (bash):
export MCP_PW_CONFIG='{"command":"npx","args":["@playwright/mcp"]}'
```

### FEISHU — Feishu/Lark Messaging (MCP mode)

Requires Python with `mcp` and `aiohttp` packages:

```bash
# Webhook mode (simplest) — for feishu_send_text native tool:
export FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxx"

# Full API mode — for MCP-based cards, image upload, file sending:
export MCP_FEISHU_CONFIG='{"command":"python","args":["mcp_servers/feishu_server.py"]}'
export FEISHU_APP_ID="cli_xxxxxxxxx"
export FEISHU_APP_SECRET="xxxxxxxxx"
```

### MEDIA — Image Generation, Vision, TTS, STT

Requires Python with `mcp` and `aiohttp` packages, plus an OpenAI-compatible API key:

```bash
export MCP_MEDIA_CONFIG='{"command":"python","args":["mcp_servers/media_server.py"]}'
export OPENAI_API_KEY="sk-xxxxxxxxx"
# Optional: use a compatible API (DeepSeek, etc.)
export OPENAI_BASE_URL="https://api.deepseek.com/v1"
# Optional: override default models
export MEDIA_IMAGE_MODEL="dall-e-3"
export MEDIA_VISION_MODEL="gpt-4o"
export MEDIA_TTS_MODEL="tts-1-hd"
export MEDIA_STT_MODEL="whisper-1"
```

## What Else Goes Here

Things like:

- SSH hosts and aliases
- API providers / base URLs you commonly use (never the keys themselves)
- Device nicknames, paths, or directories you reach for often
- Anything environment-specific

## Examples

```markdown
### SSH
- home-server → 192.168.1.100, user: admin

### Common paths
- notes → ~/notes
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without
losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.
