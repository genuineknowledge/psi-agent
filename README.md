# psi-agent

A microkernel-style agent framework. A workspace defines the agent:
system prompt, tools, skills, schedules, and memory live in ordinary files.

## Requirements

- Python 3.14 or newer
- uv
- A model API key for the backend you choose

## Quick Start

From a cloned checkout:

```bash
uv sync
uv run psi-agent init
uv run psi-agent doctor
uv run psi-agent run --message "Summarize what you can do in one sentence"
```

`psi-agent init` creates:

```text
~/.psi-agent/config.toml
~/.psi-agent/workspaces/default/
```

Set the API key environment variable shown by `psi-agent init`.

macOS or Linux:

```bash
export OPENAI_API_KEY="your-key"
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-key"
```

Use Anthropic defaults instead:

```bash
uv run psi-agent init --ai anthropic-messages
```

## One-Line Setup

The fastest path needs no manual clone. The remote installer installs `uv` if
missing, clones psi-agent into `~/.psi-agent/psi-agent`, syncs dependencies,
then launches the interactive setup wizard.

macOS, Linux, or WSL2:

```bash
curl -fsSL https://raw.githubusercontent.com/genuineknowledge/psi-agent/feat/web-channel-setup-flow/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/genuineknowledge/psi-agent/feat/web-channel-setup-flow/install.ps1 | iex
```

The installer downloads and runs the official `uv` installer from `astral.sh`
when `uv` is not already present, and clones from GitHub. Review the script
first if you prefer. Useful overrides: `PSI_AGENT_BRANCH`, `PSI_AGENT_HOME`,
and `PSI_AGENT_SKIP_SETUP=1` (clone and sync only, skip the wizard).

If you already have a cloned checkout, run the bootstrap script instead. It
skips the clone step and reuses the current directory.

macOS or Linux:

```bash
./bootstrap.sh
```

Windows PowerShell:

```powershell
./bootstrap.ps1
```

The wizard is also available on its own once dependencies are synced:

```bash
uv run psi-agent setup
```

`psi-agent setup` prompts for the AI backend, model, base URL, and API key,
then writes the key directly into `~/.psi-agent/config.toml` (owner-only
permissions on macOS and Linux). It can also configure a Feishu or WeChat
bridge channel and generate the matching gateway `profile.yaml`. The wizard
needs an interactive terminal; for non-interactive setup use `psi-agent init`
and set the API key environment variable.

## Upgrade

From a cloned checkout:

```bash
git pull --ff-only
uv sync
uv run psi-agent doctor
```

If the upgrade fails, keep the existing checkout and run `uv run psi-agent doctor`.
The command reports the next step without printing API key values.

## One-Shot Run

Use this for external callers such as Fusion Flow:

```bash
uv run psi-agent run \
  --workspace examples/a-simple-bash-only-workspace \
  --profile fusion \
  --message "Summarize what you can do in one sentence"
```

If `--workspace` is omitted, `psi-agent run` reads `default_workspace` from
`~/.psi-agent/config.toml`.

Example config:

```toml
config_version = 1
default_profile = "fusion"
default_workspace = "/absolute/path/to/workspace"

[profiles.fusion]
ai = "openai-completions"      # or "anthropic-messages"
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

Prefer `api_key_env` over storing `api_key` directly in the config file.

## Interactive Mode

Three-process mode is useful when you want a persistent session.

Terminal 1:

```bash
uv run psi-agent ai openai-completions \
  --session-socket ./ai.sock \
  --model gpt-4o-mini \
  --api-key "$OPENAI_API_KEY" \
  --base-url https://api.openai.com/v1
```

Terminal 2:

```bash
uv run psi-agent session \
  --workspace examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock
```

Terminal 3:

```bash
uv run psi-agent channel repl --session-socket ./channel.sock
```

REPL controls:

- `Enter`: new line
- `Alt+Enter`: send
- `Ctrl+D`: exit

Validate and normalize a channel link:

```bash
uv run psi-agent channel link --url https://t.me/psi_agent/42
```

Supported link families:

- Telegram: `tg://`, `telegram://`, `t.me`
- WhatsApp: `whatsapp://`, `wa.me`, `chat.whatsapp.com`
- Discord: `discord://`, `discord.com`, `discord.gg`
- Slack: `slack://`, `slack.com`, `app.slack.com`
- REPL session links: `repl://`
- QQ: `qq://`, `mqq://`, `qm.qq.com`, `qun.qq.com`, `bot.q.qq.com`
- WeChat/QClaw: `wechat://`, `weixin://`, `wecom://`, `wechat-bridge://`, `qclaw.qq.com`
- Feishu/Lark: `feishu://`, `lark://`, `feishu.cn`, `larksuite.com`
- DingTalk: `dingtalk://`, `ding://`, `dingtalk.com`, `oapi.dingtalk.com`

REPL means "Read-Eval-Print Loop": an interactive terminal loop, not a full TUI.

## Platform Channels

