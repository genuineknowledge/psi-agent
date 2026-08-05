# Serial Fallback Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 psi-agent Router 增加按配置顺序串行容灾的 Fallback 模式，并让 Routing、Aggregation、Fallback 与普通 AI 通过类型化 upstream 任意组成无环调用图。

**Architecture:** Router 根包继续提供 mode-neutral HTTP/SSE、Socket 客户端、请求复制和隐私工具；新增共享 `buffered_complete()`，让 `fallback/` 在确认一个候选完整成功后才重放原始 events。类型化 AI/Router 边决定是否传播私有 `routing.session_id/path`，Gateway 用 backend type/ID 表达同一依赖图，所有策略只依赖统一 Chat Completions/SSE 契约。

**Tech Stack:** Python 3.14+、AnyIO、aiohttp、OpenAI-compatible Chat Completions/SSE、dataclasses、tyro、PyYAML、pytest/pytest-anyio、Vue 3、Pinia、Vitest、Vite、ruff、ty。

**Design Spec:** `docs/superpowers/specs/2026-08-05-serial-fallback-router-design.md`

## Global Constraints

- 所有异步 IO 使用 AnyIO；禁止新增原生 `asyncio`、同步 subprocess、`time.sleep()` 或 async 上下文中的 `pathlib` IO。
- 每个组件 `async def run(self)` 的第一行可执行语句必须是 `setup_logging(verbose=self.verbose)`。
- 每个有效 SSE event 恰好一个 choice；0 choice 跳过，多 choice 抛错；`compaction_needed` 不覆盖真实终态。
- 每个 async generator 必须通过 `aclosing()` 消费；取消、提前退出和失败都必须关闭上游。
- 取消异常原样传播；aiohttp session/response/runner 的跨 await cleanup 使用 shielded `CancelScope`。
- `copy_public_request_body()` 继续深拷贝、删除 `model/routing`、强制 `stream=True` 并透传其他未知字段。
- 只有显式 `backend_type="router"` 的边可以传播规范化的 `routing.session_id/path`；AI、Selector、Aggregator 永不接收该字段。
- Fallback 严格串行，首个完整成功才输出；失败候选的任何 event 都不得离开 Fallback。
- Fallback 工具轮从 sticky 候选继续，失败后只向配置后方回退，不回绕。
- target Socket 不得进入 prompt、外部错误、REST/state/upstream 元数据；错误摘要逐项最多 512 字符。
- 原始 Socket 配置只拒绝直接 self-loop；不增加拓扑发现、运行时环检测、重试、熔断、健康检查或负载均衡。
- 继续保持零 `noqa`、零 `per-file-ignores`，并保持精确容器类型与关键字参数风格。
- 当前工作区已有 `modelPresets.js`、`modelPresets.test.js`、`providers.js` 的用户改动；实施任务不得覆盖、格式化或提交这些无关改动。

---

## File and Responsibility Map

### Shared Router layer

- `src/psi_agent/router/models.py`：`RouterMode.FALLBACK`、backend/upstream/scope 类型、`RouterTarget.backend_type`、`BufferedCompletion`。
- `src/psi_agent/router/request.py`：解析私有 scope，区分 AI/Router edge 并复制请求。
- `src/psi_agent/router/privacy.py`：Aggregation/Fallback 共用的 Socket 脱敏与长度限制。
- `src/psi_agent/router/client.py`：唯一 SSE 传输、`buffered_complete()`、兼容 `complete()`。
- `src/psi_agent/router/server.py`：校验 `routing.path`；保持 mode-neutral。
- `src/psi_agent/router/entry.py`：三模式 facade、二元/三元 upstream 与 CLI `upstream_types` 规范化。

### Mode packages

- `src/psi_agent/router/routing/strategy.py`：scope-aware sticky 和类型化 target 转发。
- `src/psi_agent/router/aggregation/strategy.py`：类型化并发分支与共享隐私 helper。
- `src/psi_agent/router/fallback/models.py`：`FallbackConfig`。
- `src/psi_agent/router/fallback/errors.py`：`FallbackError`。
- `src/psi_agent/router/fallback/strategy.py`：串行 attempt、完整成功判定、重放和 sticky。
- `src/psi_agent/router/fallback/entry.py`：独立 `FallbackRouter`。
- 两级 `__init__.py`：导出新的公共接口。

### Configuration and Gateway

- `src/psi_agent/_run.py`：YAML 二元/三元 upstream 规范化。
- `src/psi_agent/gateway/_router_manager.py`：AI/Router backend 引用、Fallback 生命周期、依赖删除保护。
- `src/psi_agent/gateway/_state.py`、`gateway/__init__.py`：新 upstream state schema、旧 `ai_id` 单向迁移和恢复。
- `src/psi_agent/gateway/server.py`、`_openapi.py`：REST/OpenAPI 条件字段、409、Fallback 标题/摘要 Socket。
- Gateway SPA Router store/config/dialog/list：Fallback 表单与 AI/Router upstream 选择。

### Tests and docs

- `tests/psi_agent/router/fallback/`：Fallback 模型、策略和入口单元测试。
- `tests/integration/test_fallback_router_composition.py`：3×3 相邻模式、六种三层排列、工具链和取消。
- 现有 Router/Gateway/CLI/YAML/SPA tests：兼容与新 schema。
- 根、Router、Gateway、SPA 文档：三模式与组合不变量。

---

### Task 1: Typed targets, private routing scope, and shared privacy helper

**Files:**

- Modify: `src/psi_agent/router/models.py:6-53`
- Modify: `src/psi_agent/router/request.py:1-16`
- Create: `src/psi_agent/router/privacy.py`
- Modify: `src/psi_agent/router/server.py:168-189`
- Modify: `tests/psi_agent/router/test_models.py`
- Modify: `tests/psi_agent/router/test_request.py`
- Create: `tests/psi_agent/router/test_privacy.py`
- Modify: `tests/psi_agent/router/test_server.py`

**Interfaces:**

- Produces `RouterBackendType = Literal["ai", "router"]`.
- Produces `RouterUpstream = tuple[str, str] | tuple[str, str, RouterBackendType]`.
- Produces `RoutingScopeKey = tuple[str, tuple[str, ...]]`.
- Extends `RouterTarget(..., backend_type: RouterBackendType = "ai")`.
- Produces `routing_scope_from_body(*, body) -> RoutingScopeKey | None`.
- Produces `copy_target_request_body(*, body, target) -> dict[str, Any]`.
- Produces `redact_private_sockets(*, text, sockets, limit=512) -> str`.

- [ ] **Step 1: Write failing model and request-copy tests**

Add exact backend validation and edge behavior:

