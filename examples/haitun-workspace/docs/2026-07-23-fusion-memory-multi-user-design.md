# Fusion Memory Multi-User Token Routing Design

## Goal

Make the Fusion Memory MCP clients in `examples/fusion-memory-workspace` and
`examples/haitun-workspace` select bearer tokens by the Feishu user's isolated
Session, without changing psi-agent core, Gateway, or Feishu Channel code.

The Feishu multi-user route creates deterministic Session IDs of the form
`feishu-<open_id>`. The token map is an operator-managed JSON object keyed by
the same `open_id`. Each value contains a bearer `token`; the provenance-only
`workspace_id` may be empty or omitted and then uses the workspace default.

## Scope

Only files below these directories may change:

- `examples/fusion-memory-workspace`
- `examples/haitun-workspace`

No token map, bearer token, generated credential file, core runtime change, or
new test file is committed.

## Configuration

The process starter manually configures this optional variable:

```text
FUSION_MEMORY_TOKEN_MAP_FILE=/path/to/operator-managed-memory-tokens.json
```

The MCP endpoint remains process-level configuration through
`FUSION_MEMORY_MCP_URL`. TLS configuration such as `SSL_CERT_FILE` also remains
deployment-managed.

The process starter must configure `FUSION_MEMORY_TOKEN_MAP_FILE` before starting the
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
tool results, or source control. `token` must be a non-empty string;
`workspace_id` must be a string when present, but an empty value falls back to
the current workspace default (`fusion-memory` or `haitun`). The full map is
rejected if two user entries contain the same token, because token equality
would merge their server-side identities.

## Identity Resolution

At each memory tool call, the memory integration reads the current Session ID
through `psi_agent.session.runtime_context.get_session_id()`.

- `feishu-ou_example` resolves to `ou_example`.
- A non-Feishu Session ID has no mapped Feishu identity.
- Sanitization is reversible for current Feishu `open_id` values because they
  use the safe character set retained by `FeishuManager`.

The message-level `<feishu_context>` block is not used for authentication. It
is model-visible input and therefore cannot select a bearer token.

### Trust Boundary

This workspace-level routing assumes that the Feishu Channel, Gateway,
Session runtime, Session-management tools, host shell, and operator-managed
token-map file are trusted. `feishu-<open_id>` is a runtime routing convention,
not a cryptographically authenticated principal. Under this deployment model,
users interact through the Feishu route and cannot choose another user's
Session ID or read the token map.

This change does not claim isolation against an untrusted Agent, Gateway
caller, Session-management tool, shell process, or host user. That stronger
threat model requires changes outside the two example workspaces: an
unforgeable principal injected by the Channel/Gateway path, authorization on
Session creation and handoff, authenticated Gateway control endpoints, and a
privileged credential broker that does not expose the complete token map to
workspace code.

An active watcher revalidates its map route before each polling cycle. Removing
or invalidating an entry revokes the cached client and stops that watcher;
rotating a token closes the stale client without delaying use of the new route.
History is reparsed only when file size or modification time changes, and only
completed `kind=chat` user/assistant turns are eligible. Schedule, heartbeat,
compaction, tool-only, and incomplete rows are excluded.
Validated token maps are cached process-wide per workspace implementation by
inode, modification time, and size, so unchanged polling does not repeatedly
parse or hash the full map. Each system-prompt check renews a five-minute
watcher lease. Deleted or idle Session resources are therefore reclaimed even
though the current runtime has no workspace-tool shutdown hook; the next turn
starts a fresh watcher idempotently.

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

"Start each user's long-term memory" means that the process starter manually
configures one operator-managed token-map path before launching HaiTun. HaiTun
then activates the correct user's MCP client and passive history writer on that
user's first message after the process starts. No user or agent must copy,
paste, request, discover, or assign a bearer token during a conversation.

Presence in the operator-managed token map is the enablement decision for
durable memory. A mapped user's conversation is therefore eligible for passive
history persistence immediately; the model does not ask for a second consent
step. An unmapped user can chat normally but receives neither a bearer token nor
durable memory.

The token-map path is captured when workspace modules are loaded, while the
small JSON map is parsed and structurally validated when a Session resolves its
credentials. Tool loading remains available if the file is temporarily
invalid; affected activation and tool calls fail closed with structured errors
instead of removing the tools from HaiTun.

