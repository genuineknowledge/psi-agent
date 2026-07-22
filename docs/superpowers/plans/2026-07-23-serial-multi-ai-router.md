# Serial Multi-AI Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a socket-only Router that plans exactly three subtasks, runs their tool-capable AI loops serially through Session, aggregates their final answers with the planning backend, and proxies failures to a default backend.

**Architecture:** Session remains the sole tool executor and adds a stable internal session ID to AI requests. Router owns one temporary three-branch state machine per Session, uses a reusable socket-aware SSE client for every upstream role, and exposes the existing single-choice OpenAI Chat Completions endpoint. Planning and aggregation share `router_socket`; branch selection and fallback use only configured sockets.

**Tech Stack:** Python 3.14, anyio, aiohttp, tyro, loguru, pytest/pytest-asyncio, ruff, ty.

---

## File Map

- Create `src/psi_agent/router/__init__.py`: export `Router` and `serve_router`.
- Create `src/psi_agent/router/protocol.py`: typed plan, branch, tool mapping, run, and upstream result models.
- Create `src/psi_agent/router/prompts.py`: pure planning, repair, branch, and aggregation prompt builders.
- Create `src/psi_agent/router/client.py`: socket-aware OpenAI SSE client and default raw-stream proxy.
- Create `src/psi_agent/router/planner.py`: strict three-task JSON parsing and one repair request.
- Create `src/psi_agent/router/orchestrator.py`: run store, serial branch transitions, tool continuation, aggregation, TTL, and fallback signaling.
- Create `src/psi_agent/router/server.py`: request validation, orchestration/fallback response streaming, aiohttp lifecycle, and Router CLI dataclass.
- Modify `src/psi_agent/session/agent.py`: add stable internal `routing.session_id` after filtering channel parameters.
- Modify `src/psi_agent/ai/server.py`: explicitly remove `routing` before calling external providers.
- Modify `src/psi_agent/cli.py`: add the top-level Router union member.
- Create `tests/psi_agent/router/__init__.py` and mirrored Router unit tests.
- Create `tests/integration/test_serial_multi_ai_router.py`: socket-level planner, tools, serial branches, aggregation, and fallback scenarios.
- Modify `tests/psi_agent/session/test_agent.py`: assert Session sends protected routing metadata on every tool round.
- Modify `tests/psi_agent/ai/test_server.py`: assert ordinary AI strips routing metadata.
- Create `src/psi_agent/router/AGENTS.md`; modify root `AGENTS.md`, `src/psi_agent/ai/AGENTS.md`, `src/psi_agent/session/AGENTS.md`, `README.md`, and `README_en.md` to document the component and protocol.

## Task 1: Define Router Protocol Types and Pure Prompt Builders

**Files:**
- Create: `src/psi_agent/router/protocol.py`
- Create: `src/psi_agent/router/prompts.py`
- Create: `tests/psi_agent/router/__init__.py`
- Create: `tests/psi_agent/router/test_protocol.py`
- Create: `tests/psi_agent/router/test_prompts.py`

- [ ] **Step 1: Write failing protocol tests**

Test construction and invariants with real dataclasses:

```python
from psi_agent.router.protocol import BranchState, BranchStatus, PlannedTask, RoutingRun


def test_routing_run_has_exactly_three_branches() -> None:
    branch = BranchState(subtask="one", socket="a", messages=[])
    with pytest.raises(ValueError, match="exactly three"):
        RoutingRun.create(
            run_id="run",
            session_id="session",
            original_messages=[],
            tools=[],
            branches=[branch],
        )


def test_planned_tasks_may_repeat_socket() -> None:
    tasks = tuple(PlannedTask(subtask=str(i), socket="same") for i in range(3))
    run = RoutingRun.create(
        run_id="run",
        session_id="session",
        original_messages=[],
        tools=[],
        branches=[BranchState.from_task(task) for task in tasks],
    )
    assert [branch.socket for branch in run.branches] == ["same"] * 3
    assert run.branches[0].status is BranchStatus.READY
    assert run.branches[1].status is BranchStatus.PENDING
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/psi_agent/router/test_protocol.py -v`

Expected: collection fails because `psi_agent.router` does not exist.

- [ ] **Step 3: Implement the minimal typed protocol**