```python
def test_router_target_defaults_to_ai_and_rejects_unknown_backend_type() -> None:
    target = RouterTarget("candidate-1", "one.sock", "one")
    assert target.backend_type == "ai"
    with pytest.raises(ValueError, match="backend_type"):
        RouterTarget("candidate-1", "one.sock", "one", cast(Any, "service"))


def test_router_target_copy_propagates_normalized_scope_and_appends_path() -> None:
    source = {
        "model": "private",
        "messages": [{"role": "user", "content": "hello"}],
        "routing": {"session_id": " session-a ", "path": ["candidate-2"]},
        "future": {"x": 1},
        "stream": False,
    }
    target = RouterTarget("candidate-1", "nested.sock", "nested", "router")
    copied = copy_target_request_body(body=source, target=target)
    assert copied["routing"] == {
        "session_id": "session-a",
        "path": ["candidate-2", "candidate-1"],
    }
    assert copied["future"] == {"x": 1}
    assert copied["stream"] is True
    assert "model" not in copied
    assert source["routing"]["path"] == ["candidate-2"]


def test_ai_target_copy_strips_private_routing_context() -> None:
    target = RouterTarget("candidate-1", "ai.sock", "ai", "ai")
    copied = copy_target_request_body(
        body={"messages": [], "routing": {"session_id": "s", "path": []}},
        target=target,
    )
    assert "routing" not in copied
```

Add parameterized `routing_scope_from_body()` failures for non-dict routing, empty/non-string session ID, non-list path, invalid path item, and a non-empty path without session ID. Extend `test_server.py` so each becomes HTTP 400 before response prepare.

- [ ] **Step 2: Write failing privacy tests**

```python
def test_redact_private_sockets_replaces_raw_repr_and_escaped_forms() -> None:
    sockets = [r"\\.\pipe\private", "http://127.0.0.1:9876"]
    text = " ".join(value for socket in sockets for value in (socket, repr(socket), repr(socket)[1:-1]))
    redacted = redact_private_sockets(text=text + "x" * 800, sockets=sockets)
    assert len(redacted) == 512
    assert "<private-socket>" in redacted
    assert all(socket not in redacted for socket in sockets)
```

Also assert overlapping Socket names are replaced longest-first and `limit=0` returns `""`.

- [ ] **Step 3: Run targeted tests and verify RED**

Run:

```text
uv run pytest -q tests/psi_agent/router/test_models.py tests/psi_agent/router/test_request.py tests/psi_agent/router/test_privacy.py tests/psi_agent/router/test_server.py
```

Expected: FAIL because backend types, scope parsing, Router-edge copying, privacy helper, and path validation do not exist.

- [ ] **Step 4: Implement the shared contracts**

In `models.py`, retain `_CANDIDATE_ID` as the single validator and add:

```python
type RouterBackendType = Literal["ai", "router"]
type RouterUpstream = tuple[str, str] | tuple[str, str, RouterBackendType]
type RoutingScopeKey = tuple[str, tuple[str, ...]]


def is_candidate_id(value: object) -> TypeIs[str]:
    return isinstance(value, str) and _CANDIDATE_ID.fullmatch(value) is not None
```

Add `backend_type` after `description`, validate it against `{"ai", "router"}`, and preserve the existing whitespace normalization for the other fields.

In `request.py`, implement exact normalization:

```python
def routing_scope_from_body(*, body: dict[str, Any]) -> RoutingScopeKey | None:
    raw = body.get("routing")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise InvalidRouterRequestError("routing must be an object when present")
    raw_session_id = raw.get("session_id")
    raw_path = raw.get("path", [])
    if raw_session_id is None:
        if raw_path not in (None, []):
            raise InvalidRouterRequestError("routing.path requires routing.session_id")
        return None
    if not isinstance(raw_session_id, str) or not raw_session_id.strip():
        raise InvalidRouterRequestError("routing.session_id must be a non-empty string")
    if not isinstance(raw_path, list) or any(not is_candidate_id(item) for item in raw_path):
        raise InvalidRouterRequestError("routing.path must be a list of candidate IDs")
    return raw_session_id.strip(), tuple(raw_path)
```

Build `copy_target_request_body()` from `copy_public_request_body()`. Only a Router target with a non-None scope gets a reconstructed `routing` dict and appended candidate ID; never copy arbitrary keys from the caller's routing object.

In `privacy.py`, generate raw/repr/unquoted-repr representations, sort by `(-len(value), value)`, replace with `<private-socket>`, then slice to `limit`.

Replace `server.py`'s partial routing validation with `routing_scope_from_body(body=body)`; keep `_discard_session_state()` keyed by normalized session ID because each strategy's `discard()` removes all paths for that Session.

- [ ] **Step 5: Run targeted tests and verify GREEN**

Run the Step 3 command.

Expected: PASS; existing AI public-copy test remains unchanged, Router-edge scope is isolated, and invalid path data returns HTTP 400.

- [ ] **Step 6: Commit the shared edge contract**

```bash
git add src/psi_agent/router/models.py src/psi_agent/router/request.py src/psi_agent/router/privacy.py src/psi_agent/router/server.py tests/psi_agent/router/test_models.py tests/psi_agent/router/test_request.py tests/psi_agent/router/test_privacy.py tests/psi_agent/router/test_server.py
git diff --cached --check
git commit -m "feat(router): add typed composition edges"
```

---

### Task 2: Buffered completion primitive

**Files:**

- Modify: `src/psi_agent/router/models.py:44-53`
- Modify: `src/psi_agent/router/client.py:20-69`
- Modify: `src/psi_agent/router/__init__.py`
- Modify: `tests/psi_agent/router/test_client.py`

**Interfaces:**

- Produces `BufferedCompletion(events: tuple[dict[str, Any], ...], completion: CompletionResult)`.
- Produces `RouterHttpClient.buffered_complete(*, socket, body, **options) -> BufferedCompletion`.
- Preserves `RouterHttpClient.complete(...) -> CompletionResult` by delegation.

- [ ] **Step 1: Write failing event-preservation and delegation tests**

```python
@pytest.mark.anyio
async def test_buffered_complete_preserves_events_and_accumulates_result() -> None:
    content_event = {
        "id": "answer",
        "model": "nested-router",
        "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 1},
    }
    compaction_event = {
        "choices": [{"index": 0, "delta": {}, "finish_reason": "compaction_needed"}],
        "psi_compaction": {"needed": True, "prompt_tokens": 10, "threshold": 8},
    }
    # Serve content_event then compaction_event and [DONE].
    buffered = await RouterHttpClient().buffered_complete(
        socket=server_url,
        body={"messages": [], "stream": True},
        timeout=None,
    )
    assert buffered.events == (content_event, compaction_event)
    assert buffered.completion.content == "ok"
    assert buffered.completion.finish_reason == "stop"
```

Monkeypatch `buffered_complete()` in a second test and assert `complete()` returns the exact `.completion` object. Retain the existing fragmented tool-call tests as the compatibility oracle.

- [ ] **Step 2: Write failing cleanup/error tests for the buffered path**

Add tests that `buffered_complete()`:

- raises on `finish_reason="error"` without returning partial events;
- raises when only compaction is observed;
- closes the wrapped stream when cancellation interrupts accumulation;
- skips 0-choice heartbeats and rejects multiple choices through the existing decoder.

Use `anyio.Event` for cancellation synchronization; do not use fixed sleeps.

- [ ] **Step 3: Run client tests and verify RED**

Run: `uv run pytest -q tests/psi_agent/router/test_client.py`

Expected: FAIL because `BufferedCompletion` and `buffered_complete()` are absent.

- [ ] **Step 4: Move accumulation into `buffered_complete()`**

Add the model:

```python
@dataclass(frozen=True)
class BufferedCompletion:
    events: tuple[dict[str, Any], ...]
    completion: CompletionResult
```

Refactor without duplicating protocol logic:

