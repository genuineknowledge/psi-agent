# LLMRouter Upstream Routing Design

Date: 2026-07-13

## Summary

Add an experimental AI router that uses `ulab-uiuc/LLMRouter`'s
`LLMMultiRoundRouter` routing stage to choose one of psi-agent's existing AI
upstreams. The router runs in the same Python environment and process as
psi-agent. It preserves psi-agent's OpenAI-compatible HTTP/SSE boundary:
LLMRouter makes the routing decision, while the selected upstream continues to
generate the actual response, tool calls, and reasoning stream.

This first version intentionally calls
`LLMMultiRoundRouter._decompose_and_route()`, pins `llmrouter-lib==0.3.1`, and
collapses its potentially multi-model result to one upstream by majority vote.
It is an experiment to evaluate routing quality and latency. A later version
may instead use `LLMMultiRoundRouter.route_single()` as a complete
decompose-route-execute-aggregate backend.

## Goals

- Use the actual `ulab-uiuc/LLMRouter` library rather than only imitating its
  prompting strategy.
- Configure exactly one remote routing model with a model name, API key, and
  OpenAI-compatible base URL.
- Continue to define answer-producing candidates as parallel `upstream` and
  `route_model` lists.
- Include bounded conversation context in the routing input.
- Select exactly one upstream for each original Chat Completions request.
- Preserve the original request body, SSE stream, reasoning, tool calls, and
  Session agent loop after routing.
- Produce observable routing results without exposing sensitive conversation
  content by default.
- Fall back deterministically to `default_model`, or to `upstream[0]` when no
  default is configured.

## Non-goals

- Training or fine-tuning an LLMRouter model.
- Loading a local Hugging Face or vLLM routing model.
- Sending tool result bodies to the routing model.
- Executing subqueries or aggregating answers inside LLMRouter in this phase.
- Supporting multiple routing-model credentials in one process.
- Retrying a failed routing decision.
- Caching or persisting routing decisions.
- User-authored candidate descriptions in the first version.
- Maintaining compatibility with the current semantic-router demo.

## Verified LLMRouter API

The design is based on `ulab-uiuc/LLMRouter` `main` as inspected on
2026-07-13. Its package is published as `llmrouter-lib==0.3.1`.

`LLMMultiRoundRouter` is constructed from a YAML file:

```python
from llmrouter.models import LLMMultiRoundRouter

router = LLMMultiRoundRouter(yaml_path=config_path)
```

Its public `route_single()` method runs a complete pipeline:

1. Decompose and route the query.
2. Execute each subquery against its selected model.
3. Aggregate the subresponses.
4. Return the final answer.

That public method does not fit this phase because psi-agent must continue to
proxy the original request to one upstream. The routing-only behavior currently
exists as the private synchronous method:

```python
routes = router._decompose_and_route(query)
```

Its effective return type is:

```python
list[tuple[str, str]]  # [(subquery, model_name), ...]
```

Because this is private API, the integration pins exactly version `0.3.1` and
adds contract tests around its input and output. Upgrading the dependency
requires an explicit compatibility review.

## Architecture

```text
Channel
   |
   v
Session
   | POST /chat/completions
   v
AiRouter
   | derive bounded routing context
   v
LLMMultiRoundRouter._decompose_and_route()  [worker thread]
   | [(subquery, model), ...]
   v
validate candidates + majority vote
   | selected route_model[i]
   v
proxy the original request to upstream[i]
   | OpenAI-compatible SSE
   v
Session / Channel
```

The components have the following responsibilities:

- `AiRouter`: lifecycle, configuration validation, request orchestration,
  fallback, and upstream SSE proxying.
- `LLMRouterAdapter`: runtime-file creation, LLMRouter construction, synchronous
  routing invocation, result normalization, and majority voting.
- Context serializer: bounded conversion from Chat Completions messages into
  the text consumed by LLMRouter.
- Capability builder: automatic candidate metadata derived from model names.

The implementation should split the current oversized `ai/router.py` so these
units can be tested independently. A suitable initial layout is:

```text
src/psi_agent/ai/
|-- router.py
|-- llmrouter_adapter.py
`-- router_capabilities.py
```

## Single-environment deployment

The LLMRouter package runs in the same environment and process as psi-agent.
It is an optional dependency so users who do not use this policy do not install
its machine-learning stack:

```toml
[project.optional-dependencies]
llmrouter = [
    "llmrouter-lib==0.3.1",
]
```

The development environment installs it with:

```powershell
uv sync --extra llmrouter
```

Imports must be lazy. Starting ordinary `psi-agent ai` must not fail when the
extra is absent. Starting the LLMRouter policy without the extra fails during
startup with a precise installation message.

### Python 3.14 compatibility gate

psi-agent requires Python 3.14 or newer. LLMRouter declares Python 3.10 or
newer, but its published classifiers list 3.10 and 3.11 and it depends on
Torch, Transformers, torch-geometric, and other compiled packages. Therefore
installation and import are hard implementation gates:

```powershell
uv sync --extra llmrouter
uv run python -c "from llmrouter.models import LLMMultiRoundRouter; print(LLMMultiRoundRouter)"
```

If the dependency graph cannot install and import on the project's Python
3.14 runtime, the single-environment design is blocked. The implementation
must not copy selected source files or silently omit dependencies while
claiming official-library integration. The alternatives would be broadening
psi-agent's supported Python versions or revisiting process isolation.

## Configuration

`AiRouter` exposes the following routing configuration:

```python
@dataclass
class AiRouter:
    session_socket: str
    upstream: list[str] = field(default_factory=list)
    route_model: list[str] = field(default_factory=list)
    router_model: str = ""
    router_api_key: str = ""
    router_base_url: str = ""
    default_model: str = ""
    router_timeout: float | None = None
    router_context_chars: int = 12_000
    log_router_details: bool = False
    policy: str = "llmrouter"
    verbose: bool = False
```

There is exactly one remote routing model. Candidate answer models remain
upstreams:

```text
route_model[i] <-> upstream[i]
```

Example CLI shape:

```powershell
uv run psi-agent ai router `
  --session-socket .\router.sock `
  --router-model qwen-turbo `
  --router-api-key sk-router `
  --router-base-url https://router.example.com/v1 `
  --default-model qwen-plus `
  --upstream .\qwen.sock .\deepseek.sock .\coder.sock `
  --route-model qwen-plus deepseek-reasoner qwen-coder
```

Secrets may use environment-variable fallbacks. Logs report only whether a key
is set; they never contain the key value.

### Startup validation

After `setup_logging(verbose=self.verbose)`, which remains the first executable
line of `run()`, validate:

- At least one non-empty upstream exists.
- At least one non-empty route model exists.
- `upstream` and `route_model` have equal lengths.
- Route model names are unique.
- `router_model` and `router_base_url` are present for the LLMRouter policy.
- An empty router API key is allowed for unauthenticated local-compatible
  endpoints.
- A non-empty `default_model` matches a configured route model exactly.
- `router_timeout` is `None` or a finite positive number.
- `router_context_chars` is positive.
- `llmrouter-lib==0.3.1` is importable.

## LLMRouter runtime configuration

`LLMMultiRoundRouter` only accepts a YAML path, and `MetaRouter` loads candidate
metadata from the file referenced by `data_path.llm_data`. At router startup,
the adapter creates a runtime directory containing:

```text
router-runtime/
|-- router.yaml
`-- llm_data.json
```

The files are written asynchronously before LLMRouter is constructed in a
worker thread. Runtime paths are absolute because LLMRouter otherwise resolves
relative data paths against its installed package root.

The YAML has the minimum required form:

```yaml
data_path:
  llm_data: "<absolute path to llm_data.json>"
base_model: "<router_model>"
use_local_llm: false
api_endpoint: "<router_base_url>"
```

Candidate metadata is generated from `route_model` names. It follows
LLMRouter's verified `llm_data` structure while omitting candidate API details,
because psi-agent upstreams own those details:

```json
{
  "qwen-plus": {
    "feature": "General chat, general question answering, and Chinese language tasks.",
    "model": "qwen-plus"
  },
  "deepseek-reasoner": {
    "feature": "Reasoning, code analysis, debugging, and mathematics.",
    "model": "deepseek-reasoner"
  }
}
```

Unknown names receive a stable general-purpose description. A later version
may add explicit user descriptions with the precedence:

```text
user description > generated description > generic description
```

LLMRouter's remote API helper obtains credentials through the process-wide
`API_KEYS` environment variable. In this experimental version, one
LLMRouter-policy credential is allowed per process. Startup detects conflicting
instances/configurations rather than silently swapping the global key. A future
upstream contribution should add explicit client or credential injection.