Channel adapters receive messages from chat platforms, send user text to the
existing psi-agent session socket, then post the model reply back through the
platform API.

Webhook-style channels listen on `http://127.0.0.1:8080/webhook` by default.
Use `--listen` and `--webhook-path` to change the local endpoint.

Examples:

```bash
uv run psi-agent channel telegram --session-socket ./channel.sock --token "$TELEGRAM_BOT_TOKEN"
uv run psi-agent channel whatsapp --session-socket ./channel.sock --token "$WHATSAPP_ACCESS_TOKEN" --phone-number-id "$WHATSAPP_PHONE_NUMBER_ID"
uv run psi-agent channel discord --session-socket ./channel.sock --bot-token "$DISCORD_BOT_TOKEN"
uv run psi-agent channel slack --session-socket ./channel.sock --bot-token "$SLACK_BOT_TOKEN"
uv run psi-agent channel feishu --session-socket ./channel.sock --tenant-access-token "$FEISHU_TENANT_ACCESS_TOKEN"
uv run psi-agent channel dingtalk --session-socket ./channel.sock
```

Gateway/long-poll channels do not expose a local webhook. They connect out to
the platform service:

```bash
uv run psi-agent channel discord --mode gateway --session-socket ./channel.sock --bot-token "$DISCORD_BOT_TOKEN"
uv run psi-agent channel qqbot --session-socket ./channel.sock --app-id "$QQ_APP_ID" --client-secret "$QQ_CLIENT_SECRET"
uv run psi-agent channel weixin-ilink --session-socket ./channel.sock --token "$WEIXIN_TOKEN" --account-id "$WEIXIN_ACCOUNT_ID"
```

Weixin iLink can also login by QR code and save local credentials:

```bash
uv run psi-agent channel weixin-ilink --qr
uv run psi-agent channel weixin-ilink --session-socket ./channel.sock
```

The default state directory is `~/.psi-agent/channels/weixin-ilink`. Use
`--state-dir`, `WEIXIN_STATE_DIR`, or `OPENCLAW_STATE_DIR` to read or write a
different account state directory.

Bridge channels are available for external transport processes that already
normalize incoming QQ/WeChat messages:

```bash
uv run psi-agent channel qq-bridge --session-socket ./channel.sock --reply-url "$QQ_BRIDGE_REPLY_URL"
uv run psi-agent channel wechat-bridge --session-socket ./channel.sock --reply-url "$WECHAT_BRIDGE_REPLY_URL"
```

## Profile Gateway Runtime

`psi-agent gateway` starts a profile-based gateway as one local runtime:
AI backend, session server, schedules, channel adapters, `channel_directory.json`,
and `gateway_state.json` are owned by the profile process. This is the right
entrypoint when multiple software channels should share one agent profile.

Telegram, Slack, WhatsApp, Feishu, DingTalk, QQ bridge, and WeChat bridge use
HTTP webhook entries. Discord can run in webhook relay mode or connect to
Discord's official Gateway. Native QQBot and Weixin iLink currently run as
standalone channel processes.

Example `~/.psi-agent/gateway/profiles/default/profile.yaml`:

```yaml
name: default
workspace: D:/File/FuClaw/psi-agent/examples/a-simple-bash-only-workspace
ai: openai-completions
model: gpt-4o-mini
base_url: https://api.openai.com/v1
api_key_env: OPENAI_API_KEY

channels:
  - name: telegram
    type: telegram
    listen: http://127.0.0.1:8080
    webhook_path: /telegram/webhook
    token: ""
    webhook_secret: ""

  - name: wechat
    type: wechat-bridge
    listen: http://127.0.0.1:8080
    webhook_path: /wechat/webhook
    reply_url: ""
    bridge_secret: ""

  - name: feishu
    type: feishu
    listen: http://127.0.0.1:8080
    webhook_path: /feishu/webhook
    app_id: ""
    app_secret: ""
    verification_token: ""
```

Run it:

```bash
uv run psi-agent gateway --profile default
```

Discord can also run as a Gateway client for real `MESSAGE_CREATE` events:

```bash
uv run psi-agent channel discord \
  --mode gateway \
  --session-socket ./channel.sock \
  --bot-token "$DISCORD_BOT_TOKEN"
```

The Discord bot needs message content intent enabled in the Discord Developer
Portal and the matching gateway intents in `--gateway-intents`.

Native QQBot uses QQ Bot OpenAPI v2 credentials, fetches the official Gateway
URL, receives `C2C_MESSAGE_CREATE` and `GROUP_AT_MESSAGE_CREATE`, then replies
through `/v2/users/{openid}/messages` or `/v2/groups/{group_openid}/messages`.

Weixin iLink uses Tencent iLink Bot API long polling. It supports QR login via
`get_bot_qrcode` / `get_qrcode_status`, saves `bot_token` and `ilink_bot_id`
locally, and can still accept explicit `WEIXIN_TOKEN` and `WEIXIN_ACCOUNT_ID`.
Media delivery is not implemented yet.

