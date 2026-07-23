# Fusion Memory Workspace

This example workspace exposes Fusion Memory as an MCP client. The memory
service runs separately, normally on an operator-managed remote server, and
the agent process never imports database, model, or memory-service code.

## Tools

- `memory_add`: store a durable user preference, project fact, or decision.
- `memory_search`: retrieve raw evidence.
- `memory_answer_context`: retrieve query-grounded context for an answer.
- `memory_health`: verify authenticated MCP connectivity for the current user.

All four tools call the Streamable HTTP MCP endpoint at
`FUSION_MEMORY_MCP_URL`. There is no native memory HTTP API in this workspace.

## Configuration

For a multi-user Feishu deployment, the process starter manually configures the
endpoint and operator-managed token-map path before starting the agent:

```bash
export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
export FUSION_MEMORY_TOKEN_MAP_FILE="/absolute/path/to/memory_tokens.json"
```

The endpoint path must be exactly `/mcp`; HTTPS is required for non-loopback
hosts. The map is a JSON object keyed by Feishu `open_id`:

```json
{
  "ou_example": {
    "token": "<operator-issued-token>",
    "workspace_id": "fusion-memory"
  }
}
```

The starter owns this file and keeps it outside the workspace and source
control. `token` is required; `workspace_id` is provenance only and may be
empty or omitted, in which case it defaults to `fusion-memory`. Map membership
enables durable memory for that user. The bearer token determines the
server-side user identity, so the same user shares memory across Sessions while
different users remain isolated. Map contents are re-read at runtime; changing
the configured file path requires an Agent restart. Assigning one token to
multiple `open_id` entries rejects the map. Removing an entry stops that
Session's watcher and closes its cached client. Validated map snapshots are
cached by file signature and refreshed when the file changes.

The passive writer stores only completed ordinary chat turns. Schedule,
compaction, heartbeat, tool-only, and incomplete rows are excluded. Unchanged
history files are not reparsed on each polling interval.
Each active turn renews the Session watcher lease; an idle watcher and its MCP
client are reclaimed after five minutes and restart on the next message.

The workspace route assumes a trusted Feishu Channel, Gateway, Session
runtime and management tools, host shell, and token-map file.
`feishu-<open_id>` is a routing convention, not a cryptographically
authenticated principal. Strong isolation from forged Session IDs or
workspace code that can read the complete map requires runtime authorization
and a privileged credential broker outside this example workspace.

When `FUSION_MEMORY_TOKEN_MAP_FILE` is absent, the legacy single-user variables
`FUSION_MEMORY_TOKEN`, `FUSION_MEMORY_WORKSPACE_ID`, and
`FUSION_MEMORY_SESSION_ID` remain supported. Token-map mode never falls back to
the shared legacy token.

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

## Automatic Activation

A mapped user's first message starts that Session's MCP health connection and
passive JSONL writer automatically. Completed user/assistant turns are sent via
`memory_add_batch` with idempotent checkpoints under `.fusion-memory/`; the
user's response is not blocked when the remote service is unavailable.

An unmapped or non-Feishu user can continue chatting, but no bearer header,
connector, writer, checkpoint, or durable memory is created. Use
`memory_health` for an explicit status check. The Agent must not edit `.env`,
ask a user for a token, mint credentials, or start a local fallback service.
