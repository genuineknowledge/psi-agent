# Fusion Memory Workspace

This example workspace exposes Fusion Memory as an MCP client. The memory
service runs separately, normally on an operator-managed remote server, and
the agent process never imports database, model, or memory-service code.

## Tools

- `memory_add`: store a durable user preference, project fact, or decision.
- `memory_search`: retrieve raw evidence.
- `memory_answer_context`: retrieve query-grounded context for an answer.

All three tools call the Streamable HTTP MCP endpoint at
`FUSION_MEMORY_MCP_URL`. There is no native memory HTTP API in this workspace.

## Configuration

Set the operator-issued endpoint and bearer token before starting the agent:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_TOKEN="<operator-issued-token>"
export FUSION_MEMORY_WORKSPACE_ID="fusion-memory"
export FUSION_MEMORY_SESSION_ID="<current-session-id>"
```

The endpoint path must be exactly `/mcp`; HTTPS is required for non-loopback
hosts. The token determines the user identity. A user's sessions and
workspaces share memory on the server, while users represented by different
tokens are isolated. `FUSION_MEMORY_SESSION_ID` is provenance only and does
not create a separate memory partition.

Optional settings:

- `FUSION_MEMORY_MCP_TIMEOUT_SECONDS`: request timeout, default `30`, bounded
  to `0.1..120` seconds.
- `FUSION_MEMORY_MCP_MAX_RETRIES`: retry count for reads and idempotent writes,
  default `2`, bounded to `0..5`.

## Operator Service

The service operator owns Fusion Memory deployment, Postgres, local embedding
and reranker model pools, token provisioning, and restart supervision. A
typical deployment checks:

```bash
systemctl --user is-active fusion-memory-mcp.service
systemctl --user is-active fusion-memory-health.timer
systemctl --user is-active fusion-memory-embedding@default.service
systemctl --user is-active fusion-memory-reranker@default.service
```

The client reconnects after transport loss. The server's health unit and
stateful-session idle timeout provide recovery when a machine or SSH
connection disappears. This workspace must not install, start, or fall back to
a local memory service.

## First Use

Read `skills/fusion-memory-setup/SKILL.md` before the first memory call. If the
endpoint or token is absent, or the service is unavailable, continue without
durable memory and report the safe structured error. Never put the token in
source control, logs, shell history, or generated files.
