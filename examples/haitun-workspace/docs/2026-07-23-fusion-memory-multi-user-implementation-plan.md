# Fusion Memory Multi-User Automatic Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route Fusion Memory MCP credentials by Feishu Session and automatically start authenticated MCP health checking plus passive JSONL history persistence on each mapped user's first message.

**Architecture:** Each workspace keeps a process-level routing facade, but resolves credentials from `FUSION_MEMORY_TOKEN_MAP_FILE` for the current trusted Session Context before every effective operation. The system prompt builder/checker provides the first-message activation hook, while a daemon thread per mapped Session performs non-blocking health checks and idempotent `memory_add_batch` synchronization.

**Tech Stack:** Python 3.14, anyio, MCP Streamable HTTP client, httpx, psi-agent workspace system prompt lifecycle, JSONL history files.

## Global Constraints

- Modify only `examples/fusion-memory-workspace` and `examples/haitun-workspace`.
- Do not add test files to the pull request; use `/tmp/fusion_memory_multi_user_harness.py` for RED/GREEN verification.
- The process starter manually configures `FUSION_MEMORY_TOKEN_MAP_FILE` before HaiTun starts.
- Never commit, print, return, or log bearer-token values.
- In token-map mode, an unknown, shared, non-Feishu, invalid, or missing user mapping never falls back to `FUSION_MEMORY_TOKEN`.
- Unknown users can chat normally but receive no MCP connection, passive writer, checkpoint, or durable memory.
- The same bearer-token subject shares memory across that user's Sessions; different users remain isolated.
- Use the trusted `psi_agent.session.runtime_context.get_session_id()` value, never model-visible Feishu message context, for authentication routing.
- Preserve legacy single-user `FUSION_MEMORY_TOKEN` behavior only when `FUSION_MEMORY_TOKEN_MAP_FILE` is absent.
- Keep all background work non-blocking for the user's first response and independently retryable per Session.

---

### Task 1: Credential Resolution And Fail-Closed Routing

**Files:**
- Modify: `examples/fusion-memory-workspace/tools/_fusion_memory_config.py`
- Modify: `examples/haitun-workspace/tools/_fusion_memory_config.py`
- Temporary test: `/tmp/fusion_memory_multi_user_harness.py`

**Interfaces:**
- Consumes: process environment and a trusted Session ID such as `feishu-ou_example`.
- Produces: `async resolve_memory_config(session_id: str, config: MemoryMcpConfig = CONFIG) -> ResolvedMemoryConfig` and `MemoryConfigError(code: str, message: str)`.

- [ ] **Step 1: Write the failing credential-resolution harness**

  The harness must create a temporary token-map JSON file with two placeholder tokens, load each workspace config module by file path, and assert:

  ```python
  first = module.resolve_memory_config("feishu-ou_first", config)
  second = module.resolve_memory_config("feishu-ou_second", config)
  assert first.token != second.token
  assert first.session_id == "feishu-ou_first"
  assert second.session_id == "feishu-ou_second"
  ```

  It must also assert that an unknown Feishu Session, a non-Feishu Session, invalid JSON, and a missing map file raise `MemoryConfigError` without using a configured global fallback token. Finally, it must assert that the legacy environment-token path resolves when the map variable is absent.

- [ ] **Step 2: Run the harness to verify RED**

  Run: `uv run python /tmp/fusion_memory_multi_user_harness.py`

  Expected: FAIL because `FUSION_MEMORY_TOKEN_MAP_FILE`, `ResolvedMemoryConfig`, `MemoryConfigError`, and `resolve_memory_config` do not exist.

- [ ] **Step 3: Implement the minimal resolver in both workspaces**

  Add `token_map_file: str | None` to `MemoryMcpConfig`, a frozen `ResolvedMemoryConfig` whose `token` field uses `repr=False`, and a structured `MemoryConfigError`. Resolve `feishu-<open_id>` only from the passed Session ID. In map mode, read and validate the JSON object on every resolution, require a non-empty string `token`, reject duplicate token assignments, accept an empty or omitted `workspace_id` by falling back to the workspace default, and raise safe errors that never include token values or full entry representations.

- [ ] **Step 4: Run the harness to verify GREEN**

  Run: `uv run python /tmp/fusion_memory_multi_user_harness.py`

  Expected: credential-resolution cases PASS for both workspaces.

