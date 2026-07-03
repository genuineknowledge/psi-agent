---
name: fusion-memory-setup
description: Use before first using Fusion Memory tools, when memory tools return ok:false or service_unavailable, or when checking whether a local Fusion Memory checkout should be installed or updated.
---

# Fusion Memory Setup

Use this before the first `memory_add`, `memory_search`, or
`memory_answer_context` call in a workspace, and whenever a memory tool returns
`ok: false`.

## Check First

Always check before installing or upgrading. Fusion Memory must be checked out
under the current agent directory, not the workspace directory. Set `AGENT_DIR`
to the current agent directory; if it is unknown, ask the user once before
installing. The default tracks fusion-memory main, so update an existing
checkout from `origin/main` only when it is missing, behind, or the installed
package points somewhere else.

```bash
AGENT_DIR="/path/to/current-agent-directory"
fusion-memory status --json
fusion-memory doctor --json
git -C "$AGENT_DIR/fusion-memory" status --short --branch
git -C "$AGENT_DIR/fusion-memory" fetch origin main
git -C "$AGENT_DIR/fusion-memory" rev-list --left-right --count HEAD...origin/main
```

Do not run installation repeatedly while these checks are still in progress.
Tell the user the estimate first: a warm status check should take under 1
minute, a normal editable install should take 2-5 minutes, and a cold dependency
install can take 10-20 minutes depending on Python wheels, model dependencies,
and network speed.

## Install Or Update

If Fusion Memory is missing:

```bash
AGENT_DIR="/path/to/current-agent-directory"
git clone https://github.com/genuineknowledge/fusion-memory.git "$AGENT_DIR/fusion-memory"
cd "$AGENT_DIR/fusion-memory"
sh install.sh
```

The default local_full configuration is SQLite plus bundled local Qwen vector models:

```text
database: SQLite using Fusion Memory's default local database path
embedding: models/Qwen3-Embedding-0.6B
reranker: models/Qwen3-Reranker-0.6B
```

Postgres/pgvector is optional for production deployments that need pgvector
indexes, larger datasets, or multi-user service storage. It is not required for
the default local setup.

The repository includes the local vector model directories:

```text
models/Qwen3-Embedding-0.6B
models/Qwen3-Reranker-0.6B
```

The installer checks Python 3.11+, installs Fusion Memory in editable mode with
the full runtime extras (`.[postgres,qwen]`), and checks only those
repository-local model paths. It installs the Python runtime dependencies for
Postgres integration, local Qwen models, PyTorch, and Transformers. It does not
install or start a PostgreSQL/pgvector server, and it does not download model
weights from other locations.

If bundled model files are missing, incomplete, or Git LFS pointers,
installation reports not ready and asks you to restore the repository-local
model files. If model files are present but Qwen runtime dependencies are unavailable,
or this hardware/runtime cannot load or run both bundled vector models,
installation falls back to compromised local mode. In compromised mode Fusion
Memory still runs with SQLite plus built-in lightweight embedding/reranker, but
memory quality is compromised.

If readiness reports a Git LFS pointer instead of a real model file, install Git
LFS if needed, run `git lfs pull` in the Fusion Memory checkout, and rerun
`fusion-memory install-check --force`. Do not treat pointer files as usable model
weights.

Recommended API provider for API-backed models: Aliyun DashScope.

```bash
export DASHSCOPE_API_KEY=<your-api-key>
```

If the checkout exists and is behind main:

```bash
AGENT_DIR="/path/to/current-agent-directory"
git -C "$AGENT_DIR/fusion-memory" pull --ff-only origin main
python3 -m pip install -e "$AGENT_DIR/fusion-memory[postgres,qwen]"
fusion-memory doctor --json
```

Use the wizard only when defaults need to be overridden:

```bash
FUSION_MEMORY_USE_WIZARD=1 sh install.sh
```

## Start And Verify

Initialize the default local_full configuration:

```bash
fusion-memory init --json
```