Define `BranchStatus(StrEnum)` with `PENDING`, `READY`, `WAITING_TOOLS`, `COMPLETED`; frozen `PlannedTask`; mutable `BranchState`; frozen `ToolCallOrigin`; `RoutingRun.create()` that validates exactly three branches and activates only index zero; and typed `UpstreamDelta`/`UpstreamResult` models used by the client. Use precise types such as `tuple[BranchState, BranchState, BranchState]` and `dict[str, Any]`, never raw tuple/dict.

- [ ] **Step 4: Write and verify failing prompt tests**

```python
def test_planning_prompt_merges_duplicate_socket_descriptions() -> None:
    prompt = build_planning_messages(
        messages=[{"role": "user", "content": "Investigate"}],
        upstream=[("sock-a", "research"), ("sock-a", "writing")],
    )
    content = prompt[-1]["content"]
    assert content.count("sock-a") == 1
    assert "research" in content
    assert "writing" in content


def test_branch_three_receives_only_prior_final_answers() -> None:
    messages = build_branch_messages(
        original_messages=[{"role": "user", "content": "main"}],
        subtask="third",
        prior_answers=[("first", "answer one"), ("second", "answer two")],
    )
    text = messages[-1]["content"]
    assert "answer one" in text and "answer two" in text
    assert "tool_call" not in text and "reasoning" not in text
```

Run: `uv run pytest tests/psi_agent/router/test_prompts.py -v`

Expected: imports fail because prompt builders are missing.

- [ ] **Step 5: Implement pure prompt builders and verify GREEN**

Implement `merge_upstream_descriptions()`, `build_planning_messages()`, `build_repair_messages()`, `build_branch_messages()`, and `build_aggregation_messages()`. Every builder returns a new `list[dict[str, Any]]`, treats JSON input as untrusted, and contains explicit output-format instructions. Run:

`uv run pytest tests/psi_agent/router/test_protocol.py tests/psi_agent/router/test_prompts.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```text
git add src/psi_agent/router/protocol.py src/psi_agent/router/prompts.py tests/psi_agent/router
git commit -m "feat(router): define serial routing protocol"
```

## Task 2: Build the Socket-Aware SSE Client

**Files:**
- Create: `src/psi_agent/router/client.py`
- Create: `tests/psi_agent/router/test_client.py`

- [ ] **Step 1: Write failing client tests with inline aiohttp servers**

Cover content accumulation, partial tool-call accumulation by index, zero-choice heartbeat skipping, multiple-choice error, non-200 error, malformed JSON tolerance, `[DONE]`, and explicit generator closure. The desired API is:

```python
client = RouterClient()
result = await client.complete(
    socket=server_url,
    body={"messages": [], "stream": True},
    timeout=None,
)
assert result.content == "hello"
assert result.finish_reason == "stop"
```

For tool calls, assert fragmented `function.arguments` are concatenated and calls are ordered by numeric index.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/psi_agent/router/test_client.py -v`

Expected: import failure for `RouterClient`.

- [ ] **Step 3: Implement `RouterClient.complete()`**

Use `resolve_connector_and_endpoint(socket)`, `aiohttp.ClientSession`, and `ClientTimeout(total=timeout)`. Parse SSE `data:` lines defensively and return one `UpstreamResult`. Raise a focused `RouterUpstreamError` for non-200, multiple choices, `finish_reason="error"`, incomplete tool calls, or a stream ending without a finish reason. Log every received chunk at DEBUG. Enclose any exposed async generator in `aclosing()` and shield cleanup in `finally`.

- [ ] **Step 4: Add raw fallback streaming test and implementation**

Desired API:

```python
async with aclosing(client.stream_raw(socket=server_url, body=body, timeout=None)) as stream:
    chunks = [chunk async for chunk in stream]
assert b"data:" in b"".join(chunks)
```

`stream_raw()` must validate HTTP 200 before yielding bytes, preserve exact SSE bytes, log each chunk, and close the response when the downstream stops early.

- [ ] **Step 5: Verify GREEN and commit**

Run: `uv run pytest tests/psi_agent/router/test_client.py -v`

Expected: all tests pass.

```text
git add src/psi_agent/router/client.py tests/psi_agent/router/test_client.py
git commit -m "feat(router): add socket-aware SSE client"
```

## Task 3: Implement Strict Planning With One Repair Attempt