- [ ] **Step 5: Commit the resolver**

  ```bash
  git add examples/fusion-memory-workspace/tools/_fusion_memory_config.py \
    examples/haitun-workspace/tools/_fusion_memory_config.py
  git commit -m "feat(memory): route credentials by Feishu session"
  ```

### Task 2: Per-User MCP Client Router And Health Tool

**Files:**
- Modify: `examples/fusion-memory-workspace/tools/_fusion_memory_mcp.py`
- Modify: `examples/haitun-workspace/tools/_fusion_memory_mcp.py`
- Create: `examples/fusion-memory-workspace/tools/_fusion_memory_history.py`
- Create: `examples/haitun-workspace/tools/_fusion_memory_history.py`
- Create: `examples/fusion-memory-workspace/tools/memory_health.py`
- Create: `examples/haitun-workspace/tools/memory_health.py`
- Temporary test: `/tmp/fusion_memory_multi_user_harness.py`

**Interfaces:**
- Consumes: `resolve_memory_config()` and the current `get_session_id()` ContextVar.
- Produces: `MemoryMcpRouter.call_tool(name, arguments, retryable=...)`, `MemoryMcpRouter.call_tool_for_session(session_id, ...)`, and public `memory_health() -> str`.

- [ ] **Step 1: Extend the harness with failing router cases**

  Use a fake MCP connector that captures request headers without printing them. Bind two Session IDs with `session_id_scope`, call `memory_health`, and assert different Authorization headers, different client objects, correct workspace/session headers, connector reuse for repeated calls in one Session, and no connector calls for unknown users. Replace one map entry and assert the next call creates a replacement client with the updated effective credentials.

- [ ] **Step 2: Run the harness to verify RED**

  Run: `uv run python /tmp/fusion_memory_multi_user_harness.py`

  Expected: FAIL because the existing process-wide `CLIENT` always uses one import-time token and Session header.

- [ ] **Step 3: Implement the routing facade**

  Keep `MemoryMcpClient` as the connection supervisor but make its token private. Add `memory_health` to retryable read tools and recognize `batch_id` as an idempotency key. Replace the singleton client with `CLIENT = MemoryMcpRouter(CONFIG)`. Key clients by non-secret identity, token SHA-256 digest, workspace ID, trusted Session ID, and MCP URL; replace and close a Session's old client when its effective mapping changes. Resolve credentials before creating any connector.

- [ ] **Step 4: Add `memory_health` wrappers**

  Each wrapper must load the same path-stable private MCP module as the existing tools and return `json.dumps(await CLIENT.call_tool("memory_health", {}, retryable=True), ensure_ascii=False)`.

- [ ] **Step 5: Run the harness to verify GREEN**

  Run: `uv run python /tmp/fusion_memory_multi_user_harness.py`

  Expected: all routing, isolation, reuse, fail-closed, legacy, health, and map-update cases PASS for both workspaces.

- [ ] **Step 6: Commit the router**

  ```bash
  git add examples/fusion-memory-workspace/tools examples/haitun-workspace/tools
  git commit -m "feat(memory): isolate MCP clients per Feishu user"
  ```

### Task 3: First-Message Activation And Passive History Writer

**Files:**
- Modify: `examples/fusion-memory-workspace/tools/_fusion_memory_mcp.py`
- Modify: `examples/haitun-workspace/tools/_fusion_memory_mcp.py`
- Modify: `examples/fusion-memory-workspace/systems/system.py`
- Modify: `examples/haitun-workspace/systems/system.py`
- Create: `examples/fusion-memory-workspace/.gitignore`
- Modify: `examples/haitun-workspace/.gitignore`
- Temporary test: `/tmp/fusion_memory_multi_user_harness.py`

**Interfaces:**
- Consumes: a workspace root, trusted current Session ID, and `histories/<session-id>.jsonl`.
- Produces: `MemoryMcpRouter.activate_current_session(workspace_root) -> dict[str, Any]` and one idempotent passive writer per mapped workspace/Session.

- [ ] **Step 1: Extend the harness with failing activation cases**

  Build a temporary workspace and JSONL history. Inside `session_id_scope("feishu-ou_first")`, invoke the system builder and wait on conditions rather than fixed sleeps until the fake connector records `memory_health` and one `memory_add_batch`. Assert the submitted batch contains only a completed user/assistant turn, carries a deterministic `batch_id`, creates an atomic checkpoint, and is not resubmitted after another activation. Assert a trailing user-only turn is not submitted. Repeat with an unknown user and assert no connector, writer, or checkpoint is created.

