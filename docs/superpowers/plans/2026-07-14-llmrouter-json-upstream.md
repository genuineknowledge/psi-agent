# LLMRouter JSON Upstream Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the semantic-router demo with a directly installed `llmrouter-lib==0.3.1` adapter that selects one JSON-described upstream and preserves psi-agent's OpenAI-compatible SSE proxy.

**Architecture:** `LLMRouterAdapter` parses candidate JSON, serializes bounded conversation context, builds one reusable `LLMMultiRoundRouter`, invokes its private routing method in worker threads under a process-wide credential lock, validates routes, and applies deterministic majority voting. `AiRouter` owns startup, explicit model override, fallback, and proxying the untouched Chat Completions request to the selected target.

**Tech Stack:** Python 3.14, anyio, aiohttp, loguru, tyro, pytest, ruff, ty, `llmrouter-lib==0.3.1`.

---

## Working-tree safety

The current branch already contains modified and untracked router files. Before
each task, inspect `git diff` for every file being changed. Do not restore,
overwrite, stage, or commit unrelated user changes. Each commit command below
must stage only the listed files; if a listed file already contains unrelated
changes, omit the commit and report that the slice remains uncommitted rather
than capturing unrelated work.

## File map

- Create `src/psi_agent/ai/llmrouter_adapter.py`: JSON schema, context serializer,
  runtime files, LLMRouter lifecycle, credential isolation, route validation,
  majority vote, timeout-safe activity tracking.
- Rewrite `src/psi_agent/ai/router.py`: request orchestration and SSE proxy only.
- Modify `src/psi_agent/ai/__init__.py`: retain `AiRouter` export.
- Modify `src/psi_agent/cli.py`: retain the `ai router` dispatch with the new
  dataclass fields.
- Create `tests/psi_agent/ai/test_llmrouter_adapter.py`: adapter unit and
  concurrency tests.
- Rewrite `tests/psi_agent/ai/test_router.py`: JSON CLI and proxy integration.
- Remove `tests/psi_agent/ai/test_router_semantic.py`: obsolete behavior.
- Modify `pyproject.toml` and `uv.lock`: direct LLMRouter dependency and removal
  of semantic-router.
- Modify `AGENTS.md`, `src/psi_agent/ai/AGENTS.md`, `README.md`, and
  `README_en.md`: final behavior and limitations.

### Task 1: Parse strict JSON upstream candidates

**Files:**
- Create: `src/psi_agent/ai/llmrouter_adapter.py`
- Create: `tests/psi_agent/ai/test_llmrouter_adapter.py`

- [ ] **Step 1: Write failing schema tests**

```python
from __future__ import annotations

import json

import pytest

from psi_agent.ai.llmrouter_adapter import RouteTarget, parse_upstreams


def test_parse_upstreams_accepts_json_array() -> None:
    raw = json.dumps(
        [
            {
                "addr": "./qwen.sock",
                "model": "qwen-plus",
                "description": "通用中文问答",
            },
            {
                "addr": "./reasoner.sock",
                "model": "deepseek-reasoner",
                "description": "复杂推理与代码分析",
            },
        ],
        ensure_ascii=False,
    )
    assert parse_upstreams(raw) == [
        RouteTarget("./qwen.sock", "qwen-plus", "通用中文问答"),
        RouteTarget("./reasoner.sock", "deepseek-reasoner", "复杂推理与代码分析"),
    ]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not-json", "valid JSON"),
        ("{}", "non-empty JSON array"),
        ("[]", "non-empty JSON array"),
        ('[{"addr":"a","model":"m"}]', "description"),
        (
            '[{"addr":"a","model":"m","description":"d","api_key":"secret"}]',
            "unsupported fields",
        ),
        (
            '[{"addr":"a","model":"m","description":"d"},'
            '{"addr":"b","model":"m","description":"d2"}]',
            "duplicate upstream model",
        ),
    ],
)
def test_parse_upstreams_rejects_invalid_values(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_upstreams(raw)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
uv run pytest tests/psi_agent/ai/test_llmrouter_adapter.py -v
```

Expected: collection fails because `psi_agent.ai.llmrouter_adapter` does not
exist.

- [ ] **Step 3: Implement the minimal schema parser**

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RouteTarget:
    addr: str
    model: str
    description: str