**Files:**
- Create: `src/psi_agent/router/planner.py`
- Create: `tests/psi_agent/router/test_planner.py`

- [ ] **Step 1: Write failing parser tests**

Test plain JSON, fenced JSON, wrong root type, two/four tasks, empty subtask, non-string socket, unconfigured socket, and repeated configured sockets. Desired interface:

```python
tasks = parse_plan(
    '{"tasks":[{"subtask":"a","socket":"s"},{"subtask":"b","socket":"s"},{"subtask":"c","socket":"s"}]}',
    allowed_sockets={"s"},
)
assert len(tasks) == 3
```

- [ ] **Step 2: Run parser tests and verify RED**

Run: `uv run pytest tests/psi_agent/router/test_planner.py -v`

Expected: import failure for `parse_plan`.

- [ ] **Step 3: Implement strict parsing**

Strip one outer Markdown JSON fence only, call `json.loads`, guard every container and field with `isinstance`, and return `tuple[PlannedTask, PlannedTask, PlannedTask]`. Raise `PlanValidationError` with a bounded diagnostic.

- [ ] **Step 4: Write failing repair-flow test**

Inject a fake client whose first result is invalid and second result is valid. Assert `Planner.plan()` calls `router_socket` exactly twice, and assert two invalid results raise `PlanValidationError`.

- [ ] **Step 5: Implement `Planner.plan()` and verify GREEN**

`Planner` receives `RouterClient`, `router_socket`, upstream tuples, and timeout. It builds the first prompt, validates the result, makes exactly one repair request on validation failure, and never retries transport failures internally. Run:

`uv run pytest tests/psi_agent/router/test_planner.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```text
git add src/psi_agent/router/planner.py tests/psi_agent/router/test_planner.py
git commit -m "feat(router): add strict three-task planner"
```

## Task 4: Implement Serial Branch Execution Without Tools

**Files:**
- Create: `src/psi_agent/router/orchestrator.py`
- Create: `tests/psi_agent/router/test_orchestrator.py`

- [ ] **Step 1: Write a failing serial-order test**

Use a fake planner returning sockets `a`, `b`, `a` and a fake client recording calls. Call `orchestrator.advance()` once with a new request whose three branch results all stop. Assert exact order:

```python
assert client.sockets == ["a", "b", "a", "router"]
assert response.finish_reason == "stop"
assert response.content == "aggregated"
assert orchestrator.active_runs == {}
```

Also assert branch 2's prompt contains answer 1 but no private branch-1 messages, and branch 3 contains answers 1 and 2.

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/psi_agent/router/test_orchestrator.py -v`

Expected: import failure for `SerialRouterOrchestrator`.

- [ ] **Step 3: Implement the minimal no-tool state machine**

`advance(session_id, body)` creates a run through Planner, calls only the current branch, saves stop content, activates the next branch, and loops until aggregation or a tool boundary. Protect the run dictionary with `anyio.Lock`; log lock acquisition/release at DEBUG. Aggregation calls `router_socket` without tools and removes the run only after a successful stop result.

- [ ] **Step 4: Add passthrough and model-removal tests**

Assert branch bodies preserve `temperature`, `top_p`, and unknown fields, but replace `messages`, preserve full `tools`, force `stream=True`, and exclude `routing` and `model`. Assert planning and aggregation bodies exclude tools.

- [ ] **Step 5: Verify GREEN and commit**

Run: `uv run pytest tests/psi_agent/router/test_orchestrator.py -v`

Expected: all no-tool tests pass.

```text
git add src/psi_agent/router/orchestrator.py tests/psi_agent/router/test_orchestrator.py
git commit -m "feat(router): run three branches serially"
```

## Task 5: Add Multi-Round Tool Continuation

**Files:**
- Modify: `src/psi_agent/router/orchestrator.py`
- Modify: `tests/psi_agent/router/test_orchestrator.py`

- [ ] **Step 1: Write a failing tool-ID rewrite test**

Make branch 1 return two tool calls with upstream IDs. Assert Router returns globally unique IDs, records their origins, leaves branch 1 in `WAITING_TOOLS`, and does not call branch 2.

- [ ] **Step 2: Verify RED**

Run the single test by node ID. Expected: returned tool IDs are absent or unchanged.

- [ ] **Step 3: Implement tool-call rewriting**

