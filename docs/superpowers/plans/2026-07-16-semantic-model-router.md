# Semantic Model Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `psi-agent ai router`, which selects one configured upstream from description-only semantic routing and transparently proxies the original Chat Completions/SSE request.

**Architecture:** A new `psi_agent.router` package separates validated upstream configuration, description-only selection, HTTP/SSE proxying, and service lifecycle. The routing model returns an opaque candidate index; local configuration alone maps that index to `model_name` and `addr`, while all routing failures use `default_addr` without changing the request's original model.

**Tech Stack:** Python 3.14, anyio, aiohttp, tyro, loguru, pytest, pytest-asyncio, ruff, ty

---

## File map

- Create `src/psi_agent/router/models.py`: immutable routing types and untrusted `--upstream` JSON validation.
- Create `src/psi_agent/router/selector.py`: bounded message serialization, description-only prompt generation, router HTTP call, and response parsing.
- Create `src/psi_agent/router/server.py`: destination selection, default fallback, request forwarding, and byte-preserving SSE proxy.
- Create `src/psi_agent/router/__init__.py`: `AiRouter` CLI dataclass, environment fallback, validation, and aiohttp lifecycle.
- Create `src/psi_agent/router/AGENTS.md`: router invariants and maintenance guidance.
- Create `tests/psi_agent/router/__init__.py`: package marker required by the test layout.
- Create `tests/psi_agent/router/test_models.py`: configuration parsing tests.
- Create `tests/psi_agent/router/test_selector.py`: context and router-decision tests.
- Create `tests/psi_agent/router/test_server.py`: handler, proxy, fallback, and cleanup tests.
- Create `tests/psi_agent/router/test_router.py`: `AiRouter` validation and lifecycle tests.
- Create `tests/integration/test_semantic_router.py`: local end-to-end router/upstream/Session coverage.
- Modify `src/psi_agent/_sockets.py`: avoid duplicating a complete Chat Completions path.
- Modify `tests/psi_agent/test_sockets.py`: endpoint normalization coverage.
- Modify `src/psi_agent/cli.py`: expose ordinary AI and router under the `ai` command group.
- Create `tests/psi_agent/test_cli.py`: subprocess-free tyro command-shape coverage.
- Modify `AGENTS.md`, `src/psi_agent/ai/AGENTS.md`, `README.md`, and `README_en.md`: architecture and usage documentation.

### Task 1: Validated upstream configuration

**Files:**
- Create: `tests/psi_agent/router/__init__.py`
- Create: `tests/psi_agent/router/test_models.py`
- Create: `src/psi_agent/router/models.py`

- [ ] **Step 1: Add failing parsing and mapping tests**

```python
from __future__ import annotations

import pytest

from psi_agent.router.models import RouteDecision, Upstream, parse_upstreams


def test_parse_upstreams_preserves_order_and_maps_candidate() -> None:
    targets = parse_upstreams(
        [
            '{"model_name":"qwen","addr":"http://127.0.0.1:7001","description":"simple"}',
            '{"model_name":"deepseek","addr":"http://127.0.0.1:7002","description":"complex"}',
        ]
    )
    assert targets == (
        Upstream(model_name="qwen", addr="http://127.0.0.1:7001", description="simple"),
        Upstream(model_name="deepseek", addr="http://127.0.0.1:7002", description="complex"),
    )
    decision = RouteDecision(candidate=1, reason="needs reasoning")
    assert targets[decision.candidate].addr == "http://127.0.0.1:7002"


@pytest.mark.parametrize(
    ("encoded", "message"),
    [
        ("[]", "must be a JSON object"),
        ('{"model_name":"m","addr":"a"}', "missing fields"),
        ('{"model_name":"m","addr":"a","description":"d","extra":1}', "unsupported fields"),
        ('{"model_name":" ","addr":"a","description":"d"}', "model_name must be a non-empty string"),
    ],
)
def test_parse_upstreams_rejects_invalid_values(encoded: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_upstreams([encoded])


def test_parse_upstreams_rejects_duplicate_model_names() -> None:
    raw = '{"model_name":"same","addr":"http://a","description":"d"}'
    with pytest.raises(ValueError, match="duplicate upstream model_name"):
        parse_upstreams([raw, raw])
```

- [ ] **Step 2: Run the focused test and confirm the red state**

