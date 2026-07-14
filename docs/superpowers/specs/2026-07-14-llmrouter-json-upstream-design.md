# LLMRouter JSON Upstream Integration Design

Date: 2026-07-14

## Status and supersession

This specification supersedes
`docs/superpowers/specs/2026-07-13-llmrouter-upstream-routing-design.md` for the
first production integration. The earlier document remains as design history;
this document defines the implementation contract.

## Goal

Integrate `llmrouter-lib==0.3.1` directly into psi-agent's AI router. One
user-configured remote routing model reads bounded conversation context and
uses `LLMMultiRoundRouter._decompose_and_route()` to score candidates. Candidate
AI services are supplied as one JSON array through `--upstream`, including each
candidate's address, model identifier, and human-authored description. The
winning candidate receives the original OpenAI Chat Completions request through
psi-agent's existing HTTP/SSE proxy.

## Non-goals

- Preserve the current semantic-router implementation or dependency.
- Preserve `--route-model` or the old multiple-address `--upstream` syntax.
- Preserve `semantic`, `difficulty`, `first`, `last`, or `round_robin` policies.
- Let LLMRouter execute candidate requests or aggregate final answers.
- Send tool-result bodies, binary content, credentials, or upstream addresses
  to the routing model.
- Retry routing, call the runner-up, or choose a fallback based on failure type.
- Support different routing-model credentials concurrently in one process.

## Architecture

```text
Channel
   |
   v
Session -- POST /chat/completions --> AiRouter
                                      |
                                      | bounded context
                                      v
                              LLMRouterAdapter
                                      |
                    LLMMultiRoundRouter._decompose_and_route()
                                      |
                         [(subquery, model), ...]
                                      |
                         whitelist + majority vote
                                      |
                                      v
                              selected upstream
                                      |
                         original request + SSE
                                      v
                              Session / Channel
```

`AiRouter` owns the aiohttp service, explicit-model override, fallback, and SSE
proxy. `LLMRouterAdapter` owns JSON parsing, context serialization, runtime
LLMRouter files, LLMRouter construction, synchronous invocation, validation,
and majority voting.

## Files

Create:

```text
src/psi_agent/ai/llmrouter_adapter.py
tests/psi_agent/ai/test_llmrouter_adapter.py
```

Modify:

```text
src/psi_agent/ai/router.py
src/psi_agent/ai/__init__.py
src/psi_agent/cli.py
tests/psi_agent/ai/test_router.py
pyproject.toml
uv.lock
AGENTS.md
src/psi_agent/ai/AGENTS.md
README.md
README_en.md
```

Remove obsolete semantic-router-specific tests, including
`tests/psi_agent/ai/test_router_semantic.py` when present.

## CLI contract

The command remains:

```powershell
psi-agent ai router
```

The router model uses separate fields:

```text
--router-model
--router-base-url
--router-api-key
```

Candidates use one JSON array passed to `--upstream`:

```powershell
uv run psi-agent ai router `
  --session-socket .\router.sock `
  --router-model qwen-turbo `
  --router-base-url https://router.example.com/v1 `
  --router-api-key sk-router `
  --default-model qwen-plus `
  --upstream '[
    {
      "addr": ".\\qwen.sock",
      "model": "qwen-plus",
      "description": "适合通用中文问答、总结和文本生成"
    },
    {
      "addr": ".\\deepseek.sock",
      "model": "deepseek-reasoner",
      "description": "适合复杂推理、数学、算法和代码分析"
    }
  ]'
```

`--route-model` and `--policy` are removed. The old multiple-address upstream
syntax is not accepted.

## AiRouter configuration

```python
@dataclass
class AiRouter:
    session_socket: str
    router_model: str
    router_base_url: str
    router_api_key: str
    upstream: str
    default_model: str = ""
    router_timeout: float | None = None
    router_context_chars: int = 12_000
    log_router_details: bool = False
    verbose: bool = False
```

Router-model CLI values take precedence over these environment fallbacks:

```text
PSI_ROUTER_MODEL
PSI_ROUTER_BASE_URL
PSI_ROUTER_API_KEY
```

An empty API key is allowed for an unauthenticated local OpenAI-compatible
service. Logs report only `set` or `empty`.

## Upstream JSON schema

Each array item maps to:

```python
@dataclass(frozen=True)
class RouteTarget:
    addr: str
    model: str
    description: str
