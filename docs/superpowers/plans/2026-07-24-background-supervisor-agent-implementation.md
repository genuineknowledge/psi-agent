# Background Supervisor Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a mandatory before-turn supervisor Session that sees only user questions, maintains shared domain maps and per-user heatmaps, and injects validated breakout advice into the current main-Agent prompt.

**Architecture:** Extend `SystemPrompt` with a recoverable `system_before_turn` hook, then keep all supervision policy in the Haitun Workspace. A `SupervisorManager` reuses the existing background/subagent helpers to maintain one logical supervisor Session per `user_id`; the dedicated supervisor Workspace returns strict JSON that is validated before prompt injection.

**Tech Stack:** Python 3.14, anyio, aiohttp/ChannelCore, YAML/JSON files, loguru, pytest, pytest-anyio, ruff, ty.

---

## File Structure

Core lifecycle:

- Modify `src/psi_agent/session/system_prompt.py`: load, time-bound, and execute `system_before_turn`.
- Modify `src/psi_agent/session/agent.py`: run supervision before prompt construction and keep advice ephemeral.
- Modify `src/psi_agent/session/AGENTS.md`: document the new lifecycle and isolation contract.
- Modify `tests/psi_agent/session/test_session.py`: hook loading, timeout, invalid output, and exception behavior.
- Modify `tests/psi_agent/session/test_agent.py`: ordering and non-persistence/non-forwarding tests.

Haitun supervision implementation:

- Create `examples/haitun-workspace/systems/supervisor_protocol.py`: typed advice defaults, validation, repair, prompt rendering.
- Create `examples/haitun-workspace/systems/supervisor_store.py`: shared-map and per-user-heatmap persistence.
- Create `examples/haitun-workspace/systems/supervisor.py`: learning classification, per-user Session management, isolated payloads, retries, caching.
- Modify `examples/haitun-workspace/systems/system.py`: expose `system_before_turn` and inject rendered advice.
- Modify `examples/haitun-workspace/tools/_subagent_helpers.py`: allow supervisor Workspace override when planning the child Session.
- Reuse `examples/haitun-workspace/tools/_background_process_registry.py:start_process` unchanged unless tests reveal a missing internal argument.

Dedicated supervisor Workspace:

- Create `examples/haitun-supervisor-workspace/AGENTS.md`.
- Create `examples/haitun-supervisor-workspace/SOUL.md`.
- Create `examples/haitun-supervisor-workspace/systems/system.py`.
- Do not add map/heatmap write tools to the first-version supervisor Workspace. The main Workspace manager performs persistence only after validating the child JSON, keeping the child read-only and reducing failure modes.
- Create `examples/haitun-supervisor-workspace/histories/.gitignore`.

Verification and documentation:

- Create `tests/integration/test_haitun_supervisor.py`.
- Create `tests/integration/test_haitun_supervisor_e2e.py`.
- Create `examples/haitun-workspace/demo_supervisor_breakout.py`.
- Modify `examples/haitun-workspace/AGENTS.md`.
- Modify `README.md` and `README_en.md` only if they currently document Workspace lifecycle hooks.

## Task 1: Add the Generic Before-Turn Hook

**Files:**
- Modify: `src/psi_agent/session/system_prompt.py`
- Test: `tests/psi_agent/session/test_session.py`

- [ ] **Step 1: Write failing hook-loading and result tests**

Add tests that write a temporary `systems/system.py` containing:

```python
async def system_before_turn(user_message):
    return {"breakout": {"needed": True}, "seen": user_message["content"]}
```

Load with `SystemPrompt.from_workspace(tmp_path, "test")`, call `run_before_turn({"content": "learn"})`, and assert the returned dictionary contains `seen == "learn"`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
uv run pytest -q tests/psi_agent/session/test_session.py -k before_turn
```

Expected: failure because `SystemPrompt` does not load or expose `system_before_turn`.

- [ ] **Step 3: Extend `SystemPrompt` constructor and loader**

Add a default hook and constructor fields:

```python
@staticmethod
async def _default_before_turn(_user_message: dict[str, Any]) -> dict[str, Any]:
    return {}

