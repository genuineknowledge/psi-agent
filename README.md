# psi-agent

A microkernel-style agent framework. A workspace defines the agent:
system prompt, tools, skills, schedules, and memory live in ordinary files.

## Requirements

- Python 3.14 or newer
- uv
- A model API key for the backend you choose

## Quick Start

From a cloned checkout:

```bash
uv sync
uv run psi-agent init
uv run psi-agent doctor
uv run psi-agent run --message "Summarize what you can do in one sentence"
```

`psi-agent init` creates:

```text
~/.psi-agent/config.toml
~/.psi-agent/workspaces/default/
```

Set the API key environment variable shown by `psi-agent init`.

macOS or Linux:

```bash
export OPENAI_API_KEY="your-key"
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-key"
```

Use Anthropic defaults instead:

```bash
uv run psi-agent init --ai anthropic-messages
```

## Upgrade

From a cloned checkout:

```bash
git pull --ff-only
uv sync
uv run psi-agent doctor
```

If the upgrade fails, keep the existing checkout and run `uv run psi-agent doctor`.
The command reports the next step without printing API key values.

## One-Shot Run

Use this for external callers such as Fusion Flow:

```bash
uv run psi-agent run \
  --workspace examples/a-simple-bash-only-workspace \
  --profile fusion \
  --message "Summarize what you can do in one sentence"
```

If `--workspace` is omitted, `psi-agent run` reads `default_workspace` from
`~/.psi-agent/config.toml`.

Example config:

```toml
config_version = 1
default_profile = "fusion"
default_workspace = "/absolute/path/to/workspace"

[profiles.fusion]
ai = "openai-completions"      # or "anthropic-messages"
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

Prefer `api_key_env` over storing `api_key` directly in the config file.

## Interactive Mode

Three-process mode is useful when you want a persistent session.

Terminal 1:

```bash
uv run psi-agent ai openai-completions \
  --session-socket ./ai.sock \
  --model gpt-4o-mini \
  --api-key "$OPENAI_API_KEY" \
  --base-url https://api.openai.com/v1
```

Terminal 2:

```bash
uv run psi-agent session \
  --workspace examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock
```

Terminal 3:

```bash
uv run psi-agent channel repl --session-socket ./channel.sock
```

REPL controls:

- `Enter`: new line
- `Alt+Enter`: send
- `Ctrl+D`: exit

## Fusion Flow Workspace

`examples/fusion-flow-workspace` packages Fusion Flow as a psi-agent skill.
Use it when a natural-language request should create or run a multi-agent
`.flow.ts` workflow.

```bash
uv run psi-agent run \
  --workspace examples/fusion-flow-workspace \
  --profile fusion \
  --message "Build a parallel code-review workflow with security, performance, and readability reviewers."
```

Fusion Flow files are separated by responsibility:

```text
skills/fusion-flow/                 # immutable skill bundle and runtime
flows/<task-slug>/<task-slug>.flow.ts
flows/<task-slug>/runs/<run-id>/     # meta.json, execution-graph.json, bindings/, trace/
```

For `FLOW_ENGINE=psi`, keep provider URLs and API keys in psi-agent profile
config, not in Fusion Flow `.env` files.

## Workspace Layout

```text
my-workspace/
|-- systems/
|   `-- system.py          # async def system_prompt_builder() -> str
|-- tools/
|   `-- bash.py            # one async function per tool file
|-- skills/
|   `-- example/SKILL.md   # skill instructions loaded by the workspace prompt
|-- schedules/
|   `-- daily/TASK.md      # optional cron task
`-- memory.md              # optional workspace memory
```

Tool example:

```python
import anyio


async def bash(command: str) -> str:
    """Execute a shell command."""
    result = await anyio.run_process(["bash", "-lc", command])
    return result.stdout.decode().strip()
```

## Troubleshooting

Run:

```bash
uv run psi-agent doctor
```

Common messages:

```text
API key: missing; set OPENAI_API_KEY
```

Set the environment variable named in the output.

```text
Workspace not found
```

Run `uv run psi-agent init`, or pass `--workspace PATH`.

```text
psi-agent config is not valid TOML
```

Check quotes and `[profiles.<name>]` table headers in `~/.psi-agent/config.toml`.

```text
Cannot connect to the model service
```

Check `base_url`, network access, and whether the provider is reachable.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check .
uv run pytest -v
```

Real API tests are disabled by default:

```bash
PSI_RUN_REAL_API_TESTS=1 uv run pytest tests/integration/test_real_api.py -v
```

## License

GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE.md).
