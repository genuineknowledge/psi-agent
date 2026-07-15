# LLMRouter Integration Design

Date: 2026-07-15

## Status

This document consolidates the current LLMRouter integration design for
psi-agent. It supersedes the fragmented July 2026 LLMRouter notes as the
single reading entry for the feature's intended behavior, while earlier files
remain as design history.

## Summary

psi-agent integrates `llmrouter-lib==0.3.1` directly and uses
`ulab-uiuc/LLMRouter` as a routing framework, not as the final response
generator. The router model receives bounded conversation context, calls
`LLMMultiRoundRouter._decompose_and_route()`, and selects exactly one
configured candidate model by majority vote. The winning candidate is then
invoked through psi-agent's existing OpenAI-compatible HTTP/SSE proxy, so
streaming content, reasoning, tool calls, and error propagation remain owned
by psi-agent's normal upstream flow.

The integration intentionally uses a single remote routing model and multiple
answer-producing upstreams. The router model itself is not automatically an
upstream candidate unless the user also includes the same model name in the
candidate list.

## Goals

- Use the real LLMRouter library rather than reimplementing its prompts.
- Configure one remote router model with explicit model name, base URL, and
  API key inputs.
- Let users pass candidate upstreams explicitly, including model description.
- Include bounded conversation context in routing input.
- Select exactly one upstream per original Chat Completions request.
- Preserve psi-agent's request passthrough, SSE streaming, and fallback model
  behavior.
- Keep routing logs observable without exposing full prompt content by default.
- Support Python 3.14 project environments and CI.

## Non-goals

- Let LLMRouter execute candidate requests or aggregate the final answer in
  this phase.
- Train or fine-tune routing models.
- Route to models outside the explicit upstream candidate whitelist.
- Send tool result bodies, tool-call arguments, binary content, addresses, or
  credentials into routing context.
- Retry routing or execute runner-up candidates.
- Support multiple distinct router credentials concurrently in one process.

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

Responsibilities are split as follows:

- `AiRouter`: service lifecycle, request handling, explicit-model bypass,
  fallback, upstream proxying, and SSE/error handling.
- `LLMRouterAdapter`: upstream parsing, runtime-file generation, packaged
  prompt setup, synchronous LLMRouter invocation, result validation, and vote
  resolution.
- Context serialization: bounded conversion from Chat Completions messages into
  safe routing text.

## CLI Contract

The command remains:

```powershell
uv run psi-agent ai router
```

The routing model is configured with separate fields:

```text
--router-model
--router-base-url
--router-api-key
```

Candidate models are passed through one `--upstream` option followed by one or
more JSON objects, one candidate per value:

```powershell
uv run psi-agent ai router `
  --session-socket .\router.sock `
  --router-model qwen-turbo `
  --router-base-url https://router.example.com/v1 `
  --router-api-key sk-router `
  --default-model qwen-plus `
  --upstream `
  '{\"addr\":\"http://127.0.0.1:8101\",\"model\":\"qwen-plus\",\"description\":\"General tasks\"}' `
  '{\"addr\":\"http://127.0.0.1:8102\",\"model\":\"deepseek-reasoner\",\"description\":\"Complex reasoning\"}'
```

This multiline object form is the active contract. The following are
deliberately unsupported:

- one value containing an outer JSON array;
- newline splitting inside one string;
- repeating `--upstream` once per candidate.

Each upstream object must contain exactly:

- `addr`: upstream AI address, such as TCP URL, Unix socket path, or named
  pipe address;
- `model`: candidate model name;
- `description`: human-authored routing description.

Validation rules:

- at least one upstream is required;
- every value must be valid JSON object text;
- unknown fields are rejected;
- `addr`, `model`, and `description` must be non-empty strings;
- model names must be unique;
- candidate API keys are forbidden in upstream objects.

Malformed input errors identify the failing `upstream[index]`. When the JSON
looks object-shaped but has lost its double quotes, the error should also hint
at PowerShell `\"` escaping.

## Runtime Configuration Model

The effective router configuration is:

```python
@dataclass
class AiRouter:
    session_socket: str
    router_model: str
    router_base_url: str
    router_api_key: str
    upstream: list[str]
    default_model: str = ""
    router_timeout: float | None = None
    router_context_chars: int = 12_000
    log_router_details: bool = False
    verbose: bool = False
```

Environment fallbacks remain supported for router credentials:

- `PSI_ROUTER_MODEL`
- `PSI_ROUTER_BASE_URL`
- `PSI_ROUTER_API_KEY`

CLI values take precedence. An empty API key is allowed for unauthenticated
OpenAI-compatible endpoints.

`router_timeout` accepts:

- omitted or `None`: no psi-agent-imposed routing deadline;
- finite positive number: application-level routing timeout.