Accumulate calls in `RouterClient`, then create IDs in the format `psi_r_<run-id>_b<branch>_t<ordinal>`. Save the upstream assistant tool-call message with original IDs in private history, return rewritten calls to Session, increment the current branch's tool round, and emit a short content description naming the current subtask.

- [ ] **Step 4: Write a failing continuation test**

Send a second `advance()` request containing Session tool messages with rewritten IDs. Assert Router restores original IDs in private history, calls branch 1 again, then starts branch 2 only after branch 1 stops.

- [ ] **Step 5: Implement continuation validation**

Accept only tool messages that match every pending ID for the current run. Reject missing, duplicate, stale, foreign-session, or non-current-branch results with `RouterStateError`. Clear mappings only after all expected results are installed. Never put previous branches' raw tool results into later branch prompts.

- [ ] **Step 6: Add multi-round and limit tests**

Verify branch 1 can call tool A, then tool B, then stop; branch 2 can call tool C independently; and `max_tool_rounds` triggers a typed orchestration failure before another upstream call.

- [ ] **Step 7: Verify GREEN and commit**

Run: `uv run pytest tests/psi_agent/router/test_orchestrator.py -v`

Expected: all serial and tool-continuation tests pass.

```text
git add src/psi_agent/router/orchestrator.py tests/psi_agent/router/test_orchestrator.py
git commit -m "feat(router): support serial branch tool rounds"
```

## Task 6: Add Fallback, TTL, and Cancellation Cleanup

**Files:**
- Modify: `src/psi_agent/router/orchestrator.py`
- Modify: `src/psi_agent/router/client.py`
- Modify: `tests/psi_agent/router/test_orchestrator.py`
- Modify: `tests/psi_agent/router/test_client.py`

- [ ] **Step 1: Write failing fallback-decision tests**

Parameterize planning, branch, state-recovery, tool-limit, and aggregation errors. Assert each produces one `FallbackRequest(socket=default_socket, body=sanitized_current_body)`, deletes the active run, removes `routing` and `model`, retains `messages`, `tools`, and passthrough parameters, and does not call Planner again.

- [ ] **Step 2: Verify RED**

Run the parameterized tests. Expected: errors escape instead of becoming one fallback decision.

- [ ] **Step 3: Implement one-way fallback signaling**

Catch only expected orchestration exceptions around the stage boundary, never `BaseException`. Remove run state inside shielded cleanup and return a typed fallback object for the server to proxy. Default-backend errors must remain default errors and must not re-enter orchestration.

- [ ] **Step 4: Write TTL and cancellation tests**

Use a controllable monotonic clock. Assert expired waiting runs are removed on access and fall back. Cancel an in-flight client call and assert response/generator cleanup occurs and no run remains. Avoid real sleeps.

- [ ] **Step 5: Implement expiry and cleanup**

Inject `clock: Callable[[], float]` with `time.monotonic` default. Prune expired runs under the run lock at request entry. Use `finally` and shielded async cleanup for cancellation. Do not add an immortal cleanup task.

- [ ] **Step 6: Verify GREEN and commit**

Run:

`uv run pytest tests/psi_agent/router/test_client.py tests/psi_agent/router/test_orchestrator.py -v`

Expected: all tests pass without warnings.

```text
git add src/psi_agent/router/client.py src/psi_agent/router/orchestrator.py tests/psi_agent/router
git commit -m "feat(router): add fallback and run cleanup"
```

## Task 7: Expose the Router HTTP/SSE Service and CLI

**Files:**
- Create: `src/psi_agent/router/server.py`
- Create: `src/psi_agent/router/__init__.py`
- Modify: `src/psi_agent/cli.py`
- Create: `tests/psi_agent/router/test_server.py`
- Create: `tests/psi_agent/router/test_entry.py`

- [ ] **Step 1: Write failing parameter-validation tests**