- [ ] **Step 2: Run the harness to verify RED**

  Run: `uv run python /tmp/fusion_memory_multi_user_harness.py`

  Expected: FAIL because system-prompt callbacks do not activate Memory and no in-process passive writer exists.

- [ ] **Step 3: Implement passive synchronization**

  Add a daemon thread registry keyed by canonical workspace path and trusted Session ID. Its anyio loop must initiate `memory_health`, use `anyio.Path` for JSONL/checkpoint IO, scan only changed history files, normalize only completed ordinary chat user/assistant turns, call `memory_add_batch` with deterministic IDs and source metadata, atomically replace checkpoint files after confirmed success, revalidate a file-signature-cached map on each cycle, renew a bounded watcher lease from system-prompt checks, and retry recoverable outages with bounded exponential backoff. Log Session-safe lifecycle and error codes only; never log credentials, headers, map entries, or message contents.

- [ ] **Step 4: Wire first-message activation through system callbacks**

  In both `system.py` files, load the workspace's path-stable `_fusion_memory_mcp.py` module and call `await CLIENT.activate_current_session(workspace_root)` at the start of `system_prompt_builder()`. Add `system_prompt_rebuild_checker()` that calls the same idempotent activation entry point and returns `False`, covering existing histories on the first message after a process restart.

- [ ] **Step 5: Ignore runtime checkpoints**

  Add `.fusion-memory/` to each workspace `.gitignore`; retain the existing `histories/` ignore behavior.

- [ ] **Step 6: Run the harness to verify GREEN**

  Run: `uv run python /tmp/fusion_memory_multi_user_harness.py`

  Expected: automatic activation, passive batching, checkpoint idempotency, incomplete-turn filtering, and unknown-user no-op cases PASS for both workspaces.

- [ ] **Step 7: Commit automatic activation**

  ```bash
  git add examples/fusion-memory-workspace examples/haitun-workspace
  git commit -m "feat(memory): activate passive memory on first message"
  ```

### Task 4: Operator And Agent Guidance

**Files:**
- Modify: `examples/fusion-memory-workspace/README.md`
- Modify: `examples/fusion-memory-workspace/skills/fusion-memory-setup/SKILL.md`
- Modify: `examples/haitun-workspace/README.md`
- Modify: `examples/haitun-workspace/AGENTS.md`
- Modify: `examples/haitun-workspace/TOOLS.md`
- Modify: `examples/haitun-workspace/skills/fusion-memory-setup/SKILL.md`
- Modify: `examples/haitun-workspace/systems/prompt_sections.py`
- Modify: `examples/haitun-workspace/docs/2026-07-23-fusion-memory-multi-user-design.md`

**Interfaces:**
- Consumes: the implemented environment variables and runtime behavior.
- Produces: accurate starter configuration and model guidance with no secret-bearing examples.

- [ ] **Step 1: Update starter configuration**

  Replace multi-user examples of `FUSION_MEMORY_TOKEN` with:

  ```bash
  export FUSION_MEMORY_MCP_URL="https://memory.example.com/mcp"
  export FUSION_MEMORY_TOKEN_MAP_FILE="/absolute/path/to/memory_tokens.json"
  ```

  Document the map schema using placeholder values, the manual pre-start requirement, map-content hot reload, and the fact that the path itself changes only after restart.

- [ ] **Step 2: Update runtime guidance**

  State that token-map membership enables automatic first-message health connection and passive persistence; unknown users retain chat without durable memory; the agent must not edit `.env`, ask for tokens, create tokens, or start a Memory server. Add `memory_health` to tool lists and remove instructions that require the model to inspect environment variables or ask for per-conversation memory consent.

- [ ] **Step 3: Self-review documentation**

  Run:

  ```bash
  grep -RIn "FUSION_MEMORY_TOKEN\|consent\|first memory call\|three tools" \
    examples/fusion-memory-workspace \
    examples/haitun-workspace/README.md \
    examples/haitun-workspace/AGENTS.md \
    examples/haitun-workspace/TOOLS.md \
    examples/haitun-workspace/skills/fusion-memory-setup \
    examples/haitun-workspace/systems/prompt_sections.py \
    examples/haitun-workspace/docs/2026-07-23-fusion-memory-multi-user-design.md
  ```

  Expected: every remaining single-token reference is explicitly labeled legacy single-user compatibility; no stale consent or manual-start instructions remain.