Zero, negative, NaN, and infinity are invalid startup values.

Fallback behavior is deterministic:

- use `default_model` when configured and valid;
- otherwise use the first upstream candidate.

## Routing Input

Routing input includes conversation context, not just the latest user message.
The serializer keeps:

- the first system message;
- recent user and assistant messages from the tail of the conversation;
- the newest user message even when older context must be truncated;
- tool names and a marker that tool results exist;
- markers for image, audio, file, or other non-text content.

The serializer omits:

- tool-result bodies;
- tool-call argument bodies;
- binary or base64 content;
- upstream addresses, URLs, and API keys.

If there is no usable user context, routing is skipped and fallback is used.

## LLMRouter Library Usage

The integration intentionally calls the LLMRouter private method:

```python
LLMMultiRoundRouter._decompose_and_route(context)
```

This is a routing-only use of the library. psi-agent does not call
`route_single()` in this phase because `route_single()` would also execute
candidate requests and aggregate answers, which would bypass psi-agent's
existing upstream/SSE pipeline.

Because `_decompose_and_route()` is private API, the implementation pins
`llmrouter-lib==0.3.1` and treats upgrades as explicit compatibility work.

## Runtime Files

The adapter generates runtime files once at startup:

```text
router-runtime/
|-- runtime.yaml
`-- llm_data.json
```

`llm_data.json` is generated from the CLI upstream values and contains
candidate metadata only:

```json
{
  "qwen-plus": {
    "feature": "General tasks",
    "model": "qwen-plus"
  },
  "deepseek-reasoner": {
    "feature": "Complex reasoning",
    "model": "deepseek-reasoner"
  }
}
```

It does not store:

- candidate addresses;
- router API key;
- router endpoint credentials;
- unrelated runtime secrets.

The generated YAML contains the router model and endpoint:

```yaml
data_path:
  llm_data: C:/absolute/runtime/path/llm_data.json