Run: `uv run pytest tests/psi_agent/router/test_models.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'psi_agent.router'`.

- [ ] **Step 3: Implement immutable types and strict JSON parsing**

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Upstream:
    model_name: str
    addr: str
    description: str


@dataclass(frozen=True)
class RouteDecision:
    candidate: int
    reason: str


def _required_text(item: dict[str, Any], key: str, location: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def parse_upstreams(raw: list[str]) -> tuple[Upstream, ...]:
    if not raw:
        raise ValueError("--upstream must provide at least one JSON object")
    targets: list[Upstream] = []
    model_names: set[str] = set()
    allowed = {"model_name", "addr", "description"}
    for index, encoded in enumerate(raw):
        location = f"upstream[{index}]"
        try:
            value: Any = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{location} must be valid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{location} must be a JSON object")
        missing = allowed - set(value)
        if missing:
            raise ValueError(f"{location} has missing fields: {sorted(missing)!r}")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"{location} has unsupported fields: {sorted(unknown)!r}")
        target = Upstream(
            model_name=_required_text(value, "model_name", location),
            addr=_required_text(value, "addr", location),
            description=_required_text(value, "description", location),
        )
        if target.model_name in model_names:
            raise ValueError(f"duplicate upstream model_name: {target.model_name!r}")
        model_names.add(target.model_name)
        targets.append(target)
    return tuple(targets)
```

- [ ] **Step 4: Run the focused test and confirm green**

Run: `uv run pytest tests/psi_agent/router/test_models.py -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the configuration unit**

```bash
git add src/psi_agent/router/models.py tests/psi_agent/router/__init__.py tests/psi_agent/router/test_models.py
git commit -m "feat(router): validate semantic upstreams"
```

### Task 2: Address endpoint normalization

**Files:**
- Modify: `tests/psi_agent/test_sockets.py`
- Modify: `src/psi_agent/_sockets.py`

- [ ] **Step 1: Add failing tests for complete and `/v1` endpoints**

```python
def test_tcp_complete_chat_completions_endpoint_is_not_duplicated() -> None:
    connector, endpoint = resolve_connector_and_endpoint("http://example.com/v1/chat/completions")
    assert endpoint == "http://example.com/v1/chat/completions"
    awaitable = connector.close()
    assert awaitable is None


def test_tcp_v1_root_gets_chat_completions_suffix() -> None:
    connector, endpoint = resolve_connector_and_endpoint("https://example.com/v1")
    assert endpoint == "https://example.com/v1/chat/completions"
    awaitable = connector.close()
    assert awaitable is None
```

Adapt cleanup to the exact aiohttp connector API already used by neighboring tests; do not leave unclosed-connector warnings.

- [ ] **Step 2: Run the socket tests and confirm the duplicated-path failure**

Run: `uv run pytest tests/psi_agent/test_sockets.py -v`

Expected: the complete endpoint is currently rendered as `/v1/chat/completions/chat/completions`.

- [ ] **Step 3: Normalize only TCP endpoint paths**

Replace the TCP endpoint construction with:

```python
base = addr.rstrip("/")
suffix = path_prefix if path_prefix.startswith("/") else f"/{path_prefix}"
endpoint = base if base.endswith(suffix) else base + suffix
```

Keep Unix socket and Named Pipe behavior unchanged and keep explicit connector keyword arguments.

- [ ] **Step 4: Run socket tests and lint the touched files**

Run: `uv run pytest tests/psi_agent/test_sockets.py -v`

Expected: all socket tests pass.

Run: `uv run ruff check src/psi_agent/_sockets.py tests/psi_agent/test_sockets.py`

Expected: exit code 0.

- [ ] **Step 5: Commit endpoint normalization**

```bash
git add src/psi_agent/_sockets.py tests/psi_agent/test_sockets.py
git commit -m "fix(sockets): preserve complete API endpoints"
```

### Task 3: Description-only selector and bounded context

**Files:**
- Create: `tests/psi_agent/router/test_selector.py`
- Create: `src/psi_agent/router/selector.py`

- [ ] **Step 1: Add failing prompt, parser, and context tests**