Construct `Router` directly and call validation/run paths. Cover blank sockets, empty upstream, tuple empty fields, non-positive/NaN timeouts, non-positive tool rounds/TTL, duplicate sockets allowed, and `default_socket == session_socket` rejected.

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/psi_agent/router/test_entry.py -v`

Expected: Router import fails.

- [ ] **Step 3: Implement Router dataclass and lifecycle**

`Router.run()` calls `setup_logging(verbose=self.verbose)` as its first executable statement, validates configuration, builds client/planner/orchestrator, and awaits `serve_router()`. `serve_router()` uses `create_site()`, shields runner cleanup on startup failure and shutdown, and remains externally cancellable.

- [ ] **Step 4: Write failing handler tests**

Cover invalid JSON/non-dict bodies as HTTP 400, successful aggregation as SSE, tool-call SSE, fallback raw SSE preservation, fallback HTTP error before prepare, orchestration error after prepare as `finish_reason="error"`, and client disconnect cleanup.

- [ ] **Step 5: Implement handler and SSE encoder**

Do not prepare the downstream response until orchestration has either produced a valid internal result or a fallback upstream returned HTTP 200. Encode exactly one choice per chunk. Log every outgoing chunk at DEBUG. Use aclosing for raw fallback streams.

- [ ] **Step 6: Add Router to CLI and verify native tuple syntax**

Import `Router` in `src/psi_agent/cli.py` and add it to the tyro union. Add a subprocess-free unit test around `tyro.cli(..., args=[...])` showing the exact accepted `--upstream` token sequence and asserting the parsed value is `list[tuple[str, str]]`.

- [ ] **Step 7: Verify GREEN and commit**

Run:

```text
uv run pytest tests/psi_agent/router/test_entry.py tests/psi_agent/router/test_server.py -v
uv run psi-agent router --help
```

Expected: tests pass and help lists socket-only options.

```text
git add src/psi_agent/router src/psi_agent/cli.py tests/psi_agent/router
git commit -m "feat(router): expose router service and CLI"
```

## Task 8: Attach Stable Session Metadata and Strip It at Ordinary AI Backends

**Files:**
- Modify: `src/psi_agent/session/agent.py`
- Modify: `src/psi_agent/ai/server.py`
- Modify: `tests/psi_agent/session/test_agent.py`
- Modify: `tests/psi_agent/ai/test_server.py`

- [ ] **Step 1: Write a failing Session metadata test**

Capture two request bodies around a tool round and assert both contain:

```python
assert body["routing"] == {"session_id": agent._conversation.session_id}
```

Also pass hostile channel `extra_params={"routing": {"session_id": "other"}}` and assert it cannot override the real Session ID.

- [ ] **Step 2: Run and verify RED**

Run the new Session test by node ID. Expected: `routing` is absent or attacker-controlled.

- [ ] **Step 3: Implement protected metadata injection**

Continue removing channel-supplied `messages`, `tools`, and `stream`; additionally remove `routing`. Merge remaining parameters, then assign:

```python
request_body["routing"] = {"session_id": self._conversation.session_id}
```

Assigning after the merge makes internal identity authoritative.

- [ ] **Step 4: Write a failing ordinary-AI stripping test**

Patch `acompletion`, send a body containing routing metadata, and assert `acompletion` does not receive a `routing` keyword.

- [ ] **Step 5: Implement stripping and verify GREEN**

Add `body.pop("routing", None)` alongside the other protected fields in `src/psi_agent/ai/server.py`. Run:

`uv run pytest tests/psi_agent/session/test_agent.py tests/psi_agent/ai/test_server.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```text
git add src/psi_agent/session/agent.py src/psi_agent/ai/server.py tests/psi_agent/session/test_agent.py tests/psi_agent/ai/test_server.py
git commit -m "feat(session): attach internal routing identity"
```

## Task 9: Add End-to-End Socket Integration Tests

**Files:**
- Create: `tests/integration/test_serial_multi_ai_router.py`

- [ ] **Step 1: Write the failing serial tools integration test**

Start inline TCP aiohttp servers for Router AI, two branch sockets, and default. The Router AI returns a three-task plan on its first request and aggregate content on its second role. Branch 1 requests tool A then stops; branch 2 starts afterward, asserts answer 1 is present, requests tool B then stops; branch 3 asserts answers 1 and 2 are present and stops. Run a real `SessionAgent` with two real async ToolRegistry functions against `serve_router`.

Assert call-order markers are exactly:

```python
[
    "plan",
    "branch1-tool",
    "branch1-stop",
    "branch2-tool",
    "branch2-stop",
    "branch3-stop",
    "aggregate",
]
```