def _required_text(item: dict[str, Any], key: str, location: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def parse_upstreams(raw: str) -> list[RouteTarget]:
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("--upstream must be valid JSON") from exc
    if not isinstance(payload, list) or not payload:
        raise ValueError("--upstream must be a non-empty JSON array")
    targets: list[RouteTarget] = []
    models: set[str] = set()
    allowed = {"addr", "model", "description"}
    for index, value in enumerate(payload):
        location = f"upstream[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{location} must be an object")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"{location} has unsupported fields: {sorted(unknown)!r}")
        target = RouteTarget(
            addr=_required_text(value, "addr", location),
            model=_required_text(value, "model", location),
            description=_required_text(value, "description", location),
        )
        if target.model in models:
            raise ValueError(f"duplicate upstream model: {target.model!r}")
        models.add(target.model)
        targets.append(target)
    return targets
```

- [ ] **Step 4: Run tests and verify GREEN**

Run the command from Step 2. Expected: all schema tests pass.

- [ ] **Step 5: Run focused quality checks**

```powershell
uv run ruff check src/psi_agent/ai/llmrouter_adapter.py tests/psi_agent/ai/test_llmrouter_adapter.py
uv run ty check src/psi_agent/ai/llmrouter_adapter.py tests/psi_agent/ai/test_llmrouter_adapter.py
```

Expected: both commands exit zero.

### Task 2: Serialize bounded conversation context

**Files:**
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`

- [ ] **Step 1: Write failing context tests**

```python
from psi_agent.ai.llmrouter_adapter import serialize_context


def test_serialize_context_keeps_recent_conversation_and_omits_tool_body() -> None:
    messages = [
        {"role": "system", "content": "Python framework"},
        {"role": "user", "content": "Find the cancellation leak"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file", "arguments": "secret"}}]},
        {"role": "tool", "content": "large sensitive tool result"},
        {"role": "user", "content": "Now propose the patch"},
    ]
    result = serialize_context(messages, max_chars=1_000)
    assert "[SYSTEM]\nPython framework" in result
    assert "Find the cancellation leak" in result
    assert "Now propose the patch" in result
    assert "read_file" in result
    assert "large sensitive tool result" not in result
    assert "secret" not in result


def test_serialize_context_truncates_latest_user_to_budget() -> None:
    result = serialize_context(
        [{"role": "user", "content": "x" * 100}],
        max_chars=32,
    )
    assert len(result) <= 32
    assert result.endswith("[TRUNCATED]")


def test_serialize_context_returns_empty_without_user_text() -> None:
    assert serialize_context([{"role": "assistant", "content": "hello"}], max_chars=100) == ""
```

- [ ] **Step 2: Run the new tests and verify RED**

Expected: import failure for `serialize_context`.

- [ ] **Step 3: Implement role-safe serialization**

Implement guarded extraction and tail selection with this public shape:

```python
def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for value in content:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            text = value.get("text")
            item_type = value.get("type")
            if isinstance(text, str):
                parts.append(text)
            elif item_type in {"image", "image_url", "input_image"}:
                parts.append("[IMAGE]")
            elif item_type in {"audio", "input_audio"}:
                parts.append("[AUDIO]")
            elif item_type in {"file", "input_file"}:
                parts.append("[FILE]")
    return "\n".join(part for part in parts if part)


def serialize_context(messages: Any, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("router_context_chars must be positive")
    if not isinstance(messages, list):
        return ""
    system = ""
    blocks: list[tuple[str, str]] = []
    has_user = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if not isinstance(role, str):
            continue
        if role == "system" and not system:
            system = _content_text(message.get("content"))
        elif role in {"user", "assistant"}:
            text = _content_text(message.get("content"))
            if role == "assistant":
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    names = [
                        function.get("name")
                        for call in tool_calls
                        if isinstance(call, dict)
                        and isinstance((function := call.get("function")), dict)
                        and isinstance(function.get("name"), str)
                    ]
                    if names:
                        text = f"{text}\n[TOOLS] {', '.join(names)}".strip()
            if text:
                has_user = has_user or role == "user"
                blocks.append((role.upper(), text))
        elif role == "tool":
            blocks.append(("TOOLS", "Tool results exist; result bodies are omitted."))
    if not has_user:
        return ""
    rendered = [f"[{role}]\n{text}" for role, text in blocks]
    if system:
        rendered.insert(0, f"[SYSTEM]\n{system}")
    while len("\n\n".join(rendered)) > max_chars and len(rendered) > 1:
        rendered.pop(1 if rendered[0].startswith("[SYSTEM]") else 0)
    result = "\n\n".join(rendered)
    if len(result) > max_chars:
        marker = "[TRUNCATED]"
        result = result[: max_chars - len(marker)] + marker
    return result
```

Refactor if needed after tests are green, but retain the guarded behavior and
the exact `serialize_context(messages: Any, *, max_chars: int) -> str` API.

