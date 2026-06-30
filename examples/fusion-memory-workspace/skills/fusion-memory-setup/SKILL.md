---
name: fusion-memory-setup
description: Use before the first use of Fusion Memory tools to initialize, start, and check the Fusion Memory service.
---

# Fusion Memory First-Use Setup

Use this skill before the first use of Fusion Memory in a workspace, or when
`memory_add`, `memory_search`, or `memory_answer_context` reports that Fusion
Memory is unavailable.

## 1. Download Fusion Memory

If Fusion Memory is not already on this machine, download it:

```bash
git clone https://github.com/genuineknowledge/fusion-memory.git
cd fusion-memory
```

## 2. Install

For a beginner local setup, run:

```bash
sh install.sh
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

If bundled model files are missing or dependency installation failed, installation
reports not ready and asks you to rerun `pip install -e ".[postgres,qwen]"`.
Only when model files and dependencies are present but this hardware/runtime
cannot load or run both bundled vector models does installation fall back to
compromised local mode. In compromised mode Fusion Memory still runs with SQLite
plus built-in lightweight embedding/reranker, but memory quality is compromised.
After install, provide an API key before configuring API-backed providers.
Recommended provider: Aliyun DashScope.

```bash
export DASHSCOPE_API_KEY=<your-api-key>
```

Use a manual configuration wizard only when you need to override the defaults:

```bash
FUSION_MEMORY_USE_WIZARD=1 sh install.sh
```

## 3. Initialize Local Test Mode

If Postgres or model dependencies are not ready, use local test mode:

```bash
fusion-memory init --local-test --json
```

Local test mode uses SQLite and built-in lightweight models. It is suitable for
trying the workspace integration before production dependencies are configured.

## 4. Start and Check the Service

Start the local HTTP service:

```bash
fusion-memory start --json
```

Then check readiness:

```bash
fusion-memory doctor --json
fusion-memory status --json
```

The default beginner endpoint is:

```bash
export PSI_MEMORY_BASE_URL=http://127.0.0.1:8700
```

Set that environment variable before starting `psi-agent` with the Fusion Memory
workspace. If port `8700` is already in use, `fusion-memory start --json` tries
the next available local port and returns the actual `url`; set
`PSI_MEMORY_BASE_URL` to that returned URL.

## 5. Recover

If memory tools still return the unavailable message:

```bash
fusion-memory doctor
fusion-memory start
```

Check for a port mismatch. The workspace and beginner Fusion Memory CLI both
default to `PSI_MEMORY_BASE_URL=http://127.0.0.1:8700`.

## 6. Optional Automatic History Persistence

The workspace memory tools do not automatically write every conversation turn.
They write only when the agent calls `memory_add`. To persist Haitun/psi-agent
history continuously without changing agent core, run the Fusion Memory history
sync process beside the agent session.

If using psi-agent gateway:

```bash
fusion-memory sync-haitun-history \
  --gateway-url http://127.0.0.1:8080 \
  --session-id <session-id>
```

If using a plain workspace session:

```bash
fusion-memory sync-haitun-history \
  --workspace /path/to/fusion-memory-workspace \
  --session-id <session-id>
```

For a one-time backfill:

```bash
fusion-memory sync-haitun-history \
  --workspace /path/to/fusion-memory-workspace \
  --session-id <session-id> \
  --once --json
```

The sync command reads only user/assistant turns, writes them to Fusion Memory
`/add`, and records a local state file so repeated runs do not duplicate writes.