## Routing context

Routing includes conversation context, not only the latest user message. The
serializer produces bounded text containing:

- The system message.
- The most recent user and assistant messages, selected from the end within a
  configurable character budget.
- The latest user message even when older context must be discarded.
- Tool names and a marker that tool results exist.
- Markers for image, audio, file, or other non-text content.

It does not include:

- Tool-result bodies.
- Tool-call argument bodies.
- Binary or base64 content.
- API keys, URLs, or upstream socket paths.

Example:

```text
[SYSTEM]
You are assisting with a Python agent framework.

[USER]
The service leaks an upstream stream after cancellation.

[ASSISTANT]
Please provide the generator consumption code.

[USER]
Here it is. Diagnose and propose a fix.

[TOOLS]
Available tools: read_file, search
Tool results exist but their contents are omitted.
```

The character budget avoids introducing a router-model-specific tokenizer.
Malformed JSON message structures are treated as untrusted input and guarded
before every list or dict access.

## Synchronous invocation and cancellation

LLMRouter's routing path is synchronous. It must never run directly in the
aiohttp event-loop task. The adapter calls it through an anyio worker thread:

```python
routes = await anyio.to_thread.run_sync(
    adapter.route_sync,
    context,
    abandon_on_cancel=True,
)
```

An empty or omitted `router_timeout` resolves to `None`, meaning psi-agent does
not impose an application-level deadline. A finite positive value wraps the
await in an anyio timeout scope. Timeout causes fallback and no retry.

The accepted empty representations are an omitted CLI option, YAML `null`, a
Python `None`, or an empty `PSI_ROUTER_TIMEOUT` environment value. A CLI user
who wants unlimited waiting omits `--router-timeout`; a CLI user who wants a
deadline supplies a positive number. Input normalization occurs after
`setup_logging()` and produces the internal `float | None` value before the
router is constructed.

External cancellation must propagate. With `abandon_on_cancel=True`, psi-agent
stops awaiting the thread, but Python cannot safely terminate a synchronous
thread already inside LLMRouter. The remote call may continue until
LLMRouter's underlying client returns or reaches its own timeout. This can
consume a routing API call after the user cancels. It is an accepted limitation
of the single-process experiment and must be visible in documentation.

## Route normalization and single-model selection

The raw private API result is validated defensively. Each accepted item must:

- Be a two-element tuple or list.
- Contain a string subquery.
- Contain a non-empty string model name.
- Name an exact member of `route_model`.

Unknown or malformed items are ignored with a warning. Valid models are counted
and the model with the most occurrences wins. Ties are broken by first
appearance in the valid route sequence, making selection deterministic.

Example:

```python
[
    ("Analyze the traceback", "deepseek-reasoner"),
    ("Generate a patch", "qwen-coder"),
    ("Check cancellation safety", "deepseek-reasoner"),
]
```

selects `deepseek-reasoner`. The selected model is mapped by index:

```python
index = route_model.index(selected_model)
selected_upstream = upstream[index]
```

The original Chat Completions request is then proxied to only that upstream.
The original `messages`, `tools`, sampling parameters, and unknown fields are
preserved. Internal `routing` metadata is removed. The selected route-model
label may replace the body model for a consistent upstream contract.

## Explicit model and fallback

An incoming body model that exactly matches a configured `route_model` skips
LLMRouter and selects the corresponding upstream. An unknown requested model
never bypasses the candidate whitelist; it proceeds through automatic routing.

All recoverable routing failures use one fallback path:

```text
configured default_model -> its matching upstream
no default_model          -> upstream[0]
```

Fallback conditions include:

- Missing usable user context.
- LLMRouter exception.
- Configured psi-agent routing timeout.
- Malformed private-API result.
- No valid whitelisted result.
- All returned model names being unknown.

Fallback does not retry, run another routing policy, or choose a model based on
the failure type. Errors from the selected answer-producing upstream remain
normal upstream failures and propagate through psi-agent's existing SSE error
protocol.

## Logging and result visibility

psi-agent uses loguru; it does not add `print()` calls.

INFO records the final decision without user content:

```text
LLMRouter selected model='deepseek-reasoner' upstream='./deepseek.sock' routes=3 source='majority'
```

DEBUG records the vote table and tie resolution:

