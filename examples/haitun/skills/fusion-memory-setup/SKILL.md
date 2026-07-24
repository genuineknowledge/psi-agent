---
name: fusion-memory-setup
description: Use before first using Fusion Memory tools or when a remote Fusion Memory MCP tool reports unavailable.
---

# Fusion Memory Remote MCP Setup

Haitun is a consumer of an operator-provisioned remote Fusion Memory service.
It must use the service's MCP Streamable HTTP endpoint; it must not install,
start, configure, or create tokens for a local memory service.

## Required Configuration

Set these values in the process environment or a deployment-managed secret
store before starting Haitun:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_TOKEN="<operator-issued-bearer-token>"
export FUSION_MEMORY_WORKSPACE_ID="haitun"
export FUSION_MEMORY_SESSION_ID="<current-session-id>"
```

- `FUSION_MEMORY_MCP_URL` is the remote MCP Streamable HTTP endpoint. TLS is
  terminated by the service's reverse proxy.
- `FUSION_MEMORY_TOKEN` is a bearer token issued and managed by the service
  operator. Never commit it, log it, echo it, or put it in generated files.
- `FUSION_MEMORY_WORKSPACE_ID` identifies the Haitun workspace context. It
  defaults to `haitun` when omitted.
- `FUSION_MEMORY_SESSION_ID` identifies the current session context and may be
  omitted when the launcher does not provide one.

The bearer token, not a client-supplied user identifier, determines the user
identity. Memory is shared across sessions and workspaces for the same user;
different users, including users represented by different tokens, are isolated.

## Consent And Use

Before the first memory tool call, follow the workspace consent policy. When
consent is required and has not been granted, do not call `memory_add`,
`memory_search`, or `memory_answer_context`; continue without durable memory.
After consent, use `memory_add` only for durable, reusable facts and use the
search/context tools only when previous user information is relevant.

If either the endpoint or token is absent, or a memory tool reports that the
remote service is unavailable, explain that durable memory is unavailable and
continue with the current conversation and workspace files. Do not attempt a
local fallback.

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

Use deployment-managed secrets for the token. Do not paste token values into
shell history, tickets, chat, logs, source control, or the workspace `.env`.

## Recovery

If a configured remote endpoint cannot be reached, report the endpoint host
without credentials and the tool error. Ask the operator to check the
`systemd` units and reverse-proxy health. Haitun must not provision a service,
mint a token, weaken TLS, or switch memory transport.