MCP network sessions are not opened for every token during Python module
import. At that point most per-user Feishu Sessions do not exist, and bulk
network side effects would couple workspace loading to the availability of all
users and the remote service. Instead, the workspace uses the system-prompt
lifecycle as its trusted first-turn hook:

1. `SessionAgent.run()` binds the current Session ID before invoking either
   `system_prompt_builder()` or `system_prompt_rebuild_checker()`.
2. Both workspace callbacks call an idempotent Fusion Memory activation entry
   point. This covers new histories and existing histories restored after a
   HaiTun restart.
3. A mapped user's first activation starts a dedicated passive writer and
   immediately initiates an authenticated `memory_health` call in that
   background worker. The user response is not blocked when Memory is offline.
4. The writer watches `histories/<session-id>.jsonl`, submits completed
   user/assistant turns with `memory_add_batch`, and stores idempotent local
   checkpoints under `.fusion-memory/haitun-history-watcher/`.
5. Later turns and explicit memory tools reuse the same effective client and
   reconnect through the existing supervisor when needed.
6. Unknown users continue chatting; activation and memory calls stop before any
   bearer header, MCP connector, watcher, or checkpoint is created.

This preserves automatic per-user availability while keeping startup resilient
when the Memory service is temporarily unavailable.

## Client Lifecycle

Replace the process-wide single-token client facade with a routing facade that
resolves credentials at call time and maintains independent MCP clients keyed
by resolved user configuration and current Session provenance.

- Different mapped users never share a client or Authorization header.
- The same mapped user and Session reuse a client across calls.
- Different Sessions for the same mapped user may use separate MCP connections
  so each connection carries the correct `X-Fusion-Memory-Session` header; the
  shared bearer-token subject still gives those Sessions one user memory scope.
- A token or workspace change in the operator map creates/replaces the affected
  Session's effective client without exposing the old or new token.
- Read retry and reconnect behavior remains unchanged inside each client.
- `memory_add` remains non-replayed unless an idempotency key is supplied by
  the existing protocol rules.
- `memory_add_batch` uses its stable `batch_id` as the idempotency key and may be
  retried after a transport interruption.

The small operator map is re-read when credentials are resolved, so entry
updates become visible without restarting HaiTun. The configured map path
itself is process-start configuration and changing it requires a restart.

## Tools

The existing tools keep their public signatures:

- `memory_add`
- `memory_search`
- `memory_answer_context`

Add `memory_health`, which invokes the server's authenticated `memory_health`
MCP tool using the same per-user routing rules. It lets HaiTun answer memory
status questions with an actual connectivity check instead of inspecting or
editing `.env` files.

The setup skill must describe starter-managed token-map-path configuration and
the automatic first-message activation flow. It must not tell HaiTun to edit
`.env`, mint tokens, expose credentials, or start a local Memory service.

## Passive History Semantics

The passive writer persists only completed conversational turns. It ignores
system rows, tool-call rows, tool results, and incomplete trailing user-only
turns. This prevents a long-running tool round from producing multiple variants
of the same user message.

Each batch includes a deterministic `batch_id` derived from the workspace
history path, Session ID, source line range, and normalized user/assistant
messages. The checkpoint is written atomically only after the MCP server
confirms success. Service outages, process restarts, and repeated file scans
therefore retry safely without duplicating confirmed batches.

The writer performs no shell invocation and never places a token in command
arguments, child-process environments, checkpoint files, or logs. Background
workers for different users run independently, so one unavailable or slow user
does not serialize other users' memory operations.

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
10. A mapped user's first message starts one passive writer, initiates
    `memory_health`, and submits a completed JSONL turn through
    `memory_add_batch`.
11. Repeated turns do not start duplicate writers or resubmit checkpointed
    batches.
12. An unmapped user's first message starts no connector or writer and creates
    no checkpoint.
13. Formatting, lint, relevant existing tests, and both workspace smoke checks
    pass.

## Security Properties

The Memory service remains the authority for user isolation: each mapped
Feishu user presents a distinct operator-issued bearer token. Workspace and
Session headers remain provenance only. The client-side resolver prevents
accidental cross-user token reuse, while the server token subject enforces the
actual data boundary.