```python
from __future__ import annotations

import pytest

from psi_agent.router.models import RouteDecision, Upstream
from psi_agent.router.selector import build_routing_messages, parse_decision, serialize_context


TARGETS = (
    Upstream("secret-model-a", "http://secret-a:1", "simple Chinese tasks"),
    Upstream("secret-model-b", "http://secret-b:2", "code and mathematics"),
)


def test_prompt_exposes_only_candidate_numbers_and_descriptions() -> None:
    messages = build_routing_messages("[USER]\nprove this", TARGETS)
    rendered = "\n".join(str(item["content"]) for item in messages)
    assert "Candidate 0: simple Chinese tasks" in rendered
    assert "Candidate 1: code and mathematics" in rendered
    assert "secret-model" not in rendered
    assert "http://secret" not in rendered


@pytest.mark.parametrize(
    "text",
    [
        '{"candidate":1,"reason":"math"}',
        '```json\n{"candidate":1,"reason":"math"}\n```',
        'selection follows: {"candidate":1,"reason":"math"}',
    ],
)
def test_parse_decision_extracts_first_valid_object(text: str) -> None:
    assert parse_decision(text, candidate_count=2) == RouteDecision(1, "math")


@pytest.mark.parametrize("value", [True, -1, 2, "1"])
def test_parse_decision_rejects_invalid_candidate(value: object) -> None:
    with pytest.raises(ValueError, match="valid candidate"):
        parse_decision(f'{{"candidate":{value!r},"reason":"x"}}'.replace("'", '"'), candidate_count=2)


def test_serialize_context_keeps_system_and_latest_user_within_limit() -> None:
    messages = [
        {"role": "system", "content": "system rule"},
        {"role": "user", "content": "old " * 50},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "latest question"},
    ]
    context = serialize_context(messages, max_chars=80)
    assert len(context) <= 80
    assert "system rule" in context
    assert "latest question" in context


def test_serialize_context_marks_tools_and_multimodal_content() -> None:
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "inspect"}, {"type": "image_url"}]},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "search", "arguments": "secret"}}]},
        {"role": "tool", "content": "large secret result"},
    ]
    context = serialize_context(messages, max_chars=500)
    assert "[IMAGE]" in context
    assert "search" in context
    assert "secret" not in context
    assert "Tool results exist" in context
```

- [ ] **Step 2: Run selector tests and confirm imports fail**

Run: `uv run pytest tests/psi_agent/router/test_selector.py -v`

Expected: import fails because `selector.py` does not exist.

- [ ] **Step 3: Implement pure selector helpers**

Implement these exact public signatures:

```python
def serialize_context(messages: Any, *, max_chars: int) -> str: ...

def build_routing_messages(context: str, targets: tuple[Upstream, ...]) -> list[dict[str, str]]: ...

def parse_decision(text: str, *, candidate_count: int) -> RouteDecision: ...
```

Use `json.JSONDecoder.raw_decode()` from every `{` position. Validate with
`isinstance(candidate, int) and not isinstance(candidate, bool)`. Construct the
system prompt by enumerating only `target.description`; do not interpolate
`target.model_name` or `target.addr`. Use the serialization rules in the spec,
drop oldest non-system blocks first, and add `[TRUNCATED]` when a single final
block must be cut.

- [ ] **Step 4: Run selector tests and static checks**

Run: `uv run pytest tests/psi_agent/router/test_selector.py -v`

Expected: all tests pass.

Run: `uv run ruff check src/psi_agent/router/selector.py tests/psi_agent/router/test_selector.py`

Expected: exit code 0.

- [ ] **Step 5: Commit pure selection logic**

```bash
git add src/psi_agent/router/selector.py tests/psi_agent/router/test_selector.py
git commit -m "feat(router): add description-only selection"
```

### Task 4: Asynchronous routing-model client

**Files:**
- Modify: `tests/psi_agent/router/test_selector.py`
- Modify: `src/psi_agent/router/selector.py`

- [ ] **Step 1: Add failing aiohttp client tests**

Add local `aiohttp.web` tests that call:

```python
async def select_upstream(
    *,
    context: str,
    targets: tuple[Upstream, ...],
    router_model: str,
    router_base_url: str,
    router_api_key: str,
    timeout: float | None,
) -> RouteDecision: ...
```

The mock handler must assert this request shape:

```python
assert body["model"] == "router-model"
assert body["stream"] is False
assert body["messages"][0]["role"] == "system"
assert request.headers["Authorization"] == "Bearer router-key"
```

Return:

