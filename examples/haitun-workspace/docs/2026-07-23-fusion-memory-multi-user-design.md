# Fusion Memory Multi-User Token Routing Design

## Goal

Make the Fusion Memory MCP clients in `examples/fusion-memory-workspace` and
`examples/haitun-workspace` select bearer tokens by the Feishu user's isolated
Session, without changing psi-agent core, Gateway, or Feishu Channel code.

The Feishu multi-user route creates deterministic Session IDs of the form
`feishu-<open_id>`. The token map is an operator-managed JSON object keyed by
the same `open_id`. Each value contains a bearer `token` and a provenance-only
`workspace_id`.

## Scope

Only files below these directories may change:

- `examples/fusion-memory-workspace`
- `examples/haitun-workspace`

No token map, bearer token, generated credential file, core runtime change, or
new test file is committed.

## Configuration

Add the optional launcher variable:

```text
FUSION_MEMORY_TOKEN_MAP_FILE=/path/to/operator-managed-memory-tokens.json
```

The MCP endpoint remains process-level configuration through
`FUSION_MEMORY_MCP_URL`. TLS configuration such as `SSL_CERT_FILE` also remains
deployment-managed.

The launcher must configure `FUSION_MEMORY_TOKEN_MAP_FILE` before starting the
HaiTun process. Changing the file path itself requires a process restart;
changing entries inside the configured file is detected by the runtime cache
without restarting HaiTun.

The token map schema is:

```json
{
  "ou_example": {
    "token": "<operator-issued-bearer-token>",
    "workspace_id": "haitun"
  }
}
```

Additional metadata fields are ignored. Token values must never appear in
logs, error messages, dataclass representations, documentation examples,
tool results, or source control.

## Identity Resolution

At each memory tool call, the memory integration reads the current Session ID
through `psi_agent.session.runtime_context.get_session_id()`.

- `feishu-ou_example` resolves to `ou_example`.
- A non-Feishu Session ID has no mapped Feishu identity.
- Sanitization is reversible for current Feishu `open_id` values because they
  use the safe character set retained by `FeishuManager`.

The message-level `<feishu_context>` block is not used for authentication. It
is model-visible input and therefore cannot select a bearer token.

## Token Selection Rules

When `FUSION_MEMORY_TOKEN_MAP_FILE` is configured, map mode is fail-closed:

1. Resolve the Feishu `open_id` from the current Session ID.
2. Load the matching map entry.
3. Use only that entry's bearer token and workspace context.
4. Send the current Session ID as request provenance.

There is no fallback to `FUSION_MEMORY_TOKEN` in map mode. This applies when:

- the Session is the Feishu shared fallback Session;
- the user is not present in the map;
- the map is missing, unreadable, invalid JSON, or has an invalid entry.

These cases return a structured `memory_user_not_configured` or
`configuration_error` result before opening an MCP connection. Ordinary chat
continues because only the memory tool call fails.

When the map variable is absent, the existing single-user environment-token
behavior remains compatible for non-multi-user deployments.

## Startup And Automatic Activation

"Start each user's long-term memory" means that the launcher supplies one
operator-managed token map and HaiTun automatically activates the correct
user's MCP client when that user's isolated Feishu Session first needs memory.
No user or agent must copy, paste, request, or assign a bearer token during a
conversation.

The token map is parsed and structurally validated by the memory integration
when its workspace modules are loaded. Tool loading remains available if the
file is temporarily invalid; affected calls fail closed with structured errors
instead of removing the tools from HaiTun.

MCP network sessions are not opened for every token during Python module
import. At that point most per-user Feishu Sessions do not exist, and bulk
network side effects would couple workspace loading to the availability of all
users and the remote service. Instead:

1. A mapped user's first `memory_health`, read, or write call creates that
   user's client and establishes the MCP connection.
2. Later calls reuse the same user-specific client and reconnect through the
   existing supervisor when needed.
3. HaiTun's Fusion Memory skill and system guidance direct the agent to call
   `memory_health` when a user asks whether memory is active, and before the
   first relevant memory operation when a health check is needed.
4. Unknown users continue chatting; their memory calls stop before any bearer
   header or network request is created.

This preserves automatic per-user availability while keeping startup resilient
when the Memory service is temporarily unavailable.

## Client Lifecycle

Replace the process-wide single-token client facade with a routing facade that
resolves credentials at call time and maintains independent MCP clients keyed
by resolved user configuration.

- Different mapped users never share a client or Authorization header.
- The same mapped user reuses a client across calls.
- A token or workspace change in the operator map creates/replaces that user's
  effective client without exposing the old or new token.
- Read retry and reconnect behavior remains unchanged inside each client.
- `memory_add` remains non-replayed unless an idempotency key is supplied by
  the existing protocol rules.

The map may be cached by file metadata, but updates must become visible without
restarting HaiTun. Cache state must not contain printable credential objects.

## Tools

The existing tools keep their public signatures:

- `memory_add`
- `memory_search`
- `memory_answer_context`

Add `memory_health`, which invokes the server's authenticated `memory_health`
MCP tool using the same per-user routing rules. It lets HaiTun answer memory
status questions with an actual connectivity check instead of inspecting or
editing `.env` files.

The setup skill must describe launcher-time token-map configuration and the
automatic first-use activation flow. It must not tell HaiTun to edit `.env`,
mint tokens, or start a local Memory service.

## Workspace Synchronization

Both example workspaces receive the same resolver, routing, error, and health
semantics. Existing differences such as branding, default source names, prompt
wording, and README structure remain intact.

Update each workspace's setup skill, system prompt or system description,
README, and applicable `AGENTS.md` guidance so that operators know:

- multi-user deployments use `FUSION_MEMORY_TOKEN_MAP_FILE`;
- map keys are Feishu `open_id` values;
- unknown users can chat but have no bearer token and no durable memory;
- map mode never falls back to a shared bearer token;
- secrets stay outside workspaces and source control.

## Verification

Do not add test files to the pull request. Before implementation, use a
temporary one-off harness to demonstrate the current failure. After each
implementation step, use the same harness plus existing repository checks.

The verification cases are:

1. Two Feishu Session IDs select different tokens and different MCP clients.
2. Repeated calls for one Feishu user reuse that user's client.
3. A mapped user receives its configured workspace and current Session header.
4. An unknown Feishu user returns `memory_user_not_configured` without opening
   a connector.
5. A shared or non-Feishu Session receives no token in map mode.
6. Invalid or missing map data fails closed and never falls back globally.
7. Without map mode, the existing single-user environment-token flow works.
8. `memory_health` routes through the same user-specific client.
9. Token-map updates are observed without process restart.
10. Formatting, lint, relevant existing tests, and both workspace smoke checks
    pass.

## Security Properties

The Memory service remains the authority for user isolation: each mapped
Feishu user presents a distinct operator-issued bearer token. Workspace and
Session headers remain provenance only. The client-side resolver prevents
accidental cross-user token reuse, while the server token subject enforces the
actual data boundary.
