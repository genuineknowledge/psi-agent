---
name: fusion-memory-setup
description: Use before first using Fusion Memory tools, when memory tools return ok:false or service_unavailable, or when checking whether a local Fusion Memory checkout should be installed or updated.
---

# Fusion Memory Setup

Use this before the first `memory_add`, `memory_search`, or
`memory_answer_context` call in a workspace, and whenever a memory tool returns
`ok: false`.

## Check First

Always check before installing or upgrading. The default tracks fusion-memory main,
so update an existing checkout from `origin/main` only when it is
missing, behind, or the installed package points somewhere else.

```bash
fusion-memory status --json
fusion-memory doctor --json
git -C /path/to/fusion-memory status --short --branch
git -C /path/to/fusion-memory fetch origin main
git -C /path/to/fusion-memory rev-list --left-right --count HEAD...origin/main
```

Do not run installation repeatedly while these checks are still in progress.
Tell the user the estimate first: a warm status check should take under 1
minute, a normal editable install should take 2-5 minutes, and a cold dependency
install can take 10-20 minutes depending on Python wheels, model dependencies,
and network speed.

## Install Or Update

If Fusion Memory is missing:

```bash
git clone https://github.com/genuineknowledge/fusion-memory.git
cd fusion-memory
sh install.sh
```

The default production configuration is Postgres/pgvector plus bundled local
Qwen vector models:

```text
database: Postgres/pgvector using Fusion Memory's default local DSN
embedding: models/Qwen3-Embedding-0.6B
reranker: models/Qwen3-Reranker-0.6B
```

The repository includes the local vector model directories:

```text
models/Qwen3-Embedding-0.6B
models/Qwen3-Reranker-0.6B
```

The installer checks Python 3.11+, installs Fusion Memory in editable mode with
the full runtime extras (`.[postgres,qwen]`), and checks only those
repository-local model paths. It installs the Python runtime dependencies for
Postgres, local Qwen models, PyTorch, and Transformers, but it does not download
model weights from other locations.

If bundled model files are missing or dependency installation failed,
installation reports not ready and asks you to rerun
`pip install -e ".[postgres,qwen]"`. Only when model files and dependencies are
present but this hardware/runtime cannot load or run both bundled vector models
does installation fall back to compromised local mode. In compromised mode
Fusion Memory still runs with SQLite plus built-in lightweight
embedding/reranker, but memory quality is compromised.

Recommended API provider for API-backed models: Aliyun DashScope.

```bash
export DASHSCOPE_API_KEY=<your-api-key>
```

If the checkout exists and is behind main:

```bash
git -C /path/to/fusion-memory pull --ff-only origin main
python3 -m pip install -e "/path/to/fusion-memory[postgres,qwen]"
fusion-memory doctor --json
```

Use the wizard only when defaults need to be overridden:

```bash
FUSION_MEMORY_USE_WIZARD=1 sh install.sh
```

## Start And Verify

Use local test mode only for temporary evaluation when Postgres or model
dependencies are not ready:

```bash
fusion-memory init --local-test --json
```

Start the local HTTP service and use the returned URL:

```bash
fusion-memory start --json
fusion-memory status --json
fusion-memory doctor --json
```

The beginner default is `http://127.0.0.1:8700`, but if that port is busy the
start command may choose another local port. Set `PSI_MEMORY_BASE_URL` to the
returned URL before starting Haitun or psi-agent.

## Persistence Defaults

passive persistence is on by default for Haitun workspaces when the history
sync process is running beside the agent session. The sync reads saved
`histories/<session-id>.jsonl` turns and persists conversation evidence without
requiring the agent to decide every write:

```bash
fusion-memory --db fusion-memory.sqlite3 sync-haitun-history \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id>
```

For a one-time backfill:

```bash
fusion-memory --db fusion-memory.sqlite3 sync-haitun-history \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
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
