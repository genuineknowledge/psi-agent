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

On Windows PowerShell, use the same target directory rule but run the PowerShell
installer. It creates a dedicated `.fusion-memory-venv` for Memory instead of
using the agent's MSYS2 Python environment:

```powershell
$env:AGENT_DIR = "C:\path\to\current-agent-directory"
git clone https://github.com/genuineknowledge/fusion-memory.git "$env:AGENT_DIR\fusion-memory"
Set-Location "$env:AGENT_DIR\fusion-memory"
.\install.ps1
```

Do not use MSYS2/Mingw Python for the full local Qwen runtime on Windows;
PyTorch wheels are not available for that Python ABI. MSYS2 Python may only be
used to bootstrap the installer. Do not ask the user to manually install Python
or Git LFS. If compatible Windows CPython is unavailable, `install.ps1`
downloads a local `uv.exe` non-interactively and creates a managed
`.fusion-memory-venv` for Memory.

The default local_full configuration is SQLite plus local Qwen vector models:

```text
database: SQLite using Fusion Memory's default local database path
embedding: models/Qwen3-Embedding-0.6B
reranker: models/Qwen3-Reranker-0.6B
```

Postgres/pgvector is optional for production deployments that need pgvector
indexes, larger datasets, or multi-user service storage. It is not required for
the default local setup.

The installer checks Python 3.11+, installs Fusion Memory in editable mode,
downloads the two Qwen model directories from ModelScope into `models/`, and
installs the Python runtime dependencies for Postgres integration, local Qwen
models, PyTorch, and Transformers. It does not install or start a PostgreSQL/pgvector server.

On Windows, `install.ps1` performs the same full-runtime readiness check through
the dedicated `.fusion-memory-venv`; the local Qwen dependency step is
wheel-only and stops on failure instead of compiling from source or falling back
to `local_test`.

If model files are missing, incomplete, or Git LFS pointers, installation
downloads the real model weights from ModelScope. If model download or Qwen runtime dependency installation fails, installation is not ready; do not report
setup as complete and do not fall back to `local_test`. Only when model files
and dependencies are present but this hardware/runtime cannot load or run both
local vector models does installation fall back to compromised local mode. In
compromised mode Fusion Memory still runs with SQLite plus built-in lightweight
embedding/reranker, but memory quality is compromised.

If install-check returns not_ready, make one explicit repair attempt by rerunning
the installer before stopping:

```bash
AGENT_DIR="/path/to/current-agent-directory"
cd "$AGENT_DIR/fusion-memory"
sh install.sh
```

On Windows PowerShell:

```powershell
$env:AGENT_DIR = "C:\path\to\current-agent-directory"
Set-Location "$env:AGENT_DIR\fusion-memory"
.\install.ps1
```

If the repair attempt still reports not_ready, summarize the pip error and the
install-check `missing` / `next_step` fields for the user. Do not paste full pip logs or model download logs into chat; give the concise failure step and log
path. Do not silently fall back to local_test or report setup as complete.
`local_test` is allowed only when the user explicitly chooses temporary
evaluation after being told memory quality is downgraded.

Recommended API provider for API-backed models: Aliyun DashScope.

```bash
export DASHSCOPE_API_KEY=<your-api-key>
```

If the checkout exists and is behind main:

```bash
AGENT_DIR="/path/to/current-agent-directory"
git -C "$AGENT_DIR/fusion-memory" pull --ff-only origin main
sh "$AGENT_DIR/fusion-memory/install.sh"
fusion-memory doctor --json
```

On Windows after pulling, rerun the installer and use the venv command it
created:

```powershell
$env:AGENT_DIR = "C:\path\to\current-agent-directory"
git -C "$env:AGENT_DIR\fusion-memory" pull --ff-only origin main
Set-Location "$env:AGENT_DIR\fusion-memory"
.\install.ps1
$FusionMemory = "$env:AGENT_DIR\fusion-memory\.fusion-memory-venv\Scripts\fusion-memory.exe"
& $FusionMemory doctor --json
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

Use local test mode only for temporary evaluation when Qwen model dependencies,
model files, or hardware are not ready: `fusion-memory init --local-test --json`.
Tell the user this is a quality downgrade and not a completed full-memory
installation.

Start the local HTTP service and use the returned URL:

```bash
fusion-memory start --json
fusion-memory status --json
fusion-memory doctor --json
```

On Windows PowerShell, use the CLI from the Memory venv:

```powershell
$FusionMemory = "$env:AGENT_DIR\fusion-memory\.fusion-memory-venv\Scripts\fusion-memory.exe"
& $FusionMemory start --json
& $FusionMemory status --json
& $FusionMemory doctor --json
```

The beginner default is `http://127.0.0.1:8700`, but if that port is busy the
start command may choose another local port. Set `PSI_MEMORY_BASE_URL` to the
returned URL before starting Haitun or psi-agent.

After verifying, immediately start the passive sync process as a background
process beside the agent session. Do not run the long-running sync in the
foreground tool call. Use `sync-haitun-history --once` only for a finite
backfill check, or use `sync-haitun-history --background` / the watcher start
command for the long-running watcher.

```bash
fusion-memory status --json
fusion-memory doctor --json
fusion-memory sync-haitun-history \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --memory-url <url-from-fusion-memory-start-or-status> \
  --once --json
fusion-memory sync-haitun-history \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --memory-url <url-from-fusion-memory-start-or-status> \
  --background --json
fusion-memory status-haitun-history-watcher \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --memory-url <url-from-fusion-memory-start-or-status> \
  --json
```

Do not continue as if cross-session persistence is active until this process is
reported as running with an OS pid, pid_file, and log_file, or the user
explicitly chooses to continue without passive sync. On Windows, do not use
PowerShell job/process wrappers to manage this watcher; the Fusion Memory CLI
writes the real OS pid itself. On Linux/macOS, do not hand-write shell
backgrounding; use the same CLI background command.

## Persistence (Required After Start)

Start or verify the passive sync process after every service start for Haitun
workspaces. The sync reads saved `histories/<session-id>.jsonl` turns and
persists conversation evidence through HTTP /add without requiring the agent to
decide every write. Never run the long-running watcher in the foreground tool
call:

```bash
fusion-memory sync-haitun-history \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --memory-url <url-from-fusion-memory-start-or-status> \
  --background --json
fusion-memory status-haitun-history-watcher \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --memory-url <url-from-fusion-memory-start-or-status> \
  --json
```

For a one-time backfill:

```bash
fusion-memory sync-haitun-history \
  --workspace /path/to/haitun-workspace \
  --session-id <session-id> \
  --memory-url <url-from-fusion-memory-start-or-status> \
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