def __init__(
    self,
    builder: Callable[..., Any] | None = None,
    checker: Callable[..., Any] | None = None,
    before_turn: Callable[..., Any] | None = None,
    after_turn: Callable[..., Any] | None = None,
    before_turn_timeout_seconds: float = 25.0,
) -> None:
    self._builder = builder if builder is not None else self._default_builder
    self._checker = checker if checker is not None else self._default_checker
    self._before_turn = before_turn if before_turn is not None else self._default_before_turn
    self._after_turn = after_turn if after_turn is not None else self._default_after_turn
    self._before_turn_timeout_seconds = before_turn_timeout_seconds
```

Change `_load_module()` to return four callables in this order:

```python
builder, checker, before_turn, after_turn
```

Extract `system_before_turn` with `_extract_async_func()`.

- [ ] **Step 4: Implement recoverable execution and timeout**

Add:

```python
async def run_before_turn(self, user_message: dict[str, Any]) -> dict[str, Any]:
    try:
        with anyio.fail_after(self._before_turn_timeout_seconds):
            result = await self._before_turn(user_message)
    except TimeoutError:
        logger.warning(f"System before-turn hook timed out after {self._before_turn_timeout_seconds:.1f}s")
        return {}
    except Exception as e:
        logger.warning(f"System before-turn hook failed: {e!r}")
        return {}
    if not isinstance(result, dict):
        logger.warning(f"System before-turn hook returned {type(result).__name__}, expected dict")
        return {}
    logger.debug("System before-turn hook completed")
    return result
```

Do not catch `BaseException`.

- [ ] **Step 5: Add timeout, invalid-return, exception, and cancellation tests**

Use a 0.01-second timeout with a hook that calls `await anyio.sleep(1)`, assert `{}`. Use a hook returning `"bad"`, assert `{}`. Use a hook raising `RuntimeError`, assert `{}`. For cancellation, start `run_before_turn()` in a task group, cancel the group, and assert the task does not convert cancellation into `{}`.

- [ ] **Step 6: Run tests and lint**

```powershell
uv run pytest -q tests/psi_agent/session/test_session.py
uv run ruff check src/psi_agent/session/system_prompt.py tests/psi_agent/session/test_session.py
```

Expected: all tests pass; Ruff reports `All checks passed!`.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- src/psi_agent/session/system_prompt.py tests/psi_agent/session/test_session.py
git commit -m "feat(session): add before-turn system hook"
```

## Task 2: Run Advice Before Main Prompt Construction

**Files:**
- Modify: `src/psi_agent/session/agent.py`
- Test: `tests/psi_agent/session/test_agent.py`

- [ ] **Step 1: Write a failing ordering and isolation test**

Create a `SystemPrompt` with:

```python
events: list[str] = []

async def before_turn(message):
    events.append("before")
    return {"marker": "supervised"}

async def builder(message):
    events.append("builder")
    assert message["supervisor_advice"] == {"marker": "supervised"}
    return "supervised prompt"
```

Run one turn against the existing mock AI. Assert `events == ["before", "builder"]`, the AI's first system message contains `supervised prompt`, and neither persisted conversation messages nor forwarded request parameters contain `supervisor_advice`.

- [ ] **Step 2: Verify the test fails**

```powershell
uv run pytest -q tests/psi_agent/session/test_agent.py -k supervisor_advice
```

Expected: failure because `SessionAgent` never calls `run_before_turn`.

- [ ] **Step 3: Insert the hook before `ensure()`**

In `SessionAgent.run()` after registry refresh and before `ensure()`:

```python
supervisor_advice = await self._system_prompt.run_before_turn(hook_message)
if supervisor_advice:
    hook_message["supervisor_advice"] = supervisor_advice
await self._system_prompt.ensure(self._conversation, hook_message)
```

