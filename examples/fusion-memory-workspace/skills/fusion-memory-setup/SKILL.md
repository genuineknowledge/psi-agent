---
name: fusion-memory-setup
description: Use before first using Fusion Memory tools or when the remote MCP service is unavailable.
---

# Fusion Memory MCP Setup

This workspace is an MCP client. It does not install, start, or embed a
Fusion Memory server, database, model, or HTTP memory API.

## Configure

The operator must provide a Streamable HTTP MCP endpoint and a bearer token
before the first memory call:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_TOKEN="<operator-issued-token>"
export FUSION_MEMORY_WORKSPACE_ID="fusion-memory"
export FUSION_MEMORY_SESSION_ID="<current-session-id>"
```

`FUSION_MEMORY_MCP_URL` must be exactly `/mcp`. HTTPS is required for remote
hosts; HTTP is accepted only for loopback development. The token determines
the user identity. The server shares memory across that user's sessions and
workspaces, while tokens for different users remain isolated.

Never commit, print, or log the token. `FUSION_MEMORY_MCP_TIMEOUT_SECONDS`
defaults to 30 seconds and is clamped to `0.1..120`; retryable reads and
idempotent writes reconnect automatically after transport failures.

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

## Tool Policy

- Use `memory_add` for durable preferences, facts, decisions, and corrections.
- Use `memory_search` for raw evidence when prior context is relevant.
- Use `memory_answer_context` for query-grounded context before answering
  questions about the user's history or preferences.
- Do not add transient conversation turns as explicit facts; history sync is a
  separate operator-managed service when enabled.