Assert final content is the aggregate, tool functions each ran once, every branch received the full two-tool schema, and the Router run store is empty.

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/integration/test_serial_multi_ai_router.py::test_serial_branches_use_distinct_tools_then_aggregate -v`

Expected: failure at the first missing or incorrect cross-component behavior.

- [ ] **Step 3: Make the minimum integration fixes**

Correct only defects exposed by the test while keeping all focused unit suites green. Do not alter the asserted architecture to accommodate implementation shortcuts.

- [ ] **Step 4: Add fallback integration cases**

Parameterize planner HTTP failure, branch SSE error, aggregation error, and lost-state continuation. Assert the default socket receives one sanitized current body and its raw SSE becomes the Session response. Add a case where branch 1 already called a tool before failing and assert default history contains the short subtask description plus the completed tool result.

- [ ] **Step 5: Verify GREEN and commit**

Run:

`uv run pytest tests/integration/test_serial_multi_ai_router.py -v`

Expected: all integration scenarios pass.

```text
git add tests/integration/test_serial_multi_ai_router.py src/psi_agent/router
git commit -m "test(router): cover serial orchestration end to end"
```

## Task 10: Synchronize Architecture and User Documentation

**Files:**
- Create: `src/psi_agent/router/AGENTS.md`
- Modify: `AGENTS.md`
- Modify: `src/psi_agent/ai/AGENTS.md`
- Modify: `src/psi_agent/session/AGENTS.md`
- Modify: `README.md`
- Modify: `README_en.md`

- [ ] **Step 1: Write documentation from verified behavior**

Document component placement, exact CLI syntax confirmed in Task 7, socket-only upstream tuples, the shared planning/aggregation socket, serial three-branch semantics, full tool visibility, final-answer-only knowledge transfer, internal session metadata, strict fallback, timeout/TTL defaults, logs, and cancellation cleanup.

- [ ] **Step 2: Update root architecture inventory**

Add `router/` to the source tree and explain the deliberate boundary: Router coordinates tools but never executes them or knows model configuration.

- [ ] **Step 3: Check documentation consistency**

Run:

`rg -n "planner_model|aggregator_model|target_id|model_name|parallel branch|concurrent branch" AGENTS.md README.md README_en.md src/psi_agent/*/AGENTS.md docs/superpowers/specs docs/superpowers/plans`

Expected: no active documentation claims Router routes by model identity or runs the three branches concurrently; historical design discussions are updated if they would mislead current implementers.

- [ ] **Step 4: Commit**

```text
git add AGENTS.md README.md README_en.md src/psi_agent/ai/AGENTS.md src/psi_agent/session/AGENTS.md src/psi_agent/router/AGENTS.md
git commit -m "docs(router): document serial socket routing"
```

## Task 11: Full Verification and Definition-of-Done Audit

**Files:**
- Modify only files required to correct failures found by verification.

- [ ] **Step 1: Run focused Router and Session tests**

```text
uv run pytest tests/psi_agent/router tests/psi_agent/session/test_agent.py tests/psi_agent/ai/test_server.py tests/integration/test_serial_multi_ai_router.py -v
```

Expected: zero failures and zero unexpected warnings.

- [ ] **Step 2: Run lint and formatting checks**

```text
uv run ruff check .
uv run ruff format --check .
```

Expected: both commands exit 0. Apply formatter only if the check reports changed files, then rerun both checks.

- [ ] **Step 3: Run type checking**

Run: `uv run ty check`

Expected: exit 0 with no new suppressions.

- [ ] **Step 4: Run the complete test suite**

Run: `uv run pytest -v`

Expected: zero failures. Schedule-marked tests are included because the project Definition of Done requires the full suite.

- [ ] **Step 5: Verify CLI and build**

```text
uv run psi-agent router --help
uv build
```

Expected: Router help renders with native tuple-list syntax and the package builds successfully with `psi_agent.router` included.

- [ ] **Step 6: Audit requirements and the worktree**

Compare the implementation line by line with `docs/superpowers/specs/2026-07-23-serial-multi-ai-router-design.md`. Confirm logs, cancellation, docs, tests, fallback, socket-only configuration, and serial tool behavior. Run `git status --short` and ensure the pre-existing `.gitignore` and SPA lockfile modifications remain uncommitted and untouched unless the user separately requests them.

- [ ] **Step 7: Commit verification fixes, if any**

Stage only Router-related files and commit with a message describing the concrete fix. If verification required no changes, do not create an empty commit.