Continue to persist the original `user_message`, not `hook_message`. Continue to strip reserved identity parameters from `request_params`.

- [ ] **Step 4: Add a schedule-turn bypass test**

Define that `response_kind` beginning with `schedule.` skips the before-turn hook. Implement:

```python
if not turn_response_kind.startswith("schedule."):
    supervisor_advice = await self._system_prompt.run_before_turn(hook_message)
```

Assert a `schedule.silent` run does not append an event.

- [ ] **Step 5: Run focused Session tests**

```powershell
uv run pytest -q tests/psi_agent/session/test_agent.py tests/psi_agent/session/test_session.py
uv run ruff check src/psi_agent/session/agent.py tests/psi_agent/session/test_agent.py
```

Expected: all focused tests pass.

- [ ] **Step 6: Commit Task 2**

```powershell
git add -- src/psi_agent/session/agent.py tests/psi_agent/session/test_agent.py
git commit -m "feat(session): inject before-turn advice"
```

## Task 3: Define and Validate SupervisorAdvice

**Files:**
- Create: `examples/haitun-workspace/systems/supervisor_protocol.py`
- Create: `tests/integration/test_haitun_supervisor.py`

- [ ] **Step 1: Write failing protocol tests**

Load `supervisor_protocol.py` with the same compile/exec helper pattern used by existing Haitun tests. Cover:

```python
assert validate_advice(valid)["breakout"]["type"] == "broaden"
assert validate_advice({"breakout": {"score": 4}})["breakout"]["score"] == 1.0
assert len(validate_advice({"breakout": {"directions": [1, 2, 3, 4]}})["breakout"]["directions"]) == 3
assert validate_advice("not a dict")["diagnostics"]["source"] == "unavailable"
```

Also test `extract_json_object()` with fenced JSON and leading prose.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py -k protocol
```

Expected: import/file-not-found failure.

- [ ] **Step 3: Implement protocol defaults and validation**

Define constants:

```python
BREAKOUT_TYPES = {"none", "broaden", "deepen", "reframe", "cross_domain", "operationalize"}
ADVICE_SOURCES = {"live", "repaired", "stale", "unavailable"}
```

Provide:

```python
def empty_advice(*, source: str = "unavailable") -> dict[str, Any]: ...
def extract_json_object(text: str) -> dict[str, Any] | None: ...
def validate_advice(raw: object) -> dict[str, Any]: ...
def render_advice_prompt(advice: dict[str, Any]) -> str: ...
```

Validation rules must clamp floats, bound strings, limit evidence and directions, validate enum values, and conservatively disable breakout when required fields are contradictory.

- [ ] **Step 4: Implement concise prompt rendering**

`render_advice_prompt()` returns `""` when unavailable or non-learning. Otherwise render only domain/topic, breakout reason/directions, latent need, profile shift, and response strategy. Include these invariant instructions:

```text
先回答用户当前问题。
不要向用户提及副 Agent、监督评分或画像判断。
不要强迫用户转换话题。
```

- [ ] **Step 5: Run tests and lint**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py -k protocol
uv run ruff check examples/haitun-workspace/systems/supervisor_protocol.py tests/integration/test_haitun_supervisor.py
```

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- examples/haitun-workspace/systems/supervisor_protocol.py tests/integration/test_haitun_supervisor.py
git commit -m "feat(haitun): validate supervisor advice"
```

## Task 4: Persist Shared Maps and Per-User Heatmaps

**Files:**
- Create: `examples/haitun-workspace/systems/supervisor_store.py`
- Modify: `tests/integration/test_haitun_supervisor.py`

- [ ] **Step 1: Write failing persistence tests**

Test these behaviors with `tmp_path`:

- `save_map()` then `load_map()` round-trips a `machine-learning` map.
- two users load different heatmap files for the same domain.
- map paths are shared while heatmap paths include hashed user directories.
- an existing map is reused without changing `generated_at`.
- `update_heatmap()` increments node count, question count, and repeated-surface evidence.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py -k store
```