```python
async def complete(self, *, socket: str, body: dict[str, Any], **options: Any) -> CompletionResult:
    buffered = await self.buffered_complete(socket=socket, body=body, **options)
    return buffered.completion


async def buffered_complete(
    self,
    *,
    socket: str,
    body: dict[str, Any],
    **options: Any,
) -> BufferedCompletion:
    request_timeout = self._timeout_from_options(options)
    events: list[dict[str, Any]] = []
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    stream = self.stream(socket=socket, body=body, timeout=request_timeout)
    async with aclosing(stream) as upstream:
        async for event in upstream:
            events.append(event)
            choice = event["choices"][0]
            delta = choice.get("delta", {})
            content = delta.get("content")
            if isinstance(content, str):
                content_parts.append(content)
            reasoning = delta.get("reasoning")
            if isinstance(reasoning, str):
                reasoning_parts.append(reasoning)
            self._accumulate_tool_calls(tool_calls, delta.get("tool_calls"))
            current_finish = choice.get("finish_reason")
            if current_finish == "compaction_needed":
                continue
            if isinstance(current_finish, str):
                if current_finish == "error":
                    detail = "".join(content_parts) or "unknown upstream error"
                    raise RouterUpstreamError(
                        f"Upstream {socket!r} reported an error: {detail}"
                    )
                finish_reason = current_finish

    if finish_reason is None:
        raise RouterUpstreamError(f"Upstream {socket!r} ended without a finish reason")
    ordered_calls = [tool_calls[index] for index in sorted(tool_calls)]
    self._validate_tool_calls(ordered_calls, finish_reason)
    return BufferedCompletion(
        events=tuple(events),
        completion=CompletionResult(
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            tool_calls=ordered_calls,
            finish_reason=finish_reason,
        ),
    )
```

Do not re-encode/re-decode events and do not rebuild their top-level metadata. Export `BufferedCompletion` from the root package.

- [ ] **Step 5: Run client and aggregation regression tests**

Run:

```text
uv run pytest -q tests/psi_agent/router/test_client.py tests/psi_agent/router/aggregation/test_strategy.py
```

Expected: PASS; Aggregation still receives `CompletionResult` and fragmented tool calls retain numeric ordering.

- [ ] **Step 6: Commit the buffered transport primitive**

```bash
git add src/psi_agent/router/models.py src/psi_agent/router/client.py src/psi_agent/router/__init__.py tests/psi_agent/router/test_client.py
git diff --cached --check
git commit -m "feat(router): buffer validated upstream completions"
```

---

### Task 3: Scope-aware Routing and typed Aggregation branches

**Files:**

- Modify: `src/psi_agent/router/routing/strategy.py:30-96`
- Modify: `src/psi_agent/router/aggregation/strategy.py:46-104`
- Modify: `tests/psi_agent/router/test_routing.py`
- Modify: `tests/psi_agent/router/aggregation/test_strategy.py`

**Interfaces:**

- Routing sticky map becomes `dict[RoutingScopeKey, SelectionResult]`.
- `discard(session_id)` removes every path for that Session.
- Routing and Aggregation use `copy_target_request_body()` for targets.
- Selector and Aggregator control calls continue using `copy_public_request_body()`.
- Aggregation error summaries use `redact_private_sockets()`.

- [ ] **Step 1: Write failing Routing path-isolation tests**

Extend `FakeClient` assertions with one AI target and one Router target. Add:

```python
@pytest.mark.anyio
async def test_routing_sticky_state_is_isolated_by_router_path() -> None:
    first = RouterTarget("one", "one.sock", "one", "router")
    second = RouterTarget("two", "two.sock", "two", "router")
    # Selector returns first for path-a and second for path-b; both return tool_calls.
    # Tool iterations for each path must reuse its own selection without invoking Selector.
    assert [call[0] for call in client.calls] == ["one.sock", "two.sock", "one.sock", "two.sock"]
```

Assert Router target bodies contain `path=[*incoming_path, selected.candidate_id]`; AI target bodies contain no `routing`. Call `strategy.discard("session-a")` and verify both path entries are removed.

- [ ] **Step 2: Write failing Aggregation typed-branch and shared-redaction tests**

Change one `_targets()` entry to `backend_type="router"` and assert its branch snapshot keeps `session_id` plus an appended path while the AI branch snapshot omits routing. Keep the Aggregator request assertion routing-free. Preserve existing concurrent-start and deterministic slot tests.

Monkeypatch `redact_private_sockets()` or assert the existing raw/repr Named Pipe cases still produce identical 512-character feedback.

- [ ] **Step 3: Run mode tests and verify RED**

Run:

```text
uv run pytest -q tests/psi_agent/router/test_routing.py tests/psi_agent/router/aggregation/test_strategy.py
```

Expected: FAIL because both strategies still call `copy_public_request_body()` for every target and Routing keys sticky state by session ID only.

- [ ] **Step 4: Update RoutingStrategy**

Parse scope once per request:

```python
scope = routing_scope_from_body(body=body)
selection = self._sticky_targets.get(scope) if scope is not None and is_tool_iteration else None
```

On a normal user turn, remove only the exact existing scope before selecting. Save a selection only when scope is not None. Build the selected request with `copy_target_request_body(body=body, target=selection.target)`.

Preserve the current completion/finally rule: only a fully consumed `finish_reason="tool_calls"` keeps sticky. Implement `discard()` without a single-call private helper:

```python
normalized = session_id.strip()
if normalized:
    stale = [scope for scope in self._sticky_targets if scope[0] == normalized]
    for scope in stale:
        self._sticky_targets.pop(scope, None)
```

- [ ] **Step 5: Update AggregationStrategy**

Each `collect()` receives `copy_target_request_body(body=body, target=target)`. The Aggregator body remains `copy_public_request_body(body=body)` before messages replacement.

Delete the in-method representation set and replace it with:

```python
private_sockets = (
    self.config.aggregator_socket,
    *(item.socket for item in self.config.targets),
)
summary = redact_private_sockets(text=str(error), sockets=private_sockets)
```

Do not change branch concurrency, ordered slots, reasoning omission, tool material, or cancellation propagation.

- [ ] **Step 6: Run mode tests and verify GREEN**

Run the Step 3 command.

Expected: PASS; existing mode behavior remains and nested Router branches receive isolated scope.

- [ ] **Step 7: Commit composition support in existing modes**

```bash
git add src/psi_agent/router/routing/strategy.py src/psi_agent/router/aggregation/strategy.py tests/psi_agent/router/test_routing.py tests/psi_agent/router/aggregation/test_strategy.py
git diff --cached --check
git commit -m "feat(router): propagate typed router scopes"
```

---

### Task 4: Fallback configuration and serial strategy

**Files:**

- Create: `src/psi_agent/router/fallback/__init__.py`
- Create: `src/psi_agent/router/fallback/errors.py`
- Create: `src/psi_agent/router/fallback/models.py`
- Create: `src/psi_agent/router/fallback/strategy.py`
- Create: `tests/psi_agent/router/fallback/__init__.py`
- Create: `tests/psi_agent/router/fallback/test_models.py`
- Create: `tests/psi_agent/router/fallback/test_strategy.py`

**Interfaces:**