```

Validation is strict:

- The input must parse as JSON.
- The root must be a non-empty list.
- Every item must be a dictionary containing exactly `addr`, `model`, and
  `description`.
- Each value must be a non-empty string after trimming whitespace.
- Model identifiers must be unique.
- Candidate API keys are forbidden; each upstream AI service owns its provider
  credentials.
- Unknown fields are rejected to catch misspellings during startup.
- Every value obtained from JSON is guarded with `isinstance` before access.

Addresses continue to support transports recognized by
`psi_agent._sockets`: Unix socket paths, TCP URLs, and Windows named pipes.

## Dependency policy

Add the direct runtime dependency:

```toml
"llmrouter-lib==0.3.1",
```

Remove:

```toml
"semantic-router",
```

This is not an optional extra or sidecar. `uv lock` and `uv sync` must resolve
the complete dependency graph on the project's Python 3.14 runtime. Failure to
resolve, import, build, or package the dependency is an implementation blocker;
the implementation must not copy selected upstream sources or skip required
dependencies while claiming official-library integration.

The direct dependency is expected to increase environment and desktop-package
size because it includes Torch, Transformers, pandas, scikit-learn, Gradio,
LiteLLM, PEFT, torch-geometric, and related packages. Verification includes
`uv build` and review of the Windows/PyInstaller packaging path.

## Adapter lifecycle

`AiRouter.run()` keeps `setup_logging(verbose=self.verbose)` as its first
executable line, then resolves environment fallbacks, parses candidates,
validates configuration, constructs the adapter, and starts the aiohttp server.

The adapter starts once per router service:

1. Create a managed runtime directory using asynchronous filesystem APIs.
2. Write `llm_data.json` using UTF-8.
3. Write `runtime.yaml` using an absolute candidate-data path.
4. Construct one `LLMMultiRoundRouter` in an anyio worker thread.
5. Reuse that instance for requests until shutdown.

It does not construct LLMRouter or write configuration per request.

The adapter exposes:

```python
class LLMRouterAdapter:
    async def start(self) -> None: ...
    async def route(self, context: str) -> RouteDecision: ...
    async def close(self) -> None: ...
```

Startup failure and shutdown clean the runtime directory. Cleanup across awaits
is shielded. The adapter tracks active abandoned workers and waits for them
before deleting runtime files.

## LLMRouter runtime files

`llm_data.json` contains only candidate answer models:

```json
{
  "qwen-plus": {
    "feature": "适合通用中文问答、总结和文本生成",
    "model": "qwen-plus"
  },
  "deepseek-reasoner": {
    "feature": "适合复杂推理、数学、算法和代码分析",
    "model": "deepseek-reasoner"
  }
}
```

It excludes the routing model, addresses, and credentials. The runtime YAML is:

```yaml
data_path:
  llm_data: "C:/absolute/runtime/path/llm_data.json"
base_model: "qwen-turbo"
use_local_llm: false
api_endpoint: "https://router.example.com/v1"
```

The routing model is not a candidate and cannot be returned as an upstream.

psi-agent runtime-file operations use `anyio.Path`. Synchronous LLMRouter
construction and inference run in worker threads. psi-agent does not introduce
native asyncio, synchronous pathlib IO inside async functions, or blocking
network calls on the event loop.

## Routing context

The serialized routing context contains:

- The first system message.
- Recent user and assistant messages selected from the end.
- The latest user message even when older context must be discarded.
- Tool names and a marker indicating omitted tool results.
- Type markers for image, audio, file, and other non-text content.

It excludes tool-result bodies, tool-call argument bodies, binary/base64 data,
addresses, URLs, and API keys.

`router_context_chars` must be a positive integer. Messages are selected from
the tail within the budget. If the newest user message alone exceeds the
budget, its leading portion is retained with an explicit truncation marker. A
model-specific tokenizer is not introduced in this phase.

If no usable user context exists, LLMRouter is skipped and fallback is used.

## LLMRouter invocation and global credentials

The integration uses the verified private synchronous method:

```python
LLMMultiRoundRouter._decompose_and_route(context)
```

The dependency is pinned because this API has no compatibility guarantee. A
contract test verifies the method exists and returns the expected list-of-pairs
shape.

LLMRouter reads routing credentials from process-global `API_KEYS`. A
module-level `threading.Lock` serializes all LLMRouter calls across adapter
instances. Environment mutation and restoration happen entirely inside the
worker-thread wrapper:

```text
acquire process-wide synchronous lock
save API_KEYS
set the adapter key
call _decompose_and_route
restore or remove API_KEYS in finally
release lock
```

The async caller uses `anyio.to_thread.run_sync(..., abandon_on_cancel=True)`.
After timeout or cancellation, the worker may continue, but it retains the
synchronous lock and restores the environment itself. A later worker cannot
overwrite its credentials.

One process supports only one distinct
`router_model + router_base_url + router_api_key` configuration. Identical
configurations may coexist; a conflicting configuration fails at startup. This
also guards against third-party module-level client caches binding the first
endpoint or credential.

## Timeout and cancellation

`router_timeout` accepts `None` or a finite positive number:

- Omitted/`None`: psi-agent imposes no routing deadline.
- Positive number: stop awaiting after that many seconds and return fallback.
- Zero, negative, NaN, and infinity: startup error.

Timeout and user cancellation abandon the async wait but cannot forcibly stop
the synchronous LLMRouter thread. The remote call may continue and consume
tokens. The thread retains its lock, key, runtime files, and active-call record
until completion. This limitation is documented and tested.

## Route validation and majority vote

The raw result must be a list. Each accepted item must be a two-element tuple or
list containing a string subquery and a string model that exactly matches a
configured candidate. Malformed or unknown items are ignored with safe logging.
No fuzzy model-name matching is used.

Votes count valid model occurrences. The highest count wins. A tie is resolved
by first appearance in the valid result sequence. Descriptions, model order,
size, and cost are not secondary scores. Only the winner receives the original
request.

Internal output:

```python
@dataclass(frozen=True)
class RouteDecision:
    target: RouteTarget
    routes: tuple[tuple[str, str], ...]
    votes: dict[str, int]
    source: str