- [ ] **Step 4: Run all adapter tests and verify GREEN**

```powershell
uv run pytest tests/psi_agent/ai/test_llmrouter_adapter.py -v
```

Expected: schema and context tests pass.

### Task 3: Generate runtime files and construct LLMRouter once

**Files:**
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`

- [ ] **Step 1: Write failing runtime lifecycle tests**

```python
import json

import pytest
import yaml

from psi_agent.ai.llmrouter_adapter import LLMRouterAdapter


@pytest.mark.anyio
async def test_adapter_writes_candidate_only_runtime_config(tmp_path, monkeypatch) -> None:
    built: list[str] = []

    class FakeRouter:
        def _decompose_and_route(self, context: str):
            return [(context, "qwen-plus")]

    def fake_build(path: str):
        built.append(path)
        return FakeRouter()

    monkeypatch.setattr(LLMRouterAdapter, "_build_router_sync", staticmethod(fake_build))
    adapter = LLMRouterAdapter(
        router_model="router-small",
        router_base_url="https://router.example/v1",
        router_api_key="secret",
        targets=[RouteTarget("./qwen.sock", "qwen-plus", "中文问答")],
        runtime_root=str(tmp_path),
    )
    await adapter.start()
    assert len(built) == 1
    runtime_yaml = yaml.safe_load(await adapter.runtime_yaml.read_text(encoding="utf-8"))
    llm_data = json.loads(await adapter.llm_data.read_text(encoding="utf-8"))
    assert runtime_yaml["base_model"] == "router-small"
    assert runtime_yaml["data_path"]["llm_data"] == str(adapter.llm_data)
    assert llm_data == {"qwen-plus": {"feature": "中文问答", "model": "qwen-plus"}}
    assert "secret" not in await adapter.llm_data.read_text(encoding="utf-8")
    await adapter.close()
```

- [ ] **Step 2: Run the lifecycle test and verify RED**

Expected: `LLMRouterAdapter` does not exist.

- [ ] **Step 3: Implement start/build/close**

Create a dataclass/class that stores immutable config, uses `anyio.Path` for
runtime IO, writes JSON/YAML once, and constructs the real class in a worker:

```python
from llmrouter.models.llmmultiroundrouter import LLMMultiRoundRouter

return LLMMultiRoundRouter(yaml_path=runtime_yaml)
```

Expose `runtime_yaml` and `llm_data` as `anyio.Path`. Make `start()` idempotent,
reject routing before start/after close, and shield cleanup in `close()`.

- [ ] **Step 4: Run adapter tests and verify GREEN**

Expected: all tests pass and construction occurs once.

### Task 4: Invoke LLMRouter with safe credentials and majority voting

**Files:**
- Modify: `src/psi_agent/ai/llmrouter_adapter.py`
- Modify: `tests/psi_agent/ai/test_llmrouter_adapter.py`

- [ ] **Step 1: Write failing route/vote tests**

```python
@pytest.mark.anyio
async def test_route_selects_majority_and_restores_api_keys(monkeypatch, started_adapter) -> None:
    monkeypatch.setenv("API_KEYS", "original")
    started_adapter.router.result = [
        ("analyze", "reasoner"),
        ("write", "coder"),
        ("verify", "reasoner"),
    ]
    decision = await started_adapter.route("context")
    assert decision.target.model == "reasoner"
    assert decision.votes == {"reasoner": 2, "coder": 1}
    assert decision.routes[0] == ("analyze", "reasoner")
    assert os.environ["API_KEYS"] == "original"


@pytest.mark.anyio
async def test_route_tie_uses_first_valid_model(started_adapter) -> None:
    started_adapter.router.result = [("first", "coder"), ("second", "reasoner")]
    assert (await started_adapter.route("context")).target.model == "coder"
```

- [ ] **Step 2: Run tests and verify RED**

Expected: `route()` or `RouteDecision` is missing.

- [ ] **Step 3: Implement worker wrapper and vote algorithm**

Add module-level `threading.Lock`, a synchronous wrapper that sets/restores
`API_KEYS` inside the lock and calls `_decompose_and_route`, and async
`route()` using `anyio.to_thread.run_sync(..., abandon_on_cancel=True)`. Validate
the result list, ignore malformed/unknown pairs, count votes, and choose the
first valid model among tied maxima. Return:

```python
@dataclass(frozen=True)
class RouteDecision:
    target: RouteTarget
    routes: tuple[tuple[str, str], ...]
    votes: dict[str, int]
    source: str = "llmrouter_majority"