- Produces `FallbackConfig(session_socket, targets, target_timeout=None)`.
- Produces `FallbackError(RouterError)`.
- Produces `FallbackStrategy(config, client)` implementing `RouterStrategy`.
- Consumes `_FallbackClient.buffered_complete(...) -> BufferedCompletion`.

- [ ] **Step 1: Write failing FallbackConfig tests**

Mirror the existing Routing/Aggregation configuration matrices. Explicitly test normalization to tuple and rejection of empty/non-string session Socket, no targets, non-`RouterTarget`, duplicate IDs/Sockets, direct self-target, and invalid timeout values `[0, -1, inf, nan, True, "5"]`.

```python
def test_fallback_config_preserves_priority_order_as_tuple() -> None:
    targets = [
        RouterTarget("candidate-1", "one.sock", "one"),
        RouterTarget("candidate-2", "two.sock", "two"),
    ]
    config = FallbackConfig(" fallback.sock ", targets, target_timeout=5)
    assert config.session_socket == "fallback.sock"
    assert config.targets == tuple(targets)
    assert config.target_timeout == 5
```

- [ ] **Step 2: Write the failing sequential success/failure tests**

Create `FakeFallbackClient` with `results: dict[str, BufferedCompletion | Exception]`, `calls`, `in_flight`, and `max_in_flight`. Its `buffered_complete()` increments/decrements the counters in `try/finally`.

Cover these exact outcomes:

```python
@pytest.mark.anyio
async def test_first_complete_usable_candidate_is_replayed_and_stops_polling() -> None:
    client = FakeFallbackClient({
        "one.sock": RouterUpstreamError("one failed"),
        "two.sock": buffered(content="winner", finish="stop", marker="two"),
        "three.sock": buffered(content="unused", finish="stop", marker="three"),
    })
    events = await collect(strategy(client).stream(body=user_body()))
    assert [call.socket for call in client.calls] == ["one.sock", "two.sock"]
    assert client.max_in_flight == 1
    assert events == list(client.results["two.sock"].events)
```

Add parameterized failures for whitespace-only content, reasoning-only, no usable fields, HTTP/protocol errors, and error finish supplied by the fake. Assert failed events never appear in output. Assert non-empty tool calls count as success.

- [ ] **Step 3: Write failing all-failed privacy and cancellation tests**

Assert failure text lists candidate IDs in configuration order, carries exception type, contains `<private-socket>`, omits every target Socket, and caps each stored summary at 512 characters.

Use `anyio.Event` to cancel while the first attempt is blocked. Assert the second target is never called and the cancellation exception leaves the strategy.

- [ ] **Step 4: Write failing sticky tool-chain tests**

Use bodies with the same `session_id` and path:

1. user turn: candidate 1 fails, candidate 2 returns `tool_calls`;
2. tool turn: candidate 2 is attempted first and fails, candidate 3 returns `tool_calls`;
3. next tool turn: candidate 3 starts first and returns content;
4. new user turn: order restarts at candidate 1.

Assert call order exactly:

```python
assert sockets == [
    "one.sock", "two.sock",
    "two.sock", "three.sock",
    "three.sock",
    "one.sock",
]
```

Add a different `routing.path` under the same session and prove it starts from candidate 1. Assert `discard(session_id)` clears every path and `clear()` clears all sessions.

- [ ] **Step 5: Run Fallback tests and verify RED**

Run: `uv run pytest -q tests/psi_agent/router/fallback`

Expected: FAIL at import because the package does not exist.

- [ ] **Step 6: Implement models, error, and client protocol**

Follow the exact validation style from `AggregationConfig`. Define:

```python
class _FallbackClient(Protocol):
    async def buffered_complete(
        self,
        *,
        socket: str,
        body: dict[str, Any],
        **options: Any,
    ) -> BufferedCompletion: ...
```

`FallbackError` subclasses `RouterError`. Export only current Fallback symbols from the subpackage.

- [ ] **Step 7: Implement the minimal serial strategy**

The core loop must have this shape (the initial messages guard uses the same object-list validation as Routing):

```python
messages = body.get("messages")
if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
    raise InvalidRouterRequestError("messages must be a list of objects")
scope = routing_scope_from_body(body=body)
is_tool_iteration = bool(messages) and messages[-1].get("role") == "tool"
start_index = self._sticky_targets.get(scope, 0) if scope is not None and is_tool_iteration else 0
if scope is not None and not is_tool_iteration:
    self._sticky_targets.pop(scope, None)

failures: list[tuple[RouterTarget, str, str]] = []
selected: tuple[int, BufferedCompletion] | None = None
for index in range(start_index, len(self.config.targets)):
    target = self.config.targets[index]
    try:
        buffered = await self.client.buffered_complete(
            socket=target.socket,
            body=copy_target_request_body(body=body, target=target),
            timeout=self.config.target_timeout,
        )
        result = buffered.completion
        if not result.content.strip() and not result.tool_calls:
            raise RouterUpstreamError("upstream returned no usable content or tool calls")
        selected = index, buffered
        break
    except anyio.get_cancelled_exc_class():
        raise
    except Exception as error:
        summary = redact_private_sockets(
            text=str(error),
            sockets=(item.socket for item in self.config.targets),
        )
        failures.append((target, type(error).__name__, summary))

if selected is None:
    if scope is not None:
        self._sticky_targets.pop(scope, None)
    detail = "; ".join(
        f"{target.candidate_id} ({error_type}): {summary}"
        for target, error_type, summary in failures
    )
    raise FallbackError(f"All fallback upstreams failed: {detail}")

selected_index, buffered = selected
finish_reason = buffered.completion.finish_reason
if scope is not None:
    if finish_reason == "tool_calls":
        self._sticky_targets[scope] = selected_index
    else:
        self._sticky_targets.pop(scope, None)

completed = False
try:
    for event in buffered.events:
        yield event
    completed = True
finally:
    if scope is not None and (not completed or finish_reason != "tool_calls"):
        self._sticky_targets.pop(scope, None)
```

Log one INFO status line per attempted candidate using only candidate ID, description and success/error status. The shared client and Server retain per-chunk DEBUG logging. Yield the stored event dicts unchanged.

`discard(session_id)` removes all `(session_id, path)` keys; `clear()` empties the map.

- [ ] **Step 8: Run Fallback tests and verify GREEN**

Run: `uv run pytest -q tests/psi_agent/router/fallback`

Expected: PASS; `max_in_flight == 1`, exact order/no-wrap assertions hold, and failed events never escape.

- [ ] **Step 9: Commit the Fallback strategy**

```bash
git add src/psi_agent/router/fallback tests/psi_agent/router/fallback
git diff --cached --check
git commit -m "feat(router): add serial fallback strategy"
```

---

### Task 5: Fallback entries, unified facade, CLI, and YAML

**Files:**

- Create: `src/psi_agent/router/fallback/entry.py`
- Modify: `src/psi_agent/router/fallback/__init__.py`
- Modify: `src/psi_agent/router/entry.py:1-82`
- Modify: `src/psi_agent/router/__init__.py`
- Modify: `src/psi_agent/_run.py:24-48,96-106`
- Create: `tests/psi_agent/router/fallback/test_entry.py`
- Modify: `tests/psi_agent/router/test_entry.py`
- Modify: `tests/psi_agent/test_cli.py`
- Modify: `tests/psi_agent/test_run.py`