base_model: qwen-turbo
use_local_llm: false
api_endpoint: https://router.example.com/v1
```

Absolute paths are required because LLMRouter otherwise resolves relative paths
against its installed package root.

## Custom Task Templates

psi-agent packages and owns the LLMRouter prompt templates under:

- `src/psi_agent/ai/custom_tasks/agent_decomp_route.yaml`
- `src/psi_agent/ai/custom_tasks/agent_decomp_cot.yaml`
- `src/psi_agent/ai/custom_tasks/agent_prompt.yaml`

At startup, the adapter points `llmrouter.prompts` private globals at
psi-agent's packaged `custom_tasks` directory before constructing
`LLMMultiRoundRouter`. This is required because the default prompt loader only
searches inside the installed LLMRouter package and otherwise fails with
`FileNotFoundError`.

This integration point is intentionally narrow:

- do not copy prompt files into `.venv`;
- do not patch third-party package files;
- do not depend on the current working directory.

Missing packaged templates are treated as startup errors, not recoverable
routing failures.

## Runtime Model-Data Shim

LLMRouter 0.3.1 expects `base_model` to be resolvable from `llm_data` when
that dictionary is non-empty. Candidate-only `llm_data.json` therefore causes
endpoint lookup to fail for the router model.

psi-agent works around this by using two phases:

1. Construct `LLMMultiRoundRouter` from candidate-only `llm_data.json`.
2. Inject an in-memory `router.llm_data[router_model]` entry containing the
   router model's endpoint.

The injected record is not persisted to disk. This keeps the generated JSON a
true candidate-only artifact while still satisfying LLMRouter's runtime lookup
behavior.

If `router_model` is also one of the upstream candidates, the adapter keeps the
candidate's description and model identity and adds only the endpoint field.

## Concurrency, Timeout, and Cancellation

LLMRouter's routing path is synchronous, so psi-agent invokes it in an anyio
worker thread. The adapter also uses a process-wide synchronous lock because
LLMRouter depends on process-global prompt configuration and process-global
`API_KEYS` handling.

The lock protects:

- prompt-global setup;
- router construction;
- runtime endpoint injection;
- temporary `API_KEYS` mutation during route calls.

Timeout and cancellation behavior:

- the async caller may stop waiting;
- the underlying synchronous LLMRouter call cannot be forcefully terminated;
- an abandoned worker may continue until its own remote call returns;
- the worker remains responsible for restoring `API_KEYS` and releasing the
  synchronous lock.

This means a cancelled request may still consume one routing-model API call.

## Route Validation and Selection

The raw LLMRouter result is treated as untrusted. Accepted items must:

- be tuple/list pairs;
- contain string subquery and model values;
- reference a candidate model that exactly matches the configured whitelist.

Malformed or unknown entries are ignored with safe logging.

Selection rules:

- count valid model occurrences;
- highest count wins;
- ties resolve by first appearance in the valid route sequence.

Allowed route sources are:

- `request_model`
- `llmrouter_majority`
- `fallback_default`
- `fallback_first`

If the incoming request `model` exactly matches a configured candidate, the
router bypasses LLMRouter and selects that upstream directly.

## Request Forwarding and SSE Behavior

After route selection, psi-agent forwards the original Chat Completions request
to exactly one selected upstream.

Preserved behavior:

- original `messages`;
- `tools`;
- sampling parameters;
- unknown passthrough fields;
- streaming SSE content;
- reasoning deltas;
- tool calls;
- normal finish reasons;
- `[DONE]`.

Internal routing metadata is removed before forwarding. The forwarded request's
`model` is replaced with the selected candidate model.

Selected-upstream failures continue to use psi-agent's existing error
conventions:

- non-stream errors return the OpenAI-style JSON error shape;
- stream-time failures use `finish_reason="error"`.

## Logging

psi-agent uses loguru rather than `print()`.

Expected logging policy:

- INFO: final selection, upstream, and routing source;
- DEBUG: votes, lock lifecycle, request completion, and each SSE chunk;
- WARNING: malformed route items, timeout, fallback, and recoverable LLMRouter
  failures;
- ERROR: startup failure and selected-upstream failure.

Raw subqueries are logged only when `log_router_details` is enabled, and even
then they should remain bounded. API keys, full conversation text, tool-result
bodies, and full prompt templates are never logged.

LLMRouter's own internal `print()` output is acknowledged as third-party
behavior and is not captured by global stdout redirection.

## Dependency and CI Policy

psi-agent now depends directly on:

```toml
llmrouter-lib==0.3.1
```

This replaces the previous semantic-router-based direction for this feature.

Because LLMRouter brings LiteLLM and PyO3 build requirements, GitHub workflows
must provide a consistent Python 3.14 environment. The required CI policy is:

- set workflow-level `PYO3_USE_ABI3_FORWARD_COMPATIBILITY: "1"`;
- use `astral-sh/setup-uv@v7` with `version: "0.11.23"` and
  `python-version: "3.14"`;
- enable uv cache keyed by `uv.lock` and `pyproject.toml`;
- run `uv sync --frozen` instead of plain `uv sync`;
- install stable Rust before sync in jobs that build the project environment.

The workflows in scope are:

- `.github/workflows/ci.yml`
- `.github/workflows/nuitka.yml`
- `.github/workflows/pyinstaller.yml`
- `.github/workflows/auto-alpha-tag.yml`

The lint workflow should also smoke-test:

- LiteLLM import;
- `LLMMultiRoundRouter` import;
- `_decompose_and_route` availability;
- packaged custom-task prompt resources.

README files are intentionally outside the CI-only documentation update scope.

## Testing Expectations

Tests should cover the feature in four layers:

### Adapter unit tests

- multiline upstream parsing and strict validation;
- context serialization and redaction behavior;
- runtime YAML and candidate JSON generation;
- packaged custom-task resolution;
- startup-time endpoint injection for the router model;
- route validation, majority vote, and tie-breaking;
- `API_KEYS` restoration and process-wide lock behavior;
- timeout/cancellation behavior and cleanup coordination.

### Router integration tests

- explicit request-model bypass;
- fallback to configured default or first candidate;
- upstream selection and request forwarding;
- SSE passthrough for content, reasoning, tool calls, and done marker;
- upstream error propagation and downstream disconnect cleanup.

### CLI and environment tests

- `psi-agent ai router --help` exposes the active flags and omits removed
  contracts;
- PowerShell input examples remain valid for Windows quoting;
- ordinary AI startup is unaffected when routing-specific behavior is not used.

### CI contract tests

- workflow parsing and static assertions for uv, Python, Rust, and frozen sync;
- smoke validation that packaged prompt resources exist in the installed
  environment.

## Verification

The combined feature should be validated with:

```powershell
uv lock
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -v
uv run psi-agent ai router --help
uv build
git diff --check
```

## Documentation Follow-up

Behavioral documentation outside this file should describe:

- one router model versus multiple answer-producing upstreams;
- the multiline `--upstream` JSON-object input contract;
- candidate descriptions and whitelist behavior;
- routing-only use of LLMRouter private API;
- custom packaged task templates;
- deterministic fallback and tie-breaking;
- empty timeout meaning unlimited wait at the psi-agent layer;
- cancellation limitations of synchronous worker threads;
- Python 3.14 CI compatibility handling.

## Future Evolution

Possible future work includes:

- switching to a stable public route-only API if LLMRouter provides one;
- later evaluating `route_single()` as a full execution backend in a separate
  design;
- allowing richer user-authored candidate descriptions or metadata;
- removing the runtime endpoint shim once upstream library behavior changes.
