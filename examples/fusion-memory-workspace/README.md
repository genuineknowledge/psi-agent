# Fusion Memory Workspace

This example workspace lets `psi-agent` use Fusion Memory through HTTP-only
workspace tools. It does not add memory flags to the agent core and does not load
Fusion Memory model, service, or database code in the agent process.

## Tools

- `memory_add`: store durable user preferences, project facts, or stable decisions.
- `memory_search`: retrieve raw evidence by keyword.
- `memory_answer_context`: retrieve a query-grounded context pack.

## Environment

- `PSI_MEMORY_BASE_URL`: Fusion Memory HTTP server URL. Defaults to
  `http://127.0.0.1:8700`.
- `PSI_MEMORY_WORKSPACE_ID`: memory workspace scope. Defaults to `haitun`.
- `PSI_MEMORY_USER_ID`: user scope. Defaults to the current OS user or `user`.
- `PSI_MEMORY_AGENT_ID`: agent scope. Defaults to `haitun`.
- `PSI_MEMORY_SESSION_ID`: optional session scope. When unset, reads allow
  cross-session retrieval.
- `PSI_MEMORY_TIMEOUT_SECONDS`: request timeout in seconds. Defaults to `2.0` and is
  clamped to `0.1..5.0`.
- `PSI_AGENT_GATEWAY_URL`: optional psi-agent gateway URL used by automatic
  history persistence. Example: `http://127.0.0.1:8080`.
- `PSI_AGENT_WORKSPACE`: optional workspace path used by automatic history
  persistence when no gateway URL is configured.

## First Use Setup

Before using `memory_add`, `memory_search`, or `memory_answer_context` for the
first time, initialize and start Fusion Memory. The workspace includes
`skills/fusion-memory-setup/SKILL.md` with the full beginner workflow.

Minimal local setup:

```bash
git clone https://github.com/genuineknowledge/fusion-memory.git
cd fusion-memory
sh install.sh
fusion-memory init --local-test --json
fusion-memory start --json
fusion-memory doctor --json
export PSI_MEMORY_BASE_URL=http://127.0.0.1:8700
```

The Fusion Memory repository includes `models/Qwen3-Embedding-0.6B` and
`models/Qwen3-Reranker-0.6B`. The installer does not download model weights from
other locations. It installs full runtime dependencies (`.[postgres,qwen]`),
including Postgres adapter, local Qwen adapter, PyTorch, and Transformers. If
model files are missing or dependency installation failed, install-check reports
not ready and asks you to rerun `pip install -e ".[postgres,qwen]"`. Only when
model files and dependencies are present but this hardware/runtime cannot load or
run both bundled vector models does it fall back to a compromised local mode with
built-in lightweight retrieval and print the API-key next step. Recommended API
provider: Aliyun DashScope; set `DASHSCOPE_API_KEY` before configuring API-backed
providers.

## Run

Start Fusion Memory:

```bash
cd /path/to/memory
python -m fusion_memory.server --port 8700
```

Start a `psi-agent` session with this workspace:

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

The workspace tools do not automatically write every conversation turn by
themselves. By default, memory is written only when the agent calls
`memory_add`. Explicit tool writes are marked with
`metadata.write_mode="explicit_tool"` and `metadata.auto_persisted=false`.

For continuous persistence without changing psi-agent core, run the Fusion
Memory history sync process beside the agent session. It reads psi-agent history
and posts new user/assistant turns to Fusion Memory `/add`. Re-running it is
safe because it stores a local deduplication state file. Synced writes are
marked with `metadata.write_mode="history_sync"` and
`metadata.auto_persisted=true`.

If you use the psi-agent gateway, start the sync against the gateway history API:

```bash
fusion-memory sync-haitun-history \
  --gateway-url http://127.0.0.1:8080 \
  --session-id <session-id>
```

If you run a plain workspace session, sync directly from the workspace history
file:

```bash
fusion-memory sync-haitun-history \
  --workspace /path/to/fusion-memory-workspace \
  --session-id <session-id>
```

For a one-time backfill, add `--once --json`. The gateway mode requires the
gateway process to be running and the session to be registered in that gateway.
The workspace-file mode reads `histories/<session-id>.jsonl` and works after the
session has saved history to disk.

## Copy Into Another Workspace

This workspace is self-contained. To create a new memory-only workspace, copy the
whole directory:

```bash
cp -R examples/fusion-memory-workspace ./my-memory-workspace
```

Then start `psi-agent` with `--workspace ./my-memory-workspace`.

To add Fusion Memory to an existing workspace, copy the memory tools and merge
the system prompt instructions:

```bash
mkdir -p ./my-workspace/tools ./my-workspace/systems ./my-workspace/skills
cp examples/fusion-memory-workspace/tools/_client.py ./my-workspace/tools/
cp examples/fusion-memory-workspace/tools/_config.py ./my-workspace/tools/
cp examples/fusion-memory-workspace/tools/memory_*.py ./my-workspace/tools/
cp -R examples/fusion-memory-workspace/skills/fusion-memory-setup ./my-workspace/skills/
```

If the target workspace already has `systems/system.py`, add the Fusion Memory
tool guidance from this workspace's `systems/system.py` into the existing prompt.
If it does not, copy `examples/fusion-memory-workspace/systems/system.py` as-is.

## Offline Behavior

If Fusion Memory is offline or returns an error, the tools return a short JSON
message and the agent can continue without memory:

```json
{"ok": false, "message": "Fusion Memory is not available. Continue without memory, then run fusion-memory doctor."}
```