**Interfaces:**

- Produces `FallbackRouter(session_socket, targets, target_timeout=None, verbose=False)`.
- Extends `Router.mode` with `fallback` and `router_socket: str | None`.
- Extends `Router.upstream` with two-/three-tuple `RouterUpstream`.
- Adds `Router.upstream_types: list[RouterBackendType] = field(default_factory=list)` for tyro CLI.
- YAML accepts `[socket, description]` and `[socket, description, ai|router]`.

- [ ] **Step 1: Write failing direct-entry and facade tests**

Assert `FallbackRouter.run()` calls logging first, builds `FallbackConfig`, creates exactly one `RouterHttpClient`, and passes `FallbackStrategy` to `serve_router()`.

Extend `test_entry.py` parameterization to all three modes. Add exact facade cases:

```python
await Router(
    session_socket="fallback.sock",
    router_socket=None,
    mode="fallback",
    upstream=[("one.sock", "one"), ("nested.sock", "nested", "router")],
    target_timeout=5,
).run()
assert isinstance(captured[0], FallbackStrategy)
assert [target.backend_type for target in captured[0].config.targets] == ["ai", "router"]
```

Reject non-None fallback `router_socket`, missing routing/aggregation `router_socket`, unknown triple types, mismatched `upstream_types`, triple-plus-`upstream_types`, and invalid tuple lengths. Update the dataclass field-order assertion to include `upstream_types` after `upstream`.

- [ ] **Step 2: Write failing CLI and YAML tests**

CLI syntax is fixed by the current tyro parser:

```python
command = tyro.cli(
    Command,
    args=[
        "router",
        "--session-socket", "fallback.sock",
        "--router-socket", "None",
        "--mode", "fallback",
        "--upstream", "one.sock", "primary", "nested.sock", "nested",
        "--upstream-types", "ai", "router",
        "--target-timeout", "8",
    ],
)
assert command.router_socket is None
assert command.upstream == [("one.sock", "primary"), ("nested.sock", "nested")]
assert command.upstream_types == ["ai", "router"]
```

Add YAML with one fallback and one parent Router using a three-item nested entry. Assert `_run_config()` passes tuples of length 2/3 unchanged to `Router`.

- [ ] **Step 3: Run entry/configuration tests and verify RED**

Run:

```text
uv run pytest -q tests/psi_agent/router/fallback/test_entry.py tests/psi_agent/router/test_entry.py tests/psi_agent/test_cli.py tests/psi_agent/test_run.py
```

Expected: FAIL because facade mode/export/normalization and configuration parsing are absent.

- [ ] **Step 4: Implement `FallbackRouter` and root exports**

Match the two existing direct entries. The first executable statement in `run()` is logging; then construct config/client/strategy and call `serve_router()`.

Export `FallbackConfig`, `FallbackError`, `FallbackRouter`, `FallbackStrategy`, `BufferedCompletion`, backend/upstream types, and existing symbols. Do not reintroduce deleted process APIs.

- [ ] **Step 5: Normalize unified upstream configuration before strategy construction**

In `Router.run()`:

1. parse `RouterMode` with the three-mode error;
2. add `RouterMode.FALLBACK = "fallback"` before parsing;
3. validate `upstream` is a non-empty list;
4. if `upstream_types` is non-empty, require equal length and only two-tuples;
5. otherwise accept only two- or three-tuples;
6. validate every value and backend type;
7. build stable `candidate-{index}` targets;
8. require control Socket for Routing/Aggregation and require `None` for Fallback;
9. instantiate the selected config/strategy with one shared client.

Keep `server.py` unaware of the mode. Do not validate indirect cycles.

- [ ] **Step 6: Update YAML normalization and docs example in `_run.py`**

Convert list entries of length 2 or 3 to tuples:

```python
item["upstream"] = [
    tuple(entry) if isinstance(entry, list) and len(entry) in {2, 3} else entry
    for entry in upstream
]
```

Add a Fallback/nested Router example while preserving existing batch behavior and `setup_logging(verbose=True)` ownership.

- [ ] **Step 7: Run entry/configuration tests and verify GREEN**

Run the Step 3 command.

Expected: PASS; existing two-tuple CLI remains AI-by-default, CLI composition uses `upstream_types`, and YAML supports triples.

- [ ] **Step 8: Commit public Fallback entry points**

```bash
git add src/psi_agent/router/fallback/entry.py src/psi_agent/router/fallback/__init__.py src/psi_agent/router/entry.py src/psi_agent/router/__init__.py src/psi_agent/_run.py tests/psi_agent/router/fallback/test_entry.py tests/psi_agent/router/test_entry.py tests/psi_agent/test_cli.py tests/psi_agent/test_run.py
git diff --cached --check
git commit -m "feat(router): expose fallback mode"
```

---

### Task 6: Cross-mode composition and real Session acceptance

**Files:**

- Create: `tests/integration/test_fallback_router_composition.py`
- Modify: `tests/integration/test_serial_multi_ai_router.py`

**Interfaces:**

- Verifies every Router mode can consume every Router mode through `backend_type="router"`.
- Verifies all six distinct three-mode orders.
- Verifies nested `routing.path`, tool sticky, error interpretation, and cancellation.
- Produces no new runtime API.

- [ ] **Step 1: Add reusable real-server factories**

In the new test module, reuse the existing `_start_app`, `_start_handler`, `_sse`, `_chunk`, and `_run_session` patterns. Add:

```python
type Mode = Literal["routing", "aggregation", "fallback"]

async def _start_mode(
    mode: Mode,
    *,
    targets: list[RouterTarget],
    runners: list[web.AppRunner],
    observed: list[dict[str, Any]],
) -> str:
    client = RouterHttpClient()
    if mode == "routing":
        config = RoutingConfig(
            session_socket="routing-listener",
            selector_socket="unused-by-static-selector",
            targets=targets,
            target_timeout=5,
        )
        selector = StaticSelector(SelectionResult(targets[0].candidate_id, targets[0]))
        strategy: RouterStrategy = RoutingStrategy(config=config, selector=selector, client=client)
    elif mode == "aggregation":
        aggregator_runner, aggregator_url = await _start_handler(
            recording_aggregator(observed)
        )
        runners.append(aggregator_runner)
        config = AggregationConfig(
            session_socket="aggregation-listener",
            aggregator_socket=aggregator_url,
            targets=targets,
            target_timeout=5,
        )
        strategy = AggregationStrategy(config=config, client=client)
    else:
        config = FallbackConfig(
            session_socket="fallback-listener",
            targets=targets,
            target_timeout=5,
        )
        strategy = FallbackStrategy(config=config, client=client)
    runner, url = await _start_app(create_router_app(strategy=strategy))
    runners.append(runner)
    return url
```

Define `StaticSelector.select()` to return its stored `SelectionResult`, and define `recording_aggregator(observed)` as an aiohttp handler factory that appends `await request.json()` then returns one content/stop SSE completion. Every returned URL is the created Router app's public Socket.

- [ ] **Step 2: Add the 3×3 adjacent-mode matrix**

