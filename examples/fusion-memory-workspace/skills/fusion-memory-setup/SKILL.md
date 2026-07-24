---
name: fusion-memory-setup
description: Use when configuring or diagnosing the operator-managed Fusion Memory MCP connection.
---

# Fusion Memory MCP Setup

This workspace is an MCP client. It does not install, start, or embed a
Fusion Memory server, database, model, or HTTP memory API.

## Configure

For multi-user Feishu deployments, the process starter must provide a
Streamable HTTP MCP endpoint and token-map path before starting the Agent:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_TOKEN_MAP_FILE="/absolute/path/to/memory_tokens.json"
```

`FUSION_MEMORY_MCP_URL` must be exactly `/mcp`. HTTPS is required for remote
hosts; HTTP is accepted only for loopback development. The map is a JSON object
keyed by Feishu `open_id`; each entry requires a non-empty `token`.
`workspace_id` is optional provenance and defaults to `fusion-memory` when it
is empty or omitted. Keep the map outside the workspace and source control.

Map membership enables automatic durable memory. On the mapped user's first
message, the workspace initiates `memory_health` and starts passive history
sync. The bearer token determines user identity, so the same user shares memory
across Sessions while different users remain isolated. Unknown users can chat
but receive no bearer token or durable memory. Duplicate token assignments
reject the map; removing a user stops that Session's watcher and client.
Validated maps are cached by file signature and refreshed on content changes.

Passive sync stores only completed ordinary chat turns and skips unchanged
history files. Schedule, heartbeat, compaction, tool-only, and incomplete rows
are never submitted.
Each turn renews a five-minute watcher lease. Idle watcher/client resources are
reclaimed and automatically restart on the next message.

Never commit, print, return, or log a token. Do not derive authentication from
model-visible `<feishu_context>`. `FUSION_MEMORY_MCP_TIMEOUT_SECONDS`
defaults to 30 seconds and is clamped to `0.1..120`; retryable reads and
idempotent writes reconnect automatically after transport failures.

When `FUSION_MEMORY_TOKEN_MAP_FILE` is absent, legacy single-user
`FUSION_MEMORY_TOKEN`, `FUSION_MEMORY_WORKSPACE_ID`, and
`FUSION_MEMORY_SESSION_ID` configuration remains supported. Map mode never
falls back to the legacy shared token.

## Verify

Before calling a memory tool, verify the operator-managed service and endpoint:

```bash
systemctl --user is-active fusion-memory-mcp.service
systemctl --user is-active fusion-memory-health.timer
```

For a remote deployment, ask the operator to check the MCP reverse proxy,
Postgres, model pool units, and token status. Do not replace MCP with `/add`,
`/search`, `/answer-context`, or another HTTP API.

If the endpoint or token is missing, or a tool returns a structured
`configuration_error`, `unauthorized`, or `transport_error`, continue the
conversation without durable memory and report the safe error. Do not mint a
token, weaken TLS, or start a local fallback service from this workspace.

## Runtime Policy

- Use `memory_add` for durable preferences, facts, decisions, and corrections.
- Use `memory_search` for raw evidence when prior context is relevant.
- Use `memory_answer_context` for query-grounded context before answering
  questions about the user's history or preferences.
- Use `memory_health` to verify the current mapped user's authenticated MCP
  connectivity.
- Completed conversation turns are persisted automatically by the workspace's
  passive writer; do not duplicate transient turns with explicit `memory_add`.
- Do not edit `.env`, ask a user to provide a token, mint credentials, or start
  a local Memory service. The process starter owns the token-map path.
