# Semantic Model Router Design

## Goal

Add a lightweight semantic model router to psi-agent. For every OpenAI Chat
Completions request, the router asks one routing model to select the best
candidate using only candidate descriptions. It then forwards the original
request to the selected candidate's configured address.

This design deliberately does not use the third-party `llmrouter` framework,
multi-round task decomposition, voting, embeddings, keyword matching, or a
second local classification pass.

## Command-line interface

The router is exposed as the top-level `router` command:

```powershell
uv run psi-agent router `
  --session-socket "http://127.0.0.1:8100" `
  --router-socket "http://127.0.0.1:7001" `
  --upstream `
    '{\"socket\":\"http://127.0.0.1:7001\",\"description\":\"本地通用中文问答、摘要和简单任务\"}' `
    '{\"socket\":\"http://127.0.0.1:7002\",\"description\":\"复杂推理、代码分析、数学和多步骤任务\"}' `
  --default-socket "http://127.0.0.1:7001" `
  --router-context-chars 12000 `
  --verbose
```

Each `--upstream` value is one JSON object with exactly three fields:

```json
{
  "socket": "http://127.0.0.1:7002",
  "description": "复杂推理、代码分析、数学和多步骤任务"
}
```

`--default-socket` is required and does not have to equal a configured
candidate socket.

The existing single-model AI command remains available. CLI composition must
make both ordinary AI service startup and `psi-agent router` discoverable
without changing the ordinary AI service's behavior or parameter meanings.

## Package and component boundaries

The implementation lives in a package independent of the existing AI
provider adapter:

```text
src/psi_agent/router/
├── __init__.py       # Router configuration, validation, and lifecycle
├── models.py         # Upstream and RouteDecision types; JSON parsing
├── prompts.py        # Routing prompt template and candidate interpolation
├── selector.py       # Context serialization, router client, and decision parsing
├── server.py         # HTTP handler, fallback policy, and SSE proxy
└── AGENTS.md         # Router-specific design and maintenance constraints
```

The router presents the same `POST /chat/completions` boundary as an ordinary
AI service. Session, Channel, workspace tools, and the internal
Chat Completions/SSE protocol remain unchanged.

Each unit has one responsibility:

- `models.py` converts untrusted CLI JSON into immutable validated values.
- `prompts.py` owns the routing prompt and exposes descriptions only.
- `selector.py` serializes context, calls the routing model, and converts its
  response into a candidate index. It does not make network-routing decisions.
- `server.py` owns the request lifecycle, chooses between a candidate and the
  default address, and proxies the selected upstream.
- `Router` resolves CLI/environment configuration, constructs dependencies,
  starts the aiohttp application, and cleans it up.

## Routing data flow

### 1. Accept and preserve the request

The handler accepts an OpenAI-compatible Chat Completions JSON object and
retains the complete original body. A non-object JSON body is rejected with
HTTP 400. Unknown request fields are not discarded.

### 2. Serialize bounded routing context

The selector creates context from `messages`:

- retain textual system, user, and assistant content;
- retain assistant tool names but omit large tool-call argument bodies;
- represent tool results with a short marker and omit result bodies;
- represent non-text image, audio, and file content with short markers;
- preserve the first system message and the newest useful conversation blocks
  when truncation is necessary;
- never exceed `router_context_chars` characters.

If no usable user content exists, semantic selection is skipped and the
request uses the default socket.

### 3. Expose descriptions only

The routing prompt gives each description an opaque, zero-based candidate
number:

```text
Candidate 0: 本地通用中文问答、摘要和简单任务
Candidate 1: 复杂推理、代码分析、数学和多步骤任务
```

The routing model must not receive candidate `socket`, the default socket, or
any API key. It is asked to return JSON shaped as:

```json
{
  "candidate": 1,
  "reason": "任务涉及复杂代码推理"
}
```

Candidate numbering is only an index into local validated configuration. The
routing model can never synthesize or select an arbitrary network address.

### 4. Call the routing model service

The selector sends an OpenAI-compatible streaming request to `router_socket`.
That socket points to an already running ordinary psi-agent AI service and may
be independent from every candidate socket, so provider, model, base URL, and
API key configuration remain entirely on that AI service. The selector
aggregates `delta.content` from its SSE response before parsing the decision.

The response parser accepts a plain JSON object, fenced JSON, or the first
valid JSON object surrounded by explanatory text. A decision is valid only
when `candidate` is an integer (not a boolean) within the configured candidate
range. `reason` is diagnostic metadata and cannot affect address selection.

### 5. Forward a successful selection