- [ ] **Step 3: Implement `SupervisorStore`**

Use `anyio.Path` for all I/O and define:

```python
class SupervisorStore:
    def __init__(self, workspace: anyio.Path) -> None: ...
    async def load_map(self, domain_id: str) -> dict[str, Any] | None: ...
    async def save_map(self, domain_id: str, data: dict[str, Any]) -> None: ...
    async def load_heatmap(self, user_hash: str, domain_id: str) -> dict[str, Any]: ...
    async def save_heatmap(self, user_hash: str, domain_id: str, data: dict[str, Any]) -> None: ...
    async def load_latest_advice(self, user_hash: str) -> dict[str, Any] | None: ...
    async def save_latest_advice(self, user_hash: str, data: dict[str, Any]) -> None: ...
```

Sanitize `domain_id` to lowercase ASCII letters, digits, and hyphens. Use SHA-256 hashes supplied by the manager for user directories.

- [ ] **Step 4: Implement atomic JSON-compatible YAML persistence**

Use `yaml.safe_load`/`yaml.safe_dump` if PyYAML is already present. Write a sibling temporary file, then call `await anyio.to_thread.run_sync(os.replace, str(temp), str(target))`. Clean up the temporary file in `finally` with a shielded cancel scope.

- [ ] **Step 5: Add in-process user/domain locks**

Maintain lock dictionaries guarded by `anyio.Lock`. Expose async context managers `user_lock(user_hash)` and `domain_lock(domain_id)` and add tests that two tasks cannot enter the same key concurrently.

- [ ] **Step 6: Run tests and lint**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py -k "store or lock"
uv run ruff check examples/haitun-workspace/systems/supervisor_store.py tests/integration/test_haitun_supervisor.py
```

- [ ] **Step 7: Commit Task 4**

```powershell
git add -- examples/haitun-workspace/systems/supervisor_store.py tests/integration/test_haitun_supervisor.py
git commit -m "feat(haitun): persist supervisor knowledge state"
```

## Task 5: Create the Dedicated Supervisor Workspace

**Files:**
- Create: `examples/haitun-supervisor-workspace/AGENTS.md`
- Create: `examples/haitun-supervisor-workspace/SOUL.md`
- Create: `examples/haitun-supervisor-workspace/systems/system.py`
- Create: `examples/haitun-supervisor-workspace/histories/.gitignore`
- Modify: `tests/integration/test_haitun_supervisor.py`

- [ ] **Step 1: Write a failing prompt-contract test**

Load the new Workspace system prompt and assert it contains:

```text
独立旁路监督 Agent
不得回答最终用户问题
不得请求主 Agent 的回答、reasoning、tool_calls 或 tool results
破圈优先级最高
SupervisorAdvice JSON
```

Assert it does not contain the Haitun main persona or instructions to call `profile_update`.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py -k supervisor_workspace
```

- [ ] **Step 3: Create the minimal supervisor Workspace**

`systems/system.py` should expose only:

```python
async def system_prompt_builder(_user_message=None) -> str:
    return """...strict supervisor prompt and JSON schema..."""

async def system_prompt_rebuild_checker(_user_message=None) -> bool:
    return False
```

Do not define `system_before_turn` or `system_after_turn`, preventing recursive supervision and stage-profile updates.

Do not expose write tools in this first version. The child proposes map and heatmap changes in JSON; `SupervisorManager` validates and persists them.

- [ ] **Step 4: Add explicit map-generation instructions**

The prompt must instruct the child to return a baseline `map_updates.proposed_map` only when the request says no shared map exists. When a map exists, it must return visited nodes and optional branch additions instead of regenerating the entire map.