The WeChat bridge channel is for an external normalized bridge process. The
bridge should POST normalized messages to psi-agent:

```json
{
  "type": "message",
  "message": {
    "conversation_id": "room-1",
    "user_id": "user-1",
    "message_id": "msg-1",
    "text": "hello"
  },
  "reply_url": "https://bridge.example/reply"
}
```

psi-agent replies to `reply_url` or `--reply-url` with:

```json
{
  "conversation_id": "room-1",
  "user_id": "user-1",
  "text": "reply",
  "in_reply_to": "msg-1"
}
```

Set `WECHAT_BRIDGE_SECRET` or pass `--bridge-secret` to require
`Authorization: Bearer <secret>` or `X-WeChat-Bridge-Secret: <secret>` on
incoming bridge requests.

The QQ bridge channel uses the same normalized bridge contract for an external
QQ bridge process. Use `QQ_BRIDGE_REPLY_URL` and `QQ_BRIDGE_SECRET` for the
bridge reply endpoint and optional shared secret.

The Feishu channel accepts Events API message events such as
`im.message.receive_v1` and replies through
`/open-apis/im/v1/messages/{message_id}/reply`. Use
`--api-base-url https://open.larksuite.com` for Lark tenants.

The DingTalk channel targets outgoing robots. It reads incoming text messages
and replies through the per-message `sessionWebhook`; `--session-webhook` is
only needed as a fallback when the incoming payload does not include one.

Real platform smoke tests are disabled by default because they send messages
through real platform APIs. Enable them only with test accounts:

```bash
PSI_RUN_REAL_CHANNEL_TESTS=1 uv run pytest tests/integration/test_real_channels.py -v
```

Required variables by test:

- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_TEST_CHAT_ID`
- WhatsApp: `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_TEST_RECIPIENT`
- Slack: `SLACK_BOT_TOKEN`, `SLACK_TEST_CHANNEL_ID`
- Discord relay: `DISCORD_BOT_TOKEN`, `DISCORD_TEST_CHANNEL_ID`
- Discord Gateway manual inbound: also set `PSI_RUN_REAL_DISCORD_GATEWAY_TESTS=1` and `DISCORD_GATEWAY_TEST_TEXT`
- QQBot Gateway credential smoke: `QQ_APP_ID`, `QQ_CLIENT_SECRET`
- QQBot Gateway manual inbound: also set `PSI_RUN_REAL_QQBOT_GATEWAY_TESTS=1` and `QQ_GATEWAY_TEST_TEXT`
- Weixin iLink getupdates smoke: `WEIXIN_TOKEN`, `WEIXIN_ACCOUNT_ID`
- Weixin iLink manual inbound: also set `PSI_RUN_REAL_WEIXIN_ILINK_TESTS=1` and `WEIXIN_ILINK_TEST_TEXT`

## Fusion Flow Workspace

`examples/fusion-flow-workspace` packages Fusion Flow as a psi-agent skill.
Use it when a natural-language request should create or run a multi-agent
`.flow.ts` workflow.

```bash
uv run psi-agent run \
  --workspace examples/fusion-flow-workspace \
  --profile fusion \
  --message "Build a parallel code-review workflow with security, performance, and readability reviewers."
```

Fusion Flow files are separated by responsibility:

```text
skills/fusion-flow/                 # immutable skill bundle and runtime
flows/<task-slug>/<task-slug>.flow.ts
flows/<task-slug>/runs/<run-id>/     # meta.json, execution-graph.json, bindings/, trace/
```

For `FLOW_ENGINE=psi`, keep provider URLs and API keys in psi-agent profile
config, not in Fusion Flow `.env` files.

## Workspace Layout

```text
my-workspace/
|-- systems/
|   `-- system.py          # async def system_prompt_builder() -> str
|-- tools/
|   `-- bash.py            # one async function per tool file
|-- skills/
|   `-- example/SKILL.md   # skill instructions loaded by the workspace prompt
|-- schedules/
|   `-- daily/TASK.md      # optional cron task
`-- memory.md              # optional workspace memory
```

Tool example:

```python
import anyio


async def bash(command: str) -> str:
    """Execute a shell command."""
    result = await anyio.run_process(["bash", "-lc", command])
    return result.stdout.decode().strip()
```

## Troubleshooting

Run:

```bash
uv run psi-agent doctor
```

Common messages:

```text
API key: missing; set OPENAI_API_KEY
```

Set the environment variable named in the output.

```text
Workspace not found
```

Run `uv run psi-agent init`, or pass `--workspace PATH`.

```text
psi-agent config is not valid TOML
```

Check quotes and `[profiles.<name>]` table headers in `~/.psi-agent/config.toml`.

```text
Cannot connect to the model service
```

Check `base_url`, network access, and whether the provider is reachable.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest -v
```

Real API tests are disabled by default:

```bash
PSI_RUN_REAL_API_TESTS=1 uv run pytest tests/integration/test_real_api.py -v
```

## License

GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE.md).