```python
{"choices": [{"message": {"content": '{"candidate":0,"reason":"simple"}'}}]}
```

Also add tests for non-200 status, malformed top-level JSON shape, non-string
content, and timeout. Those cases must raise `RouterSelectionError`; fallback
belongs to the server, not this client.

- [ ] **Step 2: Run the new tests and confirm `select_upstream` is missing**

Run: `uv run pytest tests/psi_agent/router/test_selector.py -v`

Expected: import or attribute failure for `select_upstream`.

- [ ] **Step 3: Implement the client with aiohttp and anyio timeout semantics**

Add:

```python
class RouterSelectionError(RuntimeError):
    pass
```

Resolve the endpoint with `resolve_connector_and_endpoint(router_base_url)`,
use `aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=None))`,
and send a non-streaming JSON request. If `timeout` is not `None`, wrap the
request in `anyio.fail_after(timeout)` and translate timeout/network/status/
shape errors into `RouterSelectionError` with no API key in the message.

- [ ] **Step 4: Run selector tests and type checking**

Run: `uv run pytest tests/psi_agent/router/test_selector.py -v`

Expected: all tests pass without external network access.

Run: `uv run ty check src/psi_agent/router/selector.py`

Expected: exit code 0.

- [ ] **Step 5: Commit the routing client**

```bash
git add src/psi_agent/router/selector.py tests/psi_agent/router/test_selector.py
git commit -m "feat(router): call routing model asynchronously"
```

### Task 5: Destination selection, fallback, and SSE proxy

**Files:**
- Create: `tests/psi_agent/router/test_server.py`
- Create: `src/psi_agent/router/server.py`

- [ ] **Step 1: Add failing handler tests with injected selection**

Build local aiohttp upstream handlers that record JSON bodies and return exact
SSE bytes. Cover these assertions:

```python
assert selected_body["model"] == "deepseek"
assert selected_body["tools"] == original_body["tools"]
assert selected_body["unknown_extension"] == {"keep": True}
assert response_bytes == upstream_bytes
```

Monkeypatch `psi_agent.router.server.select_upstream` to return
`RouteDecision(candidate=1, reason="complex")` for semantic selection.

Add a second test where `select_upstream` raises `RouterSelectionError`; assert
the default upstream receives the original `model` unchanged. Add a third test
with messages lacking a usable user block and assert the selector is never
called.

- [ ] **Step 2: Run handler tests and confirm `server.py` is absent**

Run: `uv run pytest tests/psi_agent/router/test_server.py -v`

Expected: import failure for `psi_agent.router.server`.

- [ ] **Step 3: Implement application keys and destination selection**

Use typed `web.AppKey` values for targets, routing configuration, default
address, context limit, timeout, and detail logging. Implement:

```python
async def handle_router_chat_completions(request: web.Request) -> web.StreamResponse: ...
```

Parse and guard the JSON object before any `.get()`. Call `serialize_context`;
on empty context select `default_addr`. Catch `RouterSelectionError` and log a
WARNING before selecting `default_addr`. For semantic success, copy the body
and set `body["model"] = selected.model_name`; for fallback, copy without model
mutation.

- [ ] **Step 4: Implement byte-preserving upstream proxying**

Resolve the selected address with `resolve_connector_and_endpoint`, post the
body with `ClientTimeout(total=None)`, and return HTTP 502 OpenAI error JSON for
a non-200 response before preparing the downstream response. For a 200
response, prepare standard SSE headers and iterate `upstream_response.content.iter_any()`:

```python
async for raw in upstream_response.content.iter_any():
    logger.debug(f"Router SSE chunk: {raw!r}")
    await response.write(raw)
```

On `ConnectionResetError`, log client cancellation and let async contexts close
the upstream. On another exception after prepare, attempt the internal
`finish_reason="error"` chunk and EOF. Never catch `BaseException`.

- [ ] **Step 5: Run handler tests and add error/cancellation coverage**

Run: `uv run pytest tests/psi_agent/router/test_server.py -v`

Expected: initial semantic/fallback/proxy tests pass.

Then add tests for invalid non-object JSON (400), business upstream non-200
(502), post-prepare upstream failure (`finish_reason="error"`), and client
cancellation/connection cleanup. Re-run the same command; all tests must pass.

- [ ] **Step 6: Run lint and type checks**