```

Allowed sources are `request_model`, `llmrouter_majority`,
`fallback_default`, and `fallback_first`.

## Explicit model and fallback

An incoming `body.model` that exactly matches a candidate bypasses LLMRouter and
selects that upstream. An unknown model cannot bypass the whitelist and proceeds
through automatic routing.

Every recoverable routing failure uses one deterministic fallback:

```text
configured default_model -> matching candidate
no default_model          -> first upstream item
```

Recoverable failures include missing context, timeout, LLMRouter exception,
invalid result shape, and no valid candidate. There is no retry, runner-up,
random choice, round robin, or difficulty policy.

The forwarded request model is replaced with the selected candidate model.
Internal `routing` metadata is removed. All other unknown Chat Completions
fields are preserved.

## SSE proxy

After selection, `router.py` continues to use
`resolve_connector_and_endpoint(target.addr)` and aiohttp context managers.
Content, reasoning, tool calls, finish reasons, and `[DONE]` pass through.
Every SSE chunk is logged at DEBUG. Non-200 upstream responses use the existing
OpenAI-style error JSON. Failures after response preparation use
`finish_reason="error"`. Downstream disconnect and cancellation release the
upstream response and connector.

Routes, votes, and routing prompts are not inserted into Session history or
final model content.

## Logging

psi-agent uses loguru, not `print()`:

- INFO: lifecycle and final model/address/source.
- DEBUG: lock acquire/release, votes, request completion, and every SSE chunk.
- WARNING: timeout, invalid route items, LLMRouter exception, and fallback.
- ERROR: startup and selected-upstream failures.

Raw subqueries are logged only when `log_router_details` is true. Each subquery,
route count, and total serialized log length are bounded. API keys, full
conversation content, tool-result bodies, and full LLMRouter prompts are never
logged. Third-party LLMRouter `print()` output is not captured through global
stdout redirection because that is unsafe in a multithreaded process.

## Tests

### Adapter unit tests

- Parse valid single and multiple JSON candidates.
- Preserve Chinese descriptions and Windows paths.
- Reject invalid JSON, non-list roots, empty lists, non-dict entries, missing or
  blank fields, duplicate models, unknown fields, and candidate API keys.
- Serialize bounded system/user/assistant context.
- Omit tool results and arguments while retaining tool names and type markers.
- Guard arbitrary JSON shapes before dict/list access.
- Generate absolute-path runtime YAML and candidate-only JSON without secrets.
- Construct LLMRouter once and reject a missing private API.
- Validate result shape, whitelist models, count votes, and break ties by first
  valid appearance.
- Restore `API_KEYS` on success and exception.
- Prove process-wide serialization across two fake adapters.
- Prove timeout/cancellation returns or propagates while the worker retains the
  lock and later restores the environment.
- Wait for active workers before runtime-directory cleanup.

### Router integration tests

- Select each candidate and proxy only to the winner.
- Bypass LLMRouter for a whitelisted request model.
- Route an unknown request model automatically.
- Use explicit default or first candidate for every recoverable failure.
- Preserve messages, tools, sampling fields, and unknown request fields.
- Remove internal routing metadata and set the selected model.
- Proxy content, reasoning, tool calls, finish reasons, and `[DONE]`.
- Propagate non-stream and stream errors correctly.
- Close the selected upstream on downstream disconnect.

### CLI and dependency tests

- `psi-agent ai router --help` exposes the new fields and omits `route-model`
  and `policy`.
- PowerShell JSON input handles backslashes, Unicode, colons, and URLs.
- The pinned LLMRouter class and private method import successfully.
- Ordinary AI behavior remains covered after the direct dependency change.

## Documentation and migration

Update root and AI-layer AGENTS documents, both READMEs, and relevant docs. They
must describe the new JSON schema, removal of old syntax/policies, one router
model, candidate descriptions, majority and tie rules, explicit-model override,
fallback, timeout, process-global credential serialization, private API pin,
and abandoned-worker limitation.

Delete the current semantic-router dynamic imports, encoder, vector logic,
caches, heuristic capability inference, policy state, route-model parser, and
direct candidate credentials. Retain and simplify the HTTP/SSE error helpers,
transport resolution, proxy lifecycle, and `AiRouter` entry point.

## Verification

```powershell
uv lock
uv sync
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest -v
uv run psi-agent ai router --help
uv build
git diff --check
```

Review the Windows desktop/PyInstaller path and build-size impact of the direct
ML dependency. Confirm unrelated pre-existing working-tree changes remain
untouched.