- [ ] **Step 4: Commit documentation**

  ```bash
  git add examples/fusion-memory-workspace examples/haitun-workspace
  git commit -m "docs(memory): explain automatic multi-user activation"
  ```

### Task 5: Full Verification And PR Preparation

**Files:**
- Verify: all changed files under both allowed workspaces.
- Preserve: `.worktrees/` and `examples/haitun-workspace/fusion-memory/` as unrelated untracked user content.

**Interfaces:**
- Consumes: completed implementation and documentation.
- Produces: a verified branch ready to push and open as an Agent-side pull request.

- [ ] **Step 1: Run the full temporary harness**

  Run: `uv run python /tmp/fusion_memory_multi_user_harness.py`

  Expected: both workspace implementations pass all resolver, routing, activation, passive writer, idempotency, and fail-closed cases without printing token values.

- [ ] **Step 2: Run workspace-focused formatting and lint**

  ```bash
  uv run ruff format --check \
    examples/fusion-memory-workspace \
    examples/haitun-workspace/tools/_fusion_memory_config.py \
    examples/haitun-workspace/tools/_fusion_memory_mcp.py \
    examples/haitun-workspace/tools/memory_add.py \
    examples/haitun-workspace/tools/memory_search.py \
    examples/haitun-workspace/tools/memory_answer_context.py \
    examples/haitun-workspace/tools/memory_health.py \
    examples/haitun-workspace/systems/system.py \
    examples/haitun-workspace/systems/prompt_sections.py
  uv run ruff check \
    examples/fusion-memory-workspace \
    examples/haitun-workspace/tools/_fusion_memory_config.py \
    examples/haitun-workspace/tools/_fusion_memory_mcp.py \
    examples/haitun-workspace/tools/memory_add.py \
    examples/haitun-workspace/tools/memory_search.py \
    examples/haitun-workspace/tools/memory_answer_context.py \
    examples/haitun-workspace/tools/memory_health.py \
    examples/haitun-workspace/systems/system.py \
    examples/haitun-workspace/systems/prompt_sections.py
  ```

  Expected: exit code 0 for both commands.

- [ ] **Step 3: Run type and smoke verification**

  ```bash
  uv run ty check \
    examples/fusion-memory-workspace/tools \
    examples/fusion-memory-workspace/systems \
    examples/haitun-workspace/tools/_fusion_memory_config.py \
    examples/haitun-workspace/tools/_fusion_memory_mcp.py \
    examples/haitun-workspace/tools/memory_add.py \
    examples/haitun-workspace/tools/memory_search.py \
    examples/haitun-workspace/tools/memory_answer_context.py \
    examples/haitun-workspace/tools/memory_health.py \
    examples/haitun-workspace/systems/system.py \
    examples/haitun-workspace/systems/prompt_sections.py
  uv run python examples/fusion-memory-workspace/systems/system.py
  uv run python examples/haitun-workspace/systems/system.py
  ```

  Expected: type check and both system-prompt smoke commands exit 0; no connector starts because no trusted Feishu Session Context is bound.

- [ ] **Step 4: Inspect scope and secret safety**

  ```bash
  git status --short
  git diff --check origin/main...HEAD
  git diff --name-only origin/main...HEAD
  git grep -n "Bearer " -- examples/fusion-memory-workspace examples/haitun-workspace
  ```

  Expected: tracked changes are confined to the two allowed workspaces; unrelated untracked content remains untouched; only source code that constructs the Authorization header contains the literal `Bearer ` prefix; no real token or token-map file is staged.

- [ ] **Step 5: Push and create the Agent-side PR**

  ```bash
  git push -u origin feat/haitun-fusion-memory-multi-user
  gh pr create --base main --head feat/haitun-fusion-memory-multi-user
  ```

  The PR description must summarize manual token-map-path configuration, first-message automatic activation, per-user fail-closed routing, passive batch persistence, unknown-user chat behavior, compatibility mode, and exact verification commands.