- [ ] **Step 5: Run tests and lint**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py -k supervisor_workspace
uv run ruff check examples/haitun-supervisor-workspace/systems/system.py
```

- [ ] **Step 6: Commit Task 5**

```powershell
git add -- examples/haitun-supervisor-workspace tests/integration/test_haitun_supervisor.py
git commit -m "feat(haitun): add supervisor workspace"
```

## Task 6: Implement Per-User Supervisor Session Management

**Files:**
- Create: `examples/haitun-workspace/systems/supervisor.py`
- Modify: `examples/haitun-workspace/tools/_subagent_helpers.py`
- Modify: `tests/integration/test_haitun_supervisor.py`

- [ ] **Step 1: Write failing identity and payload-isolation tests**

Inject fake callables for plan/start/wait/chat. Assert:

- the same `user_id` produces the same `supervisor-<hash>` Session ID;
- different users produce different IDs;
- the same user reuses an existing healthy handle;
- the chat payload contains only the allowlisted fields;
- serialized payload does not contain `assistant`, `reasoning`, `tool_calls`, `tool result`, or a `messages` array.

- [ ] **Step 2: Write failing restart and timeout-facing tests**

Make the first socket probe fail and the second plan/start succeed; assert one restart. Make both fail; assert unavailable advice without raising.

- [ ] **Step 3: Implement learning classification and identity hashing**

Provide:

```python
def is_learning_question(text: str) -> bool: ...
def hash_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
```

Skip empty text, schedule messages, and supervisor Session IDs. Prefer stable `user_id`; fall back to `profile_id`, then `session_id` only for local compatibility.

- [ ] **Step 4: Allow a child Workspace override in subagent planning**

Extend `plan_subagent()` with:

```python
async def plan_subagent(
    *,
    session_id: str = "",
    workspace_raw: str = "",
    gateway_ai_id: str = "",
    child_workspace_raw: str = "",
) -> dict[str, Any]:
```

Resolve parent AI binding using the main Workspace, but build the child Session argv with `child_workspace_raw` when supplied. Existing public tool calls without the new argument must behave exactly as before.

- [ ] **Step 5: Implement `SupervisorManager` with injectable dependencies**

Constructor shape:

```python
class SupervisorManager:
    def __init__(
        self,
        workspace: anyio.Path,
        *,
        plan_fn: Callable[..., Awaitable[dict[str, Any]]],
        start_fn: Callable[..., Awaitable[dict[str, Any]]],
        wait_fn: Callable[..., Awaitable[dict[str, Any]]],
        chat_fn: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None: ...
```

Use `SupervisorStore`, one cached `SupervisorHandle` per user hash, a health probe before reuse, and one restart attempt.

- [ ] **Step 6: Build the payload from an allowlist**

Construct a new dictionary containing exactly event, hashed identities, turn index, current user question, normalized three-dimensional stage profile, existing map summary, heatmap summary, and prior supervision summary. Never copy `hook_message` wholesale.

- [ ] **Step 7: Apply validated child output to stores**

After `chat_fn`, call `extract_json_object()` and `validate_advice()`. Under the domain lock, create or expand the shared map from validated `map_updates`. Under the user lock, update the heatmap and save latest advice. Return the validated advice.

- [ ] **Step 8: Run focused tests**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py -k "manager or identity or isolation or restart"
uv run ruff check examples/haitun-workspace/systems/supervisor.py examples/haitun-workspace/tools/_subagent_helpers.py tests/integration/test_haitun_supervisor.py
```

- [ ] **Step 9: Commit Task 6**

```powershell
git add -- examples/haitun-workspace/systems/supervisor.py examples/haitun-workspace/tools/_subagent_helpers.py tests/integration/test_haitun_supervisor.py
git commit -m "feat(haitun): manage per-user supervisor sessions"
```

## Task 7: Integrate Supervision into the Haitun Dynamic Prompt

**Files:**
- Modify: `examples/haitun-workspace/systems/system.py`
- Modify: `tests/integration/test_haitun_supervisor.py`
- Modify: `tests/integration/test_haitun_profile.py`

- [ ] **Step 1: Write failing integration tests**

Monkeypatch the module-level manager to return a valid `broaden` advice. Assert:

- `system_before_turn()` returns that advice;
- `system_prompt_builder()` renders exactly one `## 旁路监督建议` section;
- explicit user text such as `只回答这个问题，不要展开` produces a suppression instruction;
- no advice produces no empty supervision section;
- the existing profile section and supervision policy remain exactly once.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py tests/integration/test_haitun_profile.py -k "prompt or before_turn"
```

- [ ] **Step 3: Add a lazy manager factory**

Avoid creating anyio locks at import time in an unknown event loop. Use a module-level cache keyed by resolved Workspace string:

```python
_SUPERVISOR_MANAGERS: dict[str, Any] = {}

def _supervisor_manager(workspace_dir: anyio.Path):
    key = str(workspace_dir)
    manager = _SUPERVISOR_MANAGERS.get(key)
    if manager is None:
        manager = SupervisorManager(workspace_dir, ...)
        _SUPERVISOR_MANAGERS[key] = manager
    return manager
```

- [ ] **Step 4: Expose `system_before_turn()`**

Resolve the Workspace exactly as the existing builder does. Return `{}` when the current Session ID begins with `supervisor-`, the message is not learning-oriented, or no stable identity is available. Otherwise call `manager.supervise(user_message)`.

- [ ] **Step 5: Render advice in `system_prompt_builder()`**

Read `user_message.get("supervisor_advice")`, validate it defensively, call `render_advice_prompt()`, and place the section after the current knowledge profile and before the existing fixed rules. Remove the old fixed fifth-turn mandatory breakout rule from `_build_profile_policy`; retain certainty, counterexample, and Socratic behavior until separately redesigned.

- [ ] **Step 6: Run focused tests and lint**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py tests/integration/test_haitun_profile.py
uv run ruff check examples/haitun-workspace/systems/system.py tests/integration/test_haitun_supervisor.py tests/integration/test_haitun_profile.py
```

- [ ] **Step 7: Commit Task 7**

```powershell
git add -- examples/haitun-workspace/systems/system.py tests/integration/test_haitun_supervisor.py tests/integration/test_haitun_profile.py
git commit -m "feat(haitun): inject breakout supervision"
```

## Task 8: Add End-to-End Mock Coverage

**Files:**
- Create: `tests/integration/test_haitun_supervisor_e2e.py`

- [ ] **Step 1: Build a per-request mock AI server**

Use an inline aiohttp server with a `nonlocal` request counter. The supervisor request returns strict JSON with `breakout.type = "broaden"`; the main request asserts its system prompt contains `## 旁路监督建议` and returns a marker response such as `FRAMEWORK_RESPONSE`.

- [ ] **Step 2: Write the end-to-end assertion**

Start the main Session with the Haitun Workspace, post a learning question with `user_id` and `profile_id`, consume SSE, and assert:

```text
supervisor request occurred before main request
supervisor request contains only the isolated payload
main system prompt contains the validated advice
final response is FRAMEWORK_RESPONSE
map and heatmap files exist
```

- [ ] **Step 3: Run the test and fix only integration defects**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor_e2e.py -v
```

Expected: PASS with no real network or API key.

- [ ] **Step 4: Run adjacent integration tests**

```powershell
uv run pytest -q tests/integration/test_haitun_supervisor.py tests/integration/test_haitun_supervisor_e2e.py tests/integration/test_haitun_profile.py tests/integration/test_session_workspace.py
```

- [ ] **Step 5: Commit Task 8**

```powershell
git add -- tests/integration/test_haitun_supervisor_e2e.py
git commit -m "test: cover supervisor breakout loop"
```

## Task 9: Add the Local Breakout Demonstration

**Files:**
- Create: `examples/haitun-workspace/demo_supervisor_breakout.py`
- Modify: `tests/integration/test_haitun_supervisor.py`

- [ ] **Step 1: Write a failing demo smoke test**

Run the demo in a subprocess with `HAITUN_DEMO_WORKSPACE=tmp_path` and `PYTHONUTF8=1`. Assert stdout contains:

```text
USER alice
BREAKOUT broaden
MAP_STATUS created
USER bob
BREAKOUT deepen
MAP_STATUS created
```

Snapshot the real Workspace map directory before and after and assert the demo did not modify it.

- [ ] **Step 2: Implement the deterministic demo**

Use a fake supervisor chat function returning controlled JSON so the demo requires no API key. Exercise the real `SupervisorManager`, stores, validation, and prompt rendering for Alice and Bob. Print paths inside the temporary demo Workspace.

- [ ] **Step 3: Run the demo and test**

```powershell
uv run --no-cache python examples/haitun-workspace/demo_supervisor_breakout.py
uv run pytest -q tests/integration/test_haitun_supervisor.py -k demo
```

Expected: both users show distinct heatmaps and the expected breakout types.

- [ ] **Step 4: Commit Task 9**

```powershell
git add -- examples/haitun-workspace/demo_supervisor_breakout.py tests/integration/test_haitun_supervisor.py
git commit -m "feat(haitun): demo background breakout supervision"
```

## Task 10: Synchronize Documentation and Run the Definition of Done

**Files:**
- Modify: `src/psi_agent/session/AGENTS.md`
- Modify: `examples/haitun-workspace/AGENTS.md`
- Inspect and modify if applicable: `README.md`, `README_en.md`

- [ ] **Step 1: Document the lifecycle contract**

In Session AGENTS.md, document:

```text
system_before_turn -> system_prompt_builder -> AI loop -> system_after_turn
```

State timeout degradation, cancellation propagation, schedule bypass, and advice non-persistence.

- [ ] **Step 2: Document the Haitun supervisor architecture**

Add the dedicated Workspace, per-user logical Session, shared maps, private heatmaps, strict input allowlist, five breakout modes, and recursion prevention. Explicitly record that the supervisor cannot audit main-answer compliance because it never receives the main answer.

- [ ] **Step 3: Run formatting and focused static checks**

```powershell
uv run ruff format src/psi_agent/session/system_prompt.py src/psi_agent/session/agent.py tests/psi_agent/session/test_session.py tests/psi_agent/session/test_agent.py examples/haitun-workspace/systems examples/haitun-supervisor-workspace tests/integration/test_haitun_supervisor.py tests/integration/test_haitun_supervisor_e2e.py
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Expected: all commands exit 0. If unrelated pre-existing failures exist, record the exact command and failure separately; do not suppress them.

- [ ] **Step 4: Run the full test suite**

```powershell
uv run pytest -v
```

Expected: all tests pass. Do not claim completion from focused tests alone.

- [ ] **Step 5: Run build and local smoke commands**

```powershell
uv build
uv run psi-agent --help
uv run --no-cache python examples/haitun-workspace/demo_supervisor_breakout.py
```

Expected: build succeeds, CLI help exits 0, and the demo prints both breakout cases.

- [ ] **Step 6: Inspect logs, cancellation, and secrets**

Confirm new logs use loguru levels consistent with neighboring code, no API key or raw `user_id` is logged, `CancelledError` is not swallowed, atomic temp files are cleaned in shielded cancellation scopes, and no `asyncio` or synchronous `pathlib` I/O was added inside async functions.

- [ ] **Step 7: Commit documentation and final cleanup**

```powershell
git add -- src/psi_agent/session/AGENTS.md examples/haitun-workspace/AGENTS.md README.md README_en.md
git commit -m "docs: document background supervisor lifecycle"
```

Only add README files if they actually changed.

## Execution Notes

- Preserve unrelated user changes in the dirty parent worktree.
- Before execution, use `superpowers:using-git-worktrees` to choose or create an isolated worktree. Do not reuse the older adaptive-profile worktree without confirming its state.
- Use TDD for every behavior change: RED, minimal GREEN, then refactor.
- Do not start a second Gateway. Supervisor Sessions reuse the parent AI when available.
- Never include the main Assistant response or reasoning in tests, payload fixtures, or real supervisor requests.
- After every delegated task, review both spec compliance and code quality before proceeding.