Run: `uv run ruff check src/psi_agent/router/server.py tests/psi_agent/router/test_server.py`

Run: `uv run ty check src/psi_agent/router/server.py`

Expected: both commands exit 0.

- [ ] **Step 7: Commit the proxy service**

```bash
git add src/psi_agent/router/server.py tests/psi_agent/router/test_server.py
git commit -m "feat(router): proxy selected model streams"
```

### Task 6: Router lifecycle and CLI command group

**Files:**
- Create: `tests/psi_agent/router/test_router.py`
- Create: `src/psi_agent/router/__init__.py`
- Create: `tests/psi_agent/test_cli.py`
- Modify: `src/psi_agent/cli.py`

- [ ] **Step 1: Add failing `AiRouter` defaults, validation, and cleanup tests**

Assert the dataclass fields and environment fallback names:

```text
PSI_ROUTER_MODEL
PSI_ROUTER_BASE_URL
PSI_ROUTER_API_KEY
```

Test rejection of empty router model/base URL, empty upstreams, empty default
address, non-positive context characters, and non-finite/non-positive timeout.
Spy on `web.AppRunner.cleanup` and force `site.start()` to fail, matching the
existing `serve_ai` cleanup test. Inspect `AiRouter.run` source and assert its
first executable statement is `setup_logging(verbose=self.verbose)`.

- [ ] **Step 2: Run lifecycle tests and confirm `AiRouter` is missing**

Run: `uv run pytest tests/psi_agent/router/test_router.py -v`

Expected: import failure for `AiRouter`.

- [ ] **Step 3: Implement `serve_router` and `AiRouter`**

Use this dataclass field order and matching assignment order:

```python
@dataclass
class AiRouter:
    session_socket: str
    router_model: str = ""
    router_base_url: str = ""
    router_api_key: str = ""
    upstream: list[str] = field(default_factory=list)
    default_addr: str = ""
    router_timeout: float | None = None
    router_context_chars: int = 12_000
    log_router_details: bool = False
    verbose: bool = False
```

`run()` resolves environment values, validates them, calls `parse_upstreams`,
then calls `serve_router`. `serve_router` creates typed app state, registers
`POST /chat/completions`, shields runner cleanup on startup failure and normal
shutdown, and waits via `anyio.sleep_forever()`.

- [ ] **Step 4: Add failing CLI shape tests**

Patch `sys.argv` and `anyio.run`, invoke `psi_agent.cli.main()`, and assert:

- ordinary AI invocation creates `Ai`;
- `ai router` creates `AiRouter`;
- two JSON values following `--upstream` arrive in order;
- `--default-addr`, `--router-context-chars`, and boolean flags parse correctly;
- `ai router --help` exits successfully and contains the new option names.

- [ ] **Step 5: Implement the nested `AiGroup` union**

In `src/psi_agent/cli.py`, define an annotated union that keeps the ordinary AI
variant and adds the router variant under the `ai` command. Verify tyro's exact
generated syntax with the tests; prefer an explicit default/ordinary
subcommand name only if tyro cannot preserve `psi-agent ai --provider ...`.
Do not silently remove the existing ordinary AI CLI shape. If tyro requires
`psi-agent ai direct`, document and test that intentional compatibility break
before proceeding.

- [ ] **Step 6: Run lifecycle and CLI tests**

Run: `uv run pytest tests/psi_agent/router/test_router.py tests/psi_agent/test_cli.py -v`

Expected: all tests pass.

Run: `uv run psi-agent ai router --help`

Expected: help includes `--upstream`, `--default-addr`, and router options.

Run: `uv run psi-agent ai --help`

Expected: ordinary AI startup remains discoverable.

- [ ] **Step 7: Commit lifecycle and CLI integration**

```bash
git add src/psi_agent/router/__init__.py src/psi_agent/cli.py tests/psi_agent/router/test_router.py tests/psi_agent/test_cli.py
git commit -m "feat(router): expose semantic router CLI"
```

### Task 7: End-to-end local integration

**Files:**
- Create: `tests/integration/test_semantic_router.py`

- [ ] **Step 1: Add a local multi-server semantic routing test**

Create pre-bound TCP sockets for a routing-model server, simple upstream,
complex upstream, and default upstream. The router-model handler inspects the
serialized task and returns candidate 0 for a simple summary request and
candidate 1 for a code-analysis request. Each business upstream records its
request and emits a distinct valid ChatCompletionChunk plus `[DONE]`.