```

- [ ] **Step 4: Add deterministic concurrency tests**

Use `threading.Event` to hold worker A while worker B begins. Assert B does not
enter the fake router or replace `API_KEYS` until A releases; then assert both
restore the original environment. Add an exception path that also restores.

- [ ] **Step 5: Run adapter tests and quality checks**

```powershell
uv run pytest tests/psi_agent/ai/test_llmrouter_adapter.py -v
uv run ruff check src/psi_agent/ai/llmrouter_adapter.py tests/psi_agent/ai/test_llmrouter_adapter.py
uv run ty check src/psi_agent/ai/llmrouter_adapter.py tests/psi_agent/ai/test_llmrouter_adapter.py
```

Expected: all pass without new ignores.

### Task 5: Replace Router policies with LLMRouter orchestration

**Files:**
- Rewrite: `src/psi_agent/ai/router.py`
- Rewrite: `tests/psi_agent/ai/test_router.py`
- Remove: `tests/psi_agent/ai/test_router_semantic.py`

- [ ] **Step 1: Write failing request-selection integration tests**

Use inline aiohttp mock upstreams and a fake adapter. Cover:

```python
@pytest.mark.anyio
async def test_router_proxies_only_to_llmrouter_winner(...):
    adapter.decision = RouteDecision(
        target=targets[1],
        routes=(("reason", targets[1].model),),
        votes={targets[1].model: 1},
    )
    response = await post_router(messages=[{"role": "user", "content": "solve"}])
    assert first_requests == []
    assert second_requests[0]["model"] == targets[1].model
    assert response contains the second upstream SSE chunk


@pytest.mark.anyio
async def test_request_model_bypasses_llmrouter(...):
    await post_router(model=targets[0].model)
    assert adapter.calls == []
    assert first_requests
```

Add fallback tests for adapter exception, timeout, missing user context, explicit
default, and implicit first candidate.

- [ ] **Step 2: Run router tests and verify RED**

Expected: current semantic-policy implementation does not accept the new
adapter/config contract.

- [ ] **Step 3: Rewrite router.py around the adapter**

Retain error payload/chunk helpers, `[DONE]` detection, transport resolution,
SSE headers, aiohttp context managers, startup/shutdown shielding, and
`setup_logging` placement. Delete semantic imports, encoders, heuristic tables,
caches, policies, round-robin state, route-model parsing, and candidate
credentials. Add AppKeys for targets, adapter, default target, timeout, context
budget, and detail logging.

Request selection order:

```text
known body.model -> request_model decision
no serialized context -> fallback
adapter success -> llmrouter_majority decision
adapter error/timeout -> fallback_default or fallback_first
```

Before proxying, copy/normalize the body, remove internal `routing`, and set the
selected candidate model. Do not insert route diagnostics in the streamed
response.

Use these core functions so selection and fallback stay independently tested:

```python
def _fallback_decision(default_target: RouteTarget, *, explicit: bool) -> RouteDecision:
    return RouteDecision(
        target=default_target,
        routes=(),
        votes={},
        source="fallback_default" if explicit else "fallback_first",
    )


async def _select_destination(app: web.Application, body: dict[str, Any]) -> RouteDecision:
    targets = app[_ROUTE_TARGETS_KEY]
    requested = body.get("model")
    if isinstance(requested, str):
        for target in targets:
            if target.model == requested:
                return RouteDecision(target=target, routes=(), votes={}, source="request_model")
    context = serialize_context(body.get("messages"), max_chars=app[_CONTEXT_CHARS_KEY])
    if not context:
        return app[_FALLBACK_KEY]
    timeout = app[_ROUTER_TIMEOUT_KEY]
    try:
        if timeout is None:
            return await app[_LLMROUTER_KEY].route(context)
        with anyio.move_on_after(timeout) as scope:
            decision = await app[_LLMROUTER_KEY].route(context)
        if scope.cancel_called:
            return app[_FALLBACK_KEY]
        return decision
    except Exception as exc:
        logger.warning(f"LLMRouter failed; using fallback: {exc!r}")
        return app[_FALLBACK_KEY]
