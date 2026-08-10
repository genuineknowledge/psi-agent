---
name: fusion-memory-setup
description: Use when configuring or diagnosing the operator-managed Fusion Memory MCP connection.
---

# Fusion Memory Remote MCP Setup

Haitun is a consumer of an operator-provisioned remote Fusion Memory service.
It must use the service's MCP Streamable HTTP endpoint; it must not install,
start, configure, or create tokens for a local memory service.

## Required Configuration

For multi-user Feishu deployments, the process starter sets these values before
starting Haitun. The token-map path is optional when automatic registration is enabled:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_AUTO_REGISTER_FEISHU=1
export FUSION_MEMORY_ORGANIZATION_ID="<organization>"
export FUSION_MEMORY_FEISHU_ORGANIZATION_CHAT_ID="<organization-chat>"
# Optional override; default: {AppData}/fusion-memory/tokens.json
export FUSION_MEMORY_TOKEN_MAP_FILE="/absolute/path/to/memory_tokens.json"
```

- `FUSION_MEMORY_MCP_URL` is the remote MCP Streamable HTTP endpoint. TLS is
  terminated by the service's reverse proxy.
- `FUSION_MEMORY_TOKEN_MAP_FILE` optionally points to an operator-owned JSON object keyed
  by Feishu `open_id`. Each entry requires a non-empty `token`; `workspace_id`
  is optional provenance and defaults to `haitun` when empty or omitted. Keep
  the file outside the workspace and source control. If omitted while automatic
  registration is enabled, Haitun creates `{AppData}/fusion-memory/tokens.json`
  with mode `0600`.
- `FUSION_MEMORY_ORGANIZATION_ID` and
  `FUSION_MEMORY_FEISHU_ORGANIZATION_CHAT_ID` bind shared memory to the
  configured organization and its current Feishu group roster. Both are
  required whenever organization memory is enabled; the model cannot choose
  either value.

The Gateway/Session process must also receive `PSI_FEISHU_APP_ID` and
`PSI_FEISHU_APP_SECRET` for the same Feishu application used by its channel.
The bot must belong to the configured organization group and the application
must have `im:chat` permission so the runtime can page through the full member
roster. Configure the Memory service with the matching
`FUSION_MEMORY_FEISHU_APP_ID`, `FUSION_MEMORY_FEISHU_APP_SECRET`, and
`FUSION_MEMORY_FEISHU_ORGANIZATION_CHAT_ID`; a mismatch is rejected rather than
falling back to cached or model-provided identity.

Map membership enables durable memory. On a mapped user's first message after
startup, Haitun initiates authenticated `memory_health` and starts passive
history persistence for the trusted `feishu-<open_id>` Session. The bearer
token, not model-visible `<feishu_context>`, determines user identity. The same
token shares memory across Sessions; different tokens remain isolated.

When automatic registration is enabled, a missing private-chat user is added
to the map on first use. Otherwise, users absent from the map can continue
chatting but receive no bearer token, connector, passive writer, checkpoint, or
durable memory. In map mode there is no fallback to `FUSION_MEMORY_TOKEN`;
duplicate token assignments reject the map, and removing an entry stops that
Session's watcher and client. When neither the map variable nor automatic
registration is configured, the legacy single-user token/workspace/session
variables remain compatible.

Passive persistence accepts only completed ordinary chat turns and skips
unchanged history files. Schedule, heartbeat, compaction, tool-only, and
incomplete rows are excluded.
Validated maps are cached by file signature. Each active turn renews a
five-minute watcher lease; idle resources are reclaimed and restart on the
next message.

## Use

Use `memory_health` for an explicit connectivity check and `memory_add` only
for durable personal facts. Use the `organization-memory` Skill for shared
project and organization facts, including the explicit read visibility and
provenance rules. Completed conversation turns are already written passively;
do not duplicate transient turns with explicit memory writes.

If the current user is not mapped or the remote service is unavailable,
continue with the current conversation and workspace files. Do not edit `.env`,
ask for a token, attempt a local fallback, or block the user's chat response.

## Operator Responsibilities

Only the service operator provisions Fusion Memory, creates/revokes bearer
tokens, configures the reverse proxy, and manages service storage. The operator
keeps the MCP, model, and history services supervised by `systemd` so they
survive SSH disconnects and restart after failures. Example health checks:

```bash
systemctl --user is-active fusion-memory-mcp.service
systemctl --user status fusion-memory-mcp.service
systemctl --user status fusion-memory-embedding@default.service
systemctl --user status fusion-memory-reranker@default.service
systemctl --user status 'fusion-memory-history-sync@<instance>.service'
systemctl --user status fusion-memory-health.timer
```

Use deployment-managed secrets for the token map. Do not paste token values
into shell history, tickets, chat, logs, source control, or workspace files.

## Recovery

If a configured remote endpoint cannot be reached, report the endpoint host
without credentials and the tool error. Ask the operator to check the
`systemd` units and reverse-proxy health. Haitun must not provision a service,
mint a token, weaken TLS, or switch memory transport.