Start `serve_router` in an anyio task group and call it through
`AiClient` or the same connector boundary Session uses. Assert:

```python
assert simple_requests[0]["model"] == "qwen"
assert complex_requests[0]["model"] == "deepseek"
assert default_requests == []
```

- [ ] **Step 2: Run the integration test and confirm missing fixture/helper failures**

Run: `uv run pytest tests/integration/test_semantic_router.py -v`

Expected: fail until all local server setup and router calls are wired.

- [ ] **Step 3: Complete the local integration fixture and make semantic routing green**

Use only aiohttp/anyio operations, consume every process/server stream, wait
for service readiness after socket creation, and cancel the task group in a
`finally` block. Do not access external network services.

- [ ] **Step 4: Add fallback and protocol-preservation cases**

Add cases where the routing model returns damaged content and where it delays
beyond `router_timeout`; both must hit `default_addr` and preserve the original
model. Add a tool-call SSE response containing `delta.tool_calls` and assert it
survives byte-for-byte through the router. Add an upstream error case and
assert Session does not commit that failed turn to conversation history.

- [ ] **Step 5: Run integration and adjacent protocol tests**

Run: `uv run pytest tests/integration/test_semantic_router.py tests/integration/test_ai_error_handling.py tests/psi_agent/session/test_ai_client.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit integration coverage**

```bash
git add tests/integration/test_semantic_router.py
git commit -m "test(router): cover semantic routing end to end"
```

### Task 8: Documentation and final verification

**Files:**
- Create: `src/psi_agent/router/AGENTS.md`
- Modify: `AGENTS.md`
- Modify: `src/psi_agent/ai/AGENTS.md`
- Modify: `README.md`
- Modify: `README_en.md`

- [ ] **Step 1: Write router maintenance documentation**

Document these non-obvious invariants in `src/psi_agent/router/AGENTS.md`:

- routing prompts expose only candidate indices and descriptions;
- `model_name`, addresses, and API keys remain local;
- semantic success overwrites `model`, default fallback preserves it;
- default failure is terminal and never recursively falls back;
- normal SSE is proxied as bytes and every chunk is logged at DEBUG;
- startup and shutdown cleanup are shielded;
- async generators/connections must be explicitly closed on early exit.

- [ ] **Step 2: Synchronize architecture and usage docs**

Add `src/psi_agent/router/` to the root architecture tree and explain the
router's place between Session and candidate AI services. Update AI-layer docs
to distinguish ordinary provider forwarding from `ai router`. Add the approved
PowerShell command and an equivalent POSIX example to both READMEs. Explain
the exact upstream JSON schema, `default_addr` behavior, address path
normalization, and environment variables.

- [ ] **Step 3: Run documentation stale-term checks**

Run:

```text
rg -n "default-model|\"tcp\"|third-party llmrouter" AGENTS.md README.md README_en.md src/psi_agent/router src/psi_agent/ai/AGENTS.md
```

Expected: no stale configuration names; a mention of third-party llmrouter is
allowed only when explicitly saying it is not used.

- [ ] **Step 4: Run the full verification suite**

Run each command separately and require exit code 0:

```text
uv run ruff format .
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -v
uv run psi-agent ai router --help
uv run psi-agent ai --help
```

If a command fails, invoke `superpowers:systematic-debugging`, identify the root
cause, add or adjust a regression test, and rerun the focused test before the
full suite. Do not weaken lint/type configuration and do not add suppressions.

- [ ] **Step 5: Review the Definition of Done explicitly**

Inspect documentation synchronization, log levels, cancellation paths, and
test coverage against the root `AGENTS.md` checklist. Run `git diff --check`
and `git status --short`; confirm the user's pre-existing `.gitignore` change
is not staged or modified by this work.

- [ ] **Step 6: Commit documentation and any final verified adjustments**

```bash
git add AGENTS.md README.md README_en.md src/psi_agent/ai/AGENTS.md src/psi_agent/router/AGENTS.md
git commit -m "docs: document semantic model routing"
```

- [ ] **Step 7: Perform completion verification immediately before handoff**

Invoke `superpowers:verification-before-completion`, rerun the commands it
requires, quote the fresh pass/fail evidence, and only then report the feature
as complete.