```python
@pytest.mark.anyio
@pytest.mark.parametrize("outer_mode", ["routing", "aggregation", "fallback"])
@pytest.mark.parametrize("inner_mode", ["routing", "aggregation", "fallback"])
async def test_every_router_mode_accepts_every_router_mode(
    outer_mode: Mode,
    inner_mode: Mode,
) -> None:
    # AI leaf -> inner Router -> outer Router.
    # Both edges to a Router use backend_type="router".
    assert terminal_finish == "stop"
    assert exactly_one_public_completion
```

For same-mode cases, create separate instances/Sockets. Assert the AI leaf receives no `routing`, while the inner Router request observes a non-empty path.

- [ ] **Step 3: Add all six three-mode permutations**

Parameterize `itertools.permutations(("routing", "aggregation", "fallback"))`. Build leaf-to-root and point a real `SessionAgent` at the root. Assert one final usable response and one committed assistant message for every permutation.

Add one branching graph where Aggregation calls a Routing Router, a Fallback Router, and a plain AI concurrently; verify ordered feedback and final aggregation.

- [ ] **Step 4: Add nested tool and failure semantics**

Create a nested Fallback whose first AI fails and second AI returns tool calls. Assert:

- Router-to-Router bodies carry the same `session_id` and stable appended path on both rounds;
- the ordinary AI bodies never contain `routing`;
- the tool round starts at the second Fallback candidate;
- if that candidate fails, only the third is attempted;
- outer Aggregation treats an all-failed Fallback as one failed branch;
- outer Fallback treats an inner Router error as a failed attempt;
- outer Routing forwards the selected child's error without inventing fallback.

- [ ] **Step 5: Add cancellation propagation**

Block the deepest AI with `anyio.Event`, start consuming the root, cancel the consumer, and assert every server-side generator `finally` marker is set. Do not use fixed sleeps; cancel each test task group before `__aexit__`.

- [ ] **Step 6: Run composition and existing Session tests**

Run:

```text
uv run pytest -q tests/integration/test_fallback_router_composition.py tests/integration/test_serial_multi_ai_router.py
```

Expected: PASS. If a failure reveals a runtime gap, return to the owning Task 1–5 test first; do not patch only the integration assertion.

- [ ] **Step 7: Commit composition acceptance coverage**

```bash
git add tests/integration/test_fallback_router_composition.py tests/integration/test_serial_multi_ai_router.py
git diff --cached --check
git commit -m "test(router): cover arbitrary router composition"
```

---

### Task 7: Gateway RouterManager backend graph and Fallback lifecycle

**Files:**

- Modify: `src/psi_agent/gateway/_router_manager.py:16-192`
- Modify: `tests/psi_agent/gateway/test_router_manager.py`

**Interfaces:**

- Changes `RouterUpstreamInfo` to `(backend_type, backend_id, description)`.
- Changes `RouterInfo.router_ai_id` to `str | None`.
- Produces `RouterDependencyError` for deleting a referenced Router.
- `_run_router_service()` receives optional control Socket and three-tuple upstreams.

- [ ] **Step 1: Rewrite Manager fakes and add failing typed-backend tests**

Use keyword construction everywhere:

```python
RouterUpstreamInfo(backend_type="ai", backend_id="simple", description="simple")
RouterUpstreamInfo(backend_type="router", backend_id="fallback-1", description="resilient")
```

Add tests that a parent resolves an AI through `AIManager` and an existing Router through `RouterManager.get_socket()`, then passes:

```python
upstreams=(
    ("http://simple", "simple", "ai"),
    ("fallback.sock", "resilient", "router"),
)
```

to `_run_router_service()`.

- [ ] **Step 2: Add failing mode/controller/dependency tests**

Cover:

- Routing/Aggregation reject `router_ai_id=None`;
- Fallback accepts only `router_ai_id=None` and passes `router_socket=None`;
- Fallback may contain AI and existing Router upstreams;
- missing AI/Router IDs fail before task spawn;
- duplicate `(backend_type, backend_id)` fails;
- unknown backend type fails;
- a Router that is referenced by another active Router cannot be deleted;
- after deleting the dependent parent, the child can be deleted.

- [ ] **Step 3: Run Manager tests and verify RED**

Run: `uv run pytest -q tests/psi_agent/gateway/test_router_manager.py`

Expected: FAIL because Manager only accepts AI IDs and a required control AI.

- [ ] **Step 4: Implement typed Manager models and resolver**

Define:

```python
class RouterDependencyError(RuntimeError):
    """A Router cannot be deleted while another Router references it."""


@dataclass(frozen=True)
class RouterUpstreamInfo:
    backend_type: Literal["ai", "router"]
    backend_id: str
    description: str
```

Normalize all strings before locking. Validate the mode/controller condition and timeouts/context exactly once. Resolve each upstream by type before spawning:

```python
if item.backend_type == "ai":
    socket = self._aim.get_socket(item.backend_id)
else:
    socket = self.get_socket(item.backend_id)
resolved.append((socket, item.description, item.backend_type))
```

Only Routing/Aggregation resolve `router_ai_id` through `AIManager`; Fallback passes `None`.

- [ ] **Step 5: Protect dependency deletion**

Under `_lock`, collect dependents whose upstream has `backend_type="router"` and matching ID. Raise `RouterDependencyError` with sorted dependent IDs before removing/cancelling anything. Preserve shielded cleanup on startup failure.

- [ ] **Step 6: Run Manager tests and verify GREEN**

Run: `uv run pytest -q tests/psi_agent/gateway/test_router_manager.py`

Expected: PASS; no invalid configuration spawns a task, and arbitrary DAG nodes can be created leaf-to-root.

- [ ] **Step 7: Commit Gateway lifecycle support**

```bash
git add src/psi_agent/gateway/_router_manager.py tests/psi_agent/gateway/test_router_manager.py
git diff --cached --check
git commit -m "feat(gateway): manage composable fallback routers"
```

---

### Task 8: Gateway state, restore, REST/OpenAPI, and title/summary routing

**Files:**

- Modify: `src/psi_agent/gateway/_state.py:49-163`
- Modify: `src/psi_agent/gateway/__init__.py:163-180,226-266`
- Modify: `src/psi_agent/gateway/server.py:296-339,520-533`
- Modify: `src/psi_agent/gateway/_openapi.py`
- Modify: `tests/psi_agent/gateway/test_state.py`
- Modify: `tests/psi_agent/gateway/test_server.py`
- Modify: `tests/psi_agent/gateway/test_openapi.py`
- Modify: `tests/integration/test_gateway.py:89-204`

**Interfaces:**

- Canonical state/REST upstream is `{backend_type, backend_id, description}`.
- Legacy state `{ai_id, description}` migrates in memory only.
- `router_ai_id` is nullable only for Fallback.
- Deleting a referenced Router returns HTTP 409.
- Fallback-backed title/summary generation uses the Fallback public Socket.

- [ ] **Step 1: Write failing state migration and whitelist tests**

Update the legacy assertion to:

```python
assert snapshot["routers"][0]["upstreams"] == [
    {"backend_type": "ai", "backend_id": "one", "description": "one"}
]
assert await state._path.read_text(encoding="utf-8") == raw
```

Add a fallback roundtrip with `router_ai_id is None` and mixed AI/Router upstreams. Save input containing legacy/private extras and assert persisted output includes only canonical keys.

- [ ] **Step 2: Write failing restore/persist and REST tests**

Update Gateway restore and `_do_persist()` expectations to use `RouterUpstreamInfo`'s three fields. In REST integration:

1. create two AIs;
2. create a Fallback leaf with `router_ai_id: null` and an AI upstream;
3. create an Aggregation or Routing parent with a Router upstream pointing at that leaf;
4. GET and assert canonical upstream JSON;
5. DELETE leaf and assert 409;
6. delete parent, then leaf successfully.

Every test `finally` cancels its task group before `__aexit__`.

- [ ] **Step 3: Write failing title/summary and OpenAPI tests**

Extend `FakeRouter` with `mode`, `router_ai_id`, and public `socket` access through Manager. Assert:

```python
assert await _session_ai_socket(routing_request, "session-1") == "selector.sock"
assert await _session_ai_socket(fallback_request, "session-1") == "fallback-public.sock"
```

OpenAPI assertions:

```python
assert schemas["RouterCreateRequest"]["properties"]["mode"]["enum"] == [
    "routing", "aggregation", "fallback"
]
upstream = schemas["RouterUpstreamInfo"]
assert upstream["required"] == ["backend_type", "backend_id", "description"]
assert upstream["properties"]["backend_type"]["enum"] == ["ai", "router"]
assert schemas["RouterCreateRequest"]["properties"]["router_ai_id"]["nullable"] is True
assert "409" in OPENAPI_SPEC["paths"]["/routers/{router_id}"]["delete"]["responses"]
```

Add a `oneOf` assertion for fallback-null versus routing/aggregation-nonempty controller rules.

- [ ] **Step 4: Run Gateway contract tests and verify RED**

Run:

```text
uv run pytest -q tests/psi_agent/gateway/test_state.py tests/psi_agent/gateway/test_server.py tests/psi_agent/gateway/test_openapi.py tests/integration/test_gateway.py
```

Expected: FAIL on old `ai_id`, required string controller, missing 409, and title Socket behavior.

- [ ] **Step 5: Normalize state and synchronize Gateway restore/persist**

On load, for every dict upstream:

```python
if "backend_type" in item or "backend_id" in item:
    backend_type = item.get("backend_type", "")
    backend_id = item.get("backend_id", "")
else:
    backend_type = "ai"
    backend_id = item.get("ai_id", "")
```

Do not rewrite during load. On save, whitelist only the three canonical upstream fields and preserve JSON null for fallback controller. Update restore and persistence comprehensions to use keyword `RouterUpstreamInfo` construction.

- [ ] **Step 6: Implement REST status and title/summary resolution**

Parse create requests with `.get("router_ai_id")` and canonical upstream keys. Catch `RouterDependencyError` before generic exceptions and return 409.

Resolve titles/summaries:

```python
info = rm.get(sess.backend_id)
if info.mode == "fallback":
    return rm.get_socket(sess.backend_id)
if info.router_ai_id is None:
    raise LookupError("Router control AI is not configured")
return aim.get_socket(info.router_ai_id)
```

- [ ] **Step 7: Update OpenAPI schemas**

Use OpenAPI 3.0-compatible schema objects: common `router_ai_id` is `type: string, nullable: true`; `oneOf` branches constrain mode with one-item enums, fallback controller with `enum: [null]`, and Routing/Aggregation controller with `minLength: 1`. Define canonical upstream and 409 response.

- [ ] **Step 8: Run Gateway tests and verify GREEN**

Run the Step 4 command plus:

```text
uv run pytest -q tests/psi_agent/gateway/test_router_manager.py
```

Expected: PASS; state load stays non-mutating and normal API construction remains leaf-to-root.

- [ ] **Step 9: Commit Gateway API and persistence**

```bash
git add src/psi_agent/gateway/_state.py src/psi_agent/gateway/__init__.py src/psi_agent/gateway/server.py src/psi_agent/gateway/_openapi.py tests/psi_agent/gateway/test_state.py tests/psi_agent/gateway/test_server.py tests/psi_agent/gateway/test_openapi.py tests/integration/test_gateway.py
git diff --cached --check
git commit -m "feat(gateway): expose composable fallback routers"
```

---

### Task 9: Gateway SPA Fallback and typed upstream form

**Files:**

- Modify: `src/psi_agent/gateway/spa/src/routerConfig.js`
- Modify: `src/psi_agent/gateway/spa/src/routerConfig.test.js`
- Modify: `src/psi_agent/gateway/spa/src/stores/router.js`
- Modify: `src/psi_agent/gateway/spa/src/stores/router.test.js`
- Modify: `src/psi_agent/gateway/spa/src/backendOptions.js`
- Modify: `src/psi_agent/gateway/spa/src/backendOptions.test.js`
- Modify: `src/psi_agent/gateway/spa/src/components/RouterDialog.vue`
- Modify: `src/psi_agent/gateway/spa/src/components/HubModelsPanel.vue`

**Interfaces:**

- Form upstream becomes `{backend_type, backend_id, description}`.
- `validateRouterForm(form, ais, routers)` validates both resource types and mode/controller conditions.
- `buildRouterPayload(form)` emits only canonical Gateway fields.
- Fallback submits `router_ai_id: null` and `router_timeout: null`.

- [ ] **Step 1: Write failing pure form/store tests**

Add a fallback fixture and assert:

```javascript
expect(buildRouterPayload(fallbackForm())).toEqual({
  name: 'Resilient',
  mode: 'fallback',
  router_ai_id: null,
  upstreams: [
    { backend_type: 'ai', backend_id: 'simple', description: 'primary' },
    { backend_type: 'router', backend_id: 'nested', description: 'secondary' },
  ],
  router_timeout: null,
  target_timeout: 8,
  max_context_chars: 12000,
})
```

Test unknown types/IDs, duplicate type+ID, same ID across different types, missing controller for Routing/Aggregation, forbidden controller for Fallback, Aggregator reuse only through an AI upstream, and `routerAiRole("fallback") === ""`.

Update the store reset assertion with typed empty upstream shape created by the dialog, while retaining exactly the seven top-level form fields.

- [ ] **Step 2: Run focused SPA tests and verify RED**

Working directory: `src/psi_agent/gateway/spa`

Run:

```text
npm test -- --run src/routerConfig.test.js src/stores/router.test.js src/backendOptions.test.js
```

Expected: FAIL because the UI contract still uses `ai_id` and only two modes.

- [ ] **Step 3: Implement pure configuration functions**

Use existing `backendExists()`/`getBackendLabel()` and add a helper returning the correct collection. `validateRouterForm()` checks mode first, then mode-specific controller, then each typed backend/description and duplicate composite key:

```javascript
const keys = form.upstreams.map(item => `${item.backend_type}:${item.backend_id}`)
if (new Set(keys).size !== keys.length) return '候选服务不能重复'
```

`buildRouterPayload()` always maps canonical fields. For fallback, force controller and router timeout to null rather than trusting hidden stale form values.

- [ ] **Step 4: Update RouterDialog**

- Add the Fallback option.
- Show Selector/Aggregator AI only when mode is not fallback.
- For each upstream, add type select (`ai`/`router`) and a backend select sourced from `ais` or existing `routers`.
- `addUpstream()` creates `{backend_type: "ai", backend_id: "", description: ""}`.
- On type change, clear `backend_id`.
- Hide Router timeout and context wording that only concerns Selector/Aggregator when fallback is selected; keep target timeout.
- Permit the dialog when at least one AI or Router backend exists; validation still requires an AI control model for Routing/Aggregation.