```

- [ ] **Step 4: Run router tests and verify GREEN**

```powershell
uv run pytest tests/psi_agent/ai/test_router.py -v
```

Expected: JSON routing, override, fallback, and SSE proxy tests pass.

### Task 6: Finalize CLI, lifecycle, and timeout behavior

**Files:**
- Modify: `src/psi_agent/ai/router.py`
- Modify: `src/psi_agent/ai/__init__.py`
- Modify: `src/psi_agent/cli.py`
- Modify: `tests/psi_agent/ai/test_router.py`

- [ ] **Step 1: Add failing startup-validation tests**

Test missing router model/base URL, empty allowed API key, invalid JSON,
unmatched default model, non-positive/NaN/infinite timeout, non-positive context
budget, adapter startup failure cleanup, and adapter close on Router shutdown.

- [ ] **Step 2: Implement resolved configuration and lifecycle**

Keep `setup_logging()` first, resolve `PSI_ROUTER_MODEL`,
`PSI_ROUTER_BASE_URL`, and `PSI_ROUTER_API_KEY`, parse candidates, resolve the
default target, validate numeric settings, start the adapter before serving,
and shield adapter/server cleanup. For a finite timeout, wrap adapter routing in
`anyio.move_on_after`; fallback when `cancel_called` is true.

- [ ] **Step 3: Verify CLI help contract**

```powershell
uv run psi-agent ai router --help
```

Expected: output contains `router-model`, `router-base-url`, `router-api-key`,
`upstream`, `default-model`, `router-timeout`, `router-context-chars`, and
`log-router-details`; it omits `route-model` and `policy`.

- [ ] **Step 4: Run AI-layer tests**

```powershell
uv run pytest tests/psi_agent/ai -v
```

Expected: all AI tests pass.

### Task 7: Replace dependencies and prove the real import

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Confirm the current dependency diff before editing**

```powershell
git diff -- pyproject.toml uv.lock
```

Expected: inspect and preserve any unrelated user changes; if present, stop and
separate them before updating dependencies.

- [ ] **Step 2: Replace the dependency**

Change the dependency list from `semantic-router` to:

```toml
"llmrouter-lib==0.3.1",
```

- [ ] **Step 3: Resolve and synchronize**

```powershell
uv lock
uv sync
```

Expected: both commands finish successfully on Python 3.14. If either fails,
capture the exact resolver/build error and stop; do not weaken project Python
requirements or vendor the library without a new user decision.

- [ ] **Step 4: Verify the pinned API**

```powershell
uv run python -c "from llmrouter.models.llmmultiroundrouter import LLMMultiRoundRouter; assert hasattr(LLMMultiRoundRouter, '_decompose_and_route'); print(LLMMultiRoundRouter)"
```

Expected: class prints and assertion passes.

- [ ] **Step 5: Rerun adapter and router tests with the real dependency installed**

```powershell
uv run pytest tests/psi_agent/ai/test_llmrouter_adapter.py tests/psi_agent/ai/test_router.py -v
```

Expected: all pass.

### Task 8: Synchronize documentation

**Files:**
- Modify: `AGENTS.md`
- Modify: `src/psi_agent/ai/AGENTS.md`
- Modify: `README.md`
- Modify: `README_en.md`

- [ ] **Step 1: Update architecture and CLI examples**

Document one router model, the separate model/base URL/API key fields, the
single JSON upstream array, required `addr/model/description`, and a copyable
PowerShell example.

- [ ] **Step 2: Document behavior and limitations**

Document bounded context, omitted tool bodies, majority voting, first-route tie
break, request-model override, deterministic fallback, no retry, private API
pin, direct heavy dependency, process-global credential serialization,
abandoned worker behavior, opt-in detail logging, and package-size risk.

- [ ] **Step 3: Search for stale behavior**

```powershell
rg -n "semantic-router|--route-model|round_robin|difficulty|policy.*semantic|Router Demo" AGENTS.md src/psi_agent/ai/AGENTS.md README.md README_en.md docs
```

Expected: remaining matches occur only in superseded historical design docs or
explicit migration notes.

- [ ] **Step 4: Run documentation-sensitive CLI check**

Run `uv run psi-agent ai router --help` and compare every documented flag.

### Task 9: Full verification and packaging audit

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run format, lint, and type checks**

```powershell
uv run ruff format .
uv run ruff check .
uv run ty check
```

Expected: all exit zero with no new suppressions.

- [ ] **Step 2: Run the complete test suite**

```powershell
uv run pytest -v
```

Expected: all tests pass, including integration tests; schedule tests may only
be excluded if the repository's standard command excludes them.

- [ ] **Step 3: Build the package**

```powershell
uv build
```

Expected: wheel and source distribution build successfully with the direct
LLMRouter dependency metadata.

- [ ] **Step 4: Audit desktop packaging impact**

Inspect Gateway/PyInstaller configuration and run the repository's documented
Windows packaging smoke command when available. Record missing dynamic imports,
DLL failures, and build-size change. Do not claim desktop packaging is healthy
if only `uv build` passed.

- [ ] **Step 5: Verify diff hygiene**

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only intended Router, dependency, test, and
documentation changes are attributable to this implementation. Report any
pre-existing dirty files separately.