For a valid decision, the handler retrieves the selected `Upstream` locally,
copies the original request body, overwrites its `model` field with the
candidate's `socket` and posts the original body to it unchanged. The original
`model`, `messages`, `tools`, `tool_choice`, sampling parameters, and unknown
extensions all pass through unchanged.

Candidate sockets are service base addresses rather than complete request
endpoints. The implementation always appends `/chat/completions` for HTTP/TCP
addresses, so callers must not include that suffix in the configured socket.
It should reuse the project's transport helpers so supported HTTP/TCP,
Unix-socket, and Windows Named Pipe addresses behave consistently.

### 6. Proxy SSE unchanged

A successful business upstream's SSE bytes are proxied without reconstructing
normal chunks. This preserves content, reasoning, tool calls, finish reasons,
provider extensions, and `[DONE]` exactly as emitted.

## Default fallback

The router uses `default_socket` for this request when:

- the routing model times out or raises a network error;
- the routing endpoint returns an HTTP error;
- the routing response is not OpenAI-compatible JSON;
- response content is absent or is not text;
- no valid decision JSON can be extracted;
- the candidate value is invalid or out of range; or
- no usable user context exists.

Fallback copies and forwards the original request without changing its
`model` field. The service at `default_socket` is responsible for interpreting
that original model value. A default-upstream failure is reported directly;
there is no recursive fallback.

Fallback is per request. A routing failure does not disable semantic routing
for later requests.

## Validation

Startup fails clearly unless all of the following hold:

- `router_socket` is non-empty and uses a supported address format;
- at least one upstream is present;
- every upstream JSON value is an object with exactly `socket` and
  `description`;
- both upstream fields are trimmed, non-empty strings;
- candidate sockets and `default_socket` use supported address formats;
- `default_socket` is non-empty;
- `router_context_chars` is positive; and
- an optional routing timeout, if exposed, is finite and positive.

The default socket is intentionally not required to appear in the candidate
list.

## Errors, cancellation, and cleanup

Request parsing failures before response preparation return the project's
OpenAI-style error JSON with HTTP 400.

Once a destination has been selected:

- a non-200 business-upstream response received before response preparation
  becomes OpenAI-style error JSON with HTTP 502;
- a proxy failure after SSE response preparation emits the project's internal
  error chunk with `finish_reason="error"` and then closes the response;
- a client disconnect cancels proxying and closes the upstream without trying
  to send another error response.

All I/O is asynchronous and uses `aiohttp` and `anyio`, not native `asyncio`,
synchronous HTTP libraries, or synchronous filesystem calls. Network streams
are consumed through closing async contexts. aiohttp runner cleanup is
shielded both when startup fails and during shutdown. `Router.run()` calls
`setup_logging(verbose=self.verbose)` as its first executable statement.

## Logging and secrets

Normal selection logs always identify the routing reason and final destination
socket. Every proxied SSE chunk is logged at DEBUG consistently with existing
protocol boundaries, and `--verbose` enables those additional diagnostics.
INFO summary logs must not include candidate descriptions, indices,
serialized-context lengths, raw routing context, or API keys.

## Testing

### Unit tests

`models.py` tests cover valid repeated upstream values, malformed JSON,
non-object values, missing/empty/unknown fields, duplicate model names,
address normalization, and exact index-to-upstream mapping.

`selector.py` tests prove that prompts contain descriptions and opaque indices
but do not contain sockets or API keys. They cover plain,
fenced, and surrounded JSON; invalid booleans, negative and out-of-range
indices; damaged output; bounded conversation serialization; tool-call
markers; and multimodal placeholders.

`server.py` tests cover model overwrite after a semantic selection, complete
parameter passthrough, preservation of the original model on fallback, direct
fallback for missing user context, byte-for-byte SSE proxying, HTTP errors,
streaming errors, cancellation, and resource cleanup.

### CLI tests

CLI tests cover `psi-agent router --help`, repeated PowerShell-style JSON
upstreams, missing required arguments, ordinary AI command compatibility, and
the logging-first invariant.

### Integration tests

Local aiohttp mock services verify simple and complex task selection, damaged
and timed-out router responses falling back to the default socket, tool-call
SSE surviving Router-to-Session forwarding, and upstream errors not being
committed to conversation history. Tests do not call external networks.

## Documentation and verification

Implementation updates the root and layer `AGENTS.md` files where architecture
or protocol guidance changes, adds `src/psi_agent/router/AGENTS.md`, and updates
both `README.md` and `README_en.md` with PowerShell and POSIX examples.

Completion requires fresh successful runs of:

```text
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run psi-agent router --help
uv run psi-agent ai --help
```

The implementation must preserve unrelated user changes, including the
pre-existing `.gitignore` modification.