Do not touch the user-modified model preset/provider files.

- [ ] **Step 5: Update Router list display**

Fallback rows display `Fallback · N 个候选`; Routing/Aggregation retain their controller label. Typed upstream names use backend helpers and never show Socket/API key.

- [ ] **Step 6: Run SPA tests and build**

Working directory: `src/psi_agent/gateway/spa`

Run:

```text
npm test -- --run
npm run build
```

Expected: PASS. Inspect `git status --short` and confirm the three pre-existing model preset/provider modifications were not staged by this task.

- [ ] **Step 7: Commit only Router SPA files**

```bash
git add src/psi_agent/gateway/spa/src/routerConfig.js src/psi_agent/gateway/spa/src/routerConfig.test.js src/psi_agent/gateway/spa/src/stores/router.js src/psi_agent/gateway/spa/src/stores/router.test.js src/psi_agent/gateway/spa/src/backendOptions.js src/psi_agent/gateway/spa/src/backendOptions.test.js src/psi_agent/gateway/spa/src/components/RouterDialog.vue src/psi_agent/gateway/spa/src/components/HubModelsPanel.vue
git diff --cached --check
git commit -m "feat(gateway-spa): configure fallback router graphs"
```

---

### Task 10: Router, Gateway, and repository documentation

**Files:**

- Modify: `src/psi_agent/router/README.md`
- Modify: `src/psi_agent/router/AGENTS.md`
- Modify: `AGENTS.md`
- Modify: `src/psi_agent/gateway/AGENTS.md`
- Modify: `src/psi_agent/gateway/spa/AGENTS.md`

**Interfaces:**

- Documents the implemented contracts; changes no runtime behavior.
- Replaces fallback's current “intentionally unsupported” status.

- [ ] **Step 1: Update Router developer invariants**

Add `fallback/` to the module tree and record: complete buffering, usable-result rule, strict serial order, sticky/no-wrap, scope key/path propagation, typed edge stripping, error redaction, and arbitrary acyclic composition. Keep existing AnyIO, single-choice, `aclosing()`, Socket platform and logging rules.

- [ ] **Step 2: Rewrite Router README topology/configuration sections**

Document all three modes without implying a fixed order. Include:

- Python `FallbackRouter` and unified `Router` examples;
- CLI `--router-socket None` plus aligned `--upstream-types`;
- YAML two-/three-tuple examples;
- `Aggregation -> Routing -> Fallback` as one example only;
- success/failure/tool/timeout matrices;
- Gateway leaf-to-root construction and no runtime cycle detection.

- [ ] **Step 3: Synchronize root/Gateway/SPA instructions**

Update the root tree and Router description. In Gateway docs record canonical backend references, nullable Fallback controller, dependency deletion protection, state migration, restore order and title/summary behavior. In SPA docs record mode-specific fields and typed backend selection.

- [ ] **Step 4: Scan for stale contracts**

Run:

```text
rg -n "fallback.*not supported|不支持.*fallback|upstreams.*ai_id|item\.ai_id" AGENTS.md src/psi_agent/router src/psi_agent/gateway/AGENTS.md src/psi_agent/gateway/spa/AGENTS.md
```

Expected: no stale production contract; `ai_id` may appear only in explicitly labeled legacy state migration text.

- [ ] **Step 5: Commit documentation only**

```bash
git add AGENTS.md src/psi_agent/router/README.md src/psi_agent/router/AGENTS.md src/psi_agent/gateway/AGENTS.md src/psi_agent/gateway/spa/AGENTS.md
git diff --cached --check
git commit -m "docs(router): document serial fallback composition"
```

---

### Task 11: Cross-layer verification and privacy audit

**Files:**

- Verify: all files changed by Tasks 1–10.
- Planned modifications: none; failures return to the owning earlier task and add a focused regression test there.

**Interfaces:**

- Verifies the complete design specification.
- Produces no compatibility shim or empty verification commit.

- [ ] **Step 1: Run focused Router suites**

Run:

```text
uv run pytest -q tests/psi_agent/router tests/psi_agent/test_cli.py tests/psi_agent/test_run.py
uv run pytest -q tests/integration/test_serial_multi_ai_router.py tests/integration/test_fallback_router_composition.py
```

Expected: PASS.

- [ ] **Step 2: Run focused Gateway suites**

Run:

```text
uv run pytest -q tests/psi_agent/gateway/test_router_manager.py tests/psi_agent/gateway/test_state.py tests/psi_agent/gateway/test_server.py tests/psi_agent/gateway/test_openapi.py tests/integration/test_gateway.py
```

Expected: PASS.

- [ ] **Step 3: Run the full Python suite**

Run: `uv run pytest -q`

Expected: PASS with no collection conflicts; every new test directory contains `__init__.py`.

- [ ] **Step 4: Run static quality gates**

Run:

```text
uv run ruff check .
uv run ruff format --check .
uv run ty check .
```

Expected: all commands exit 0 with no suppression comments or per-file exceptions.

- [ ] **Step 5: Run complete SPA verification**

Working directory: `src/psi_agent/gateway/spa`

Run:

```text
npm test -- --run
npm run build
```

Expected: PASS.

- [ ] **Step 6: Audit privacy, stale schema, and forbidden APIs**

Run:

```text
rg -n "routing" tests/psi_agent/router tests/integration/test_fallback_router_composition.py
rg -n "ai_id|max_context_length|default_ai_id" src/psi_agent/gateway tests/psi_agent/gateway src/psi_agent/gateway/spa/src
rg -n "RouterClient|UpstreamResult|stream_raw|Orchestrator|Planner|PlannedTask" src/psi_agent/router tests/psi_agent/router
```

Expected:

- routing hits prove Router edges retain scope and AI/control-AI assertions remove it;
- old upstream `ai_id` appears only in state migration code/tests;
- removed process APIs do not return; Planner occurs only in explicit non-goal documentation.

- [ ] **Step 7: Inspect final diff without touching unrelated work**

Run:

```text
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; the pre-existing `modelPresets.js`, `modelPresets.test.js`, and `providers.js` changes remain uncommitted and untouched unless the user separately owns/requests them.

- [ ] **Step 8: Finish without a verification-only commit**

If any command fails, invoke `superpowers:systematic-debugging`, return to the earlier task that owns the failing boundary, add the concrete regression test there, and restart Task 11. If all commands pass, leave the branch ready for the user's chosen integration workflow; Task 11 creates no commit.

---

## Self-Review Checklist

- [ ] Spec coverage: every requirement in the design spec maps to an owning task.
- [ ] Placeholder scan: no TBD/TODO, unnamed handler, vague “handle errors,” or deferred test exists.
- [ ] Type consistency: backend/upstream/scope/result names and signatures match across Router, Gateway, REST/state and SPA.
- [ ] Composition coverage: AI stripping, Router propagation, 3×3 mode matrix, six permutations, branching, tools, failures and cancellation are all tested.
- [ ] Compatibility coverage: two-tuple AI default, `complete()`, existing routing/aggregation, CLI/YAML, state migration and deleted APIs are protected.
- [ ] Worktree safety: unrelated user-modified SPA provider/preset files are never staged by the listed commands.
