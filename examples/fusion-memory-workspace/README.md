# Fusion Memory Workspace

This example workspace lets `psi-agent` and Haitun-Agent use Fusion Memory
through HTTP-only workspace tools. It does not load `MemoryService`, model code,
or database code inside the agent session process.

Workspace path:

```text
examples/fusion-memory-workspace
```

The memory repository carries the canonical integration copy under
`integrations/dolphin-fusion-memory/workspace`; keep this example and
`examples/haitun-workspace` in sync with that HTTP contract.

## Tools

- `memory_add`: store durable user preferences, project facts, or stable decisions.
- `memory_search`: retrieve raw evidence by keyword.
- `memory_answer_context`: retrieve a query-grounded context pack, preferred for
  answering questions about prior user history, preferences, and project context.

## Environment

- `PSI_MEMORY_BASE_URL`: Fusion Memory HTTP server URL. Defaults to
  `http://127.0.0.1:8700`.
- `PSI_MEMORY_WORKSPACE_ID`: memory workspace scope. Defaults to `haitun`.
- `PSI_MEMORY_USER_ID`: user scope. Defaults to the current OS user or `user`.
- `PSI_MEMORY_AGENT_ID`: agent scope. Defaults to `haitun`.
- `PSI_MEMORY_SESSION_ID`: optional session scope. When unset, reads allow
  cross-session retrieval.
- `PSI_MEMORY_TIMEOUT_SECONDS`: request timeout in seconds. Defaults to `30.0` and is
  clamped to `0.1..120.0`.
- `FUSION_MEMORY_SMOKE_MEMORY_URL`: smoke-script-only override for the Fusion Memory
  URL.

## First Use Setup

Before using `memory_add`, `memory_search`, or `memory_answer_context` for the
first time, initialize and start Fusion Memory. The workspace includes
`skills/fusion-memory-setup/SKILL.md` with the full beginner workflow.

Minimal local setup:

```bash
git clone https://github.com/genuineknowledge/fusion-memory.git
cd fusion-memory
sh install.sh
fusion-memory init --json
fusion-memory start --json
fusion-memory doctor --json
export PSI_MEMORY_BASE_URL=http://127.0.0.1:8700
```

If port `8700` is already in use, `fusion-memory start --json` tries the next available local port and returns the actual `url`; set `PSI_MEMORY_BASE_URL` to that returned URL before starting this workspace.

The Fusion Memory installer installs Fusion Memory as a `uv tool` with a
uv-managed Python 3.12 runtime, then downloads the Qwen model directories from
ModelScope into the Fusion Memory home `models/` directory. It installs the full
local Qwen runtime dependencies, including Postgres adapter, local Qwen adapter,
PyTorch, and Transformers. If model download or dependency installation fails,
install-check reports not ready with the failed step and log path. The installer
does not require Git LFS and does not silently fall back to `local_test`.
The default local configuration uses SQLite plus local Qwen models;
Postgres/pgvector is optional for production deployments. Only when model files
and dependencies are present but this hardware/runtime cannot load or run both
local vector models does it fall back to a compromised local mode with built-in
lightweight retrieval and print the API-key next step.
Recommended API provider: Aliyun DashScope; set `DASHSCOPE_API_KEY` before
configuring API-backed providers.

## Run

Start Fusion Memory on the default local port:

```bash
cd /path/to/fusion-memory
python -m fusion_memory.server --port 8700
```

Run the live adapter smoke:

```bash
cd /path/to/psi-agent
FUSION_MEMORY_SMOKE_MEMORY_URL=http://127.0.0.1:8700 \
python examples/fusion-memory-workspace/smoke.py
```

The smoke writes a unique token with `memory_add`, retrieves it with
`memory_search`, and asks for `memory_answer_context`. It exits with code `0` only
when all three steps confirm the token against a live Fusion Memory server.

Start an agent session with this workspace:

```bash
PSI_MEMORY_BASE_URL=http://127.0.0.1:8700 \
PSI_MEMORY_SESSION_ID=<session-id> \
uv run psi-agent session \
  --workspace examples/fusion-memory-workspace \
  --session-id <session-id> \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock
```

## Automatic History Persistence

Passive persistence is enabled without changing agent core by running the Fusion
Memory history sync process beside the Haitun-Agent session. It reads saved Haitun history from
`histories/<session-id>.jsonl`, keeps a checkpoint, and stores only new saved
user/assistant turns. It does not patch Haitun-Agent internals and cannot see
messages that were never written to the history file.

Explicit tool writes still happen only when the agent calls `memory_add`. Those
writes are marked with
`metadata.write_mode="explicit_tool"` and `metadata.auto_persisted=false`.
Synced passive writes are marked with `metadata.write_mode="history_sync"` and
`metadata.auto_persisted=true`.

Start passive saved-history sync in the background:

```bash
fusion-memory sync-haitun-history \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --background --json
fusion-memory status-haitun-history-watcher \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --json
```

For a one-time backfill, use `sync-haitun-history --once --json`. Do not run the
long-running watcher in a foreground WebUI/tool call.

## Copy Into Another Workspace

This workspace is self-contained. To create a new memory-only workspace, copy the
whole directory:

```bash
cp -R /path/to/integrations/dolphin-fusion-memory/workspace ./my-memory-workspace
```

Then start the agent with `--workspace ./my-memory-workspace`.

To add Fusion Memory to an existing workspace, copy the memory tools and merge
the system prompt instructions:

```bash
mkdir -p ./my-workspace/tools ./my-workspace/systems ./my-workspace/skills
cp examples/fusion-memory-workspace/tools/_fusion_memory_client.py ./my-workspace/tools/
cp examples/fusion-memory-workspace/tools/_fusion_memory_config.py ./my-workspace/tools/
cp examples/fusion-memory-workspace/tools/memory_*.py ./my-workspace/tools/
cp -R examples/fusion-memory-workspace/skills/fusion-memory-setup ./my-workspace/skills/
```

If the target workspace already has `systems/system.py`, add the Fusion Memory
tool guidance from this workspace's `systems/system.py` into the existing prompt.
If it does not, copy `examples/fusion-memory-workspace/systems/system.py` as-is.

## Offline Behavior

The tools never raise Fusion Memory connection failures into the agent loop. If
Fusion Memory is offline or returns an error, they return structured JSON:

```json
{"ok": false, "error": "service_unavailable", "cause": "connection_failed", "message": "Fusion Memory service is not reachable. Run fusion-memory status or fusion-memory start."}
```

If the server returns a request error, `error`, `cause`, and `message` preserve
the safe server-provided reason, for example `bad_request` with `missing_scope`.
The Haitun session can continue without memory and retry after the Fusion Memory
server is available or the request shape is fixed.