```text
LLMRouter votes: {'deepseek-reasoner': 2, 'qwen-coder': 1}
```

Raw subqueries are only logged when `log_router_details` is explicitly enabled.
They are length-limited even then:

```text
LLMRouter raw routes: [...]
```

Fallback warnings include the safe failure category and chosen fallback model.
They exclude API keys, full conversation text, full remote responses, and tool
result bodies.

LLMRouter itself currently contains `print()` calls. Globally redirecting
stdout in a multithreaded process is unsafe, so the experiment does not capture
them. This third-party behavior is documented rather than hidden.

## Lifecycle and resource safety

- Build the LLMRouter instance once at `AiRouter` startup, not per request.
- Keep it immutable after construction except for library-owned state.
- Serialize access if the instance or its global API client is not thread-safe.
- Start only one LLMRouter-policy credential per process.
- Create only the selected upstream connection after routing completes.
- Manage aiohttp upstream responses with context managers and shield cleanup
  across cancellation where required.
- Preserve the existing socket-file lifecycle; do not automatically unlink it.
- Shield `AppRunner.cleanup()` on startup failure and shutdown.

## Tests

### Dependency and construction tests

- Lazy import does not affect ordinary AI startup without the extra.
- Missing extra produces the documented startup error.
- The pinned library imports on the supported runtime.
- Generated YAML uses an absolute `llm_data` path.
- Generated candidate JSON matches LLMRouter's expected structure.
- `LLMMultiRoundRouter` is constructed once.
- Conflicting process-wide router credentials are rejected.

### Context tests

- System context is retained.
- Recent user/assistant context is retained from the end.
- Latest user text is retained.
- Old messages are removed when the character budget is exceeded.
- Tool names are retained but tool results and arguments are omitted.
- Multimodal parts become safe type markers.
- Invalid JSON shapes do not cause unchecked access.

### Contract and voting tests

- `_decompose_and_route()` is called with the serialized context.
- The verified list-of-pairs contract is accepted.
- Malformed entries are ignored.
- Unknown model names are ignored.
- A simple majority wins.
- A tie selects the earliest valid model.
- No valid routes triggers the single fallback path.
- Raw details are not logged by default.
- Raw details are logged only with explicit opt-in.

### Timeout and cancellation tests

- `None` waits without a psi-agent timeout.
- A finite positive timeout falls back when elapsed.
- Zero, negative, NaN, and infinity are rejected at startup.
- External cancellation propagates while the worker result is abandoned.
- No retry occurs after timeout or failure.

### Proxy integration tests

- Each selected model maps to the correct positional upstream.
- An explicit whitelisted model skips LLMRouter.
- An unknown explicit model does not bypass routing.
- The original request body is preserved except for internal metadata and the
  selected model label.
- SSE content, reasoning, tool calls, finish reasons, and `[DONE]` pass through.
- Downstream disconnect closes the selected upstream connection.
- Routing failure chooses `default_model` when configured.
- Routing failure chooses `upstream[0]` otherwise.

### Full verification

```powershell
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest -v
uv run psi-agent ai router --help
```

## Documentation changes

Implementation must update the root and AI-layer `AGENTS.md`, `README.md`,
`README_en.md`, and relevant `docs/` pages. Documentation must distinguish:

- The single routing model from multiple answer-producing upstreams.
- The positional `route_model[i]` to `upstream[i]` mapping.
- Routing-only private API use in this experimental phase.
- Majority voting and deterministic tie-breaking.
- Safe default logging versus opt-in raw route logging.
- Empty timeout meaning no psi-agent-imposed deadline.
- Cancellation's inability to terminate an already-running synchronous worker.
- The pinned dependency and Python 3.14 compatibility gate.
- The planned later move to full `route_single()` execution and aggregation.

## Future evolution

If the experiment demonstrates useful routing quality, the next phase may make
LLMRouter the full answer-producing backend by calling public
`LLMMultiRoundRouter.route_single()`. That change will require a separate design
because it moves subquery execution and aggregation out of psi-agent upstreams,
changes streaming behavior, and affects tool-call compatibility.

A nearer-term upstream contribution should expose a stable public route-only
method and explicit API client/credential injection. Once available, psi-agent
can stop depending on `_decompose_and_route()` and the process-wide `API_KEYS`
mechanism without changing its upstream mapping contract.