Use local test mode only for temporary evaluation when Qwen model dependencies
or hardware are not ready: `fusion-memory init --local-test --json`.

Start the local HTTP service and use the returned URL:

```bash
fusion-memory start --json
fusion-memory status --json
fusion-memory doctor --json
```

The beginner default is `http://127.0.0.1:8700`, but if that port is busy the
start command may choose another local port. Set `PSI_MEMORY_BASE_URL` to the
returned URL before starting Haitun or psi-agent.

After verifying, immediately start the passive sync process as a long-running
process beside the agent session. Check the pid file first; if the pid is
missing or not alive, start passive sync and write the new pid. Passive sync
posts saved turns to the Fusion Memory daemon through HTTP /add, so it reuses
the daemon's loaded models and configuration:

```bash
WORKSPACE_DIR="/path/to/haitun-workspace"
SESSION_ID="<session-id>"
PASSIVE_SYNC_DIR="$WORKSPACE_DIR/.fusion-memory/haitun-history-watcher"
PASSIVE_SYNC_PID="$PASSIVE_SYNC_DIR/$SESSION_ID.pid"
mkdir -p "$PASSIVE_SYNC_DIR"

if [ -f "$PASSIVE_SYNC_PID" ] && kill -0 "$(cat "$PASSIVE_SYNC_PID")" 2>/dev/null; then
  echo "Fusion Memory passive sync already running: $(cat "$PASSIVE_SYNC_PID")"
else
  nohup fusion-memory sync-haitun-history \
    --workspace "$WORKSPACE_DIR" \
    --session-id "$SESSION_ID" \
    > "$PASSIVE_SYNC_DIR/$SESSION_ID.log" 2>&1 &
  echo $! > "$PASSIVE_SYNC_PID"
fi
```

Do not continue as if cross-session persistence is active until this process is
running and `kill -0 "$(cat "$PASSIVE_SYNC_PID")"` succeeds, or the user
explicitly chooses to continue without passive sync.

## Persistence (Required After Start)

Start or verify the passive sync process after every service start for Haitun
workspaces. The sync reads saved `histories/<session-id>.jsonl` turns and
persists conversation evidence through HTTP /add without requiring the agent to
decide every write:

```bash
WORKSPACE_DIR="/path/to/haitun-workspace"
SESSION_ID="<session-id>"
PASSIVE_SYNC_DIR="$WORKSPACE_DIR/.fusion-memory/haitun-history-watcher"
PASSIVE_SYNC_PID="$PASSIVE_SYNC_DIR/$SESSION_ID.pid"
mkdir -p "$PASSIVE_SYNC_DIR"

if [ -f "$PASSIVE_SYNC_PID" ] && kill -0 "$(cat "$PASSIVE_SYNC_PID")" 2>/dev/null; then
  echo "Fusion Memory passive sync already running: $(cat "$PASSIVE_SYNC_PID")"
else
  nohup fusion-memory sync-haitun-history \
    --workspace "$WORKSPACE_DIR" \
    --session-id "$SESSION_ID" \
    > "$PASSIVE_SYNC_DIR/$SESSION_ID.log" 2>&1 &
  echo $! > "$PASSIVE_SYNC_PID"
fi
```

For a one-time backfill:

```bash
fusion-memory sync-haitun-history \
  --workspace "$WORKSPACE_DIR" \
  --session-id "$SESSION_ID" \
  --once --json
```

automatic turn sync writes conversation evidence. memory_add is for explicit durable facts, preferences, decisions, and corrections that should be stored
immediately. Prefer passive sync for ordinary conversation history and use
`memory_add` only when the user asks to remember something or the agent needs to
persist a high-signal fact now.

## Recovery

If a memory tool returns `service_unavailable`, run:

```bash
fusion-memory status --json
fusion-memory doctor --json
fusion-memory start --json
```

If a tool returns `bad_request`, fix the reported `cause` first, such as
`missing_scope` or `missing_query`; reinstalling will not fix request-shape
errors.
