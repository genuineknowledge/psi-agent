# Adaptive User Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a multi-user, topic-aware adaptive coaching loop with double-speed profile updates, one prompt injection point, grounded Wiki breakout suggestions, structural response validation, and guaranteed after-turn persistence.

**Architecture:** Session extracts trusted profile metadata from channel parameters and passes an immutable `TurnContext` to workspace lifecycle hooks. The Haitun profile engine owns identity hashing, versioned per-user persistence, topic resolution, EMA calculations, and migration; `system.py` only composes base prompt plus one dynamic coaching policy. Session buffers final-answer rounds for structural validation and performs at most one correction before committing and invoking after-turn.

**Tech Stack:** Python 3.14, anyio, aiohttp, PyYAML, loguru, pytest, Ruff, ty.

---

## File Map

- `src/psi_agent/session/runtime_context.py`: immutable turn identity/context and ContextVar scope.
- `src/psi_agent/session/channel_adapter.py`: extract reserved profile metadata without forwarding it to AI.
- `src/psi_agent/session/system_prompt.py`: invoke optional workspace hook parameters and expose turn-policy validation.
- `src/psi_agent/session/agent.py`: resolve conflict markers, preserve upstream history-kind behavior, buffer/validate final response, commit, and run after-turn.
- `src/psi_agent/session/protocol.py`: typed `TurnPolicy` and validation result models.
- `examples/haitun-workspace/tools/_user_profile.py`: identity-safe storage, migration, topics, double-speed EMA, global profile, and locking.
- `examples/haitun-workspace/systems/system.py`: single adaptive prompt injection and Wiki lookup fallback.
- `examples/haitun-workspace/tools/_llm_wiki_impl.py`: reusable search-to-related recommendation helper if existing primitives cannot express the fallback cleanly.
- `tests/psi_agent/session/*`: runtime context, adapter, lifecycle, conflict-resolution, and validator tests.
- `tests/integration/test_haitun_profile.py`: profile engine, migration, multi-user, Wiki, and end-to-end prompt tests.
- `src/psi_agent/session/AGENTS.md`, `examples/haitun-workspace/AGENTS.md`: lifecycle and storage documentation.

### Task 1: Restore a Valid Session Runtime and Preserve Both Conflict Sides

**Files:**
- Modify: `src/psi_agent/session/agent.py:140-400`
- Test: `tests/psi_agent/session/test_agent.py`

- [ ] **Step 1: Add a failing source-integrity test**

```python
def test_agent_source_has_no_merge_markers() -> None:
    source = Path(agent_module.__file__).read_text(encoding="utf-8")
    assert "<<<<<<<" not in source
    assert "=======" not in source
    assert ">>>>>>>" not in source
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest tests/psi_agent/session/test_agent.py::test_agent_source_has_no_merge_markers -v`

Expected: collection or assertion failure caused by the existing conflict markers.

- [ ] **Step 3: Resolve the conflict with both required behaviors**

Retain the upstream `session_id_scope`, `message_kind`, `with_kind`, and `messages_for_ai` behavior. Retain the stashed behavior that passes the current user message to `SystemPrompt.ensure` and calls `run_after_turn` after a committed final assistant response. The merged shape must be:

```python
with session_id_scope(self._conversation.session_id):
    async with self._conversation:
        await self._tool_registry.refresh()
        await self._schedule_registry.refresh()
        await self._system_prompt.ensure(self._conversation, user_message, turn_context)
        # pending chunks, committed user message, ReAct rounds
        # assistant/tool rows use with_kind(..., turn_response_kind)
        # AI messages use messages_for_ai(...)
```

- [ ] **Step 4: Verify import and focused agent behavior GREEN**

Run: `uv run pytest tests/psi_agent/session/test_agent.py -v`

Expected: all agent tests pass and the module imports without syntax errors.

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/agent.py tests/psi_agent/session/test_agent.py
git commit -m "fix: resolve session agent lifecycle conflict"
```

### Task 2: Add Trusted Turn Identity Context

**Files:**
- Modify: `src/psi_agent/session/runtime_context.py`
- Modify: `src/psi_agent/session/channel_adapter.py`
- Modify: `src/psi_agent/session/agent.py`
- Modify: `src/psi_agent/session/system_prompt.py`
- Test: `tests/psi_agent/session/test_channel_adapter.py`
- Test: `tests/psi_agent/session/test_session.py`

- [ ] **Step 1: Write failing metadata extraction tests**

```python
@pytest.mark.anyio
async def test_parse_request_reserves_profile_metadata(make_request) -> None:
    request = make_request({
        "messages": [{"role": "user", "content": "hello"}],
        "profile_id": "alice",
        "user_id": "telegram:42",
        "temperature": 0.2,
    })
    user, ai_params, identity = await ChannelAdapter.parse_request(request)
    assert identity == {"profile_id": "alice", "user_id": "telegram:42"}
    assert ai_params == {"temperature": 0.2}
```

Add a lifecycle test proving a builder accepting `(user_message, turn_context)` receives the session fallback when IDs are absent.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/psi_agent/session/test_channel_adapter.py tests/psi_agent/session/test_session.py -v`

Expected: tuple arity/signature failures because identity context does not exist.

- [ ] **Step 3: Implement immutable context and reserved parameters**

```python
@dataclass(frozen=True)
class TurnContext:
    session_id: str
    profile_id: str = ""
    user_id: str = ""
    channel: str = ""
    response_kind: str = "chat"

    @property
    def identity_source(self) -> str:
        if self.profile_id:
            return "profile"
        if self.user_id:
            return self.channel or "user"
        return "session"
```

`ChannelAdapter.parse_request` removes `profile_id`, `user_id`, and `channel` from AI passthrough. `SessionAgent.run` constructs `TurnContext` using these values plus `conversation.session_id`. Extend `SystemPrompt.ensure` and `run_after_turn` to pass context only when the workspace hook accepts it, preserving zero-argument and one-argument workspace compatibility.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/psi_agent/session/test_channel_adapter.py tests/psi_agent/session/test_session.py -v`

Expected: metadata is reserved, AI passthrough remains intact, legacy hook signatures still pass.

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/runtime_context.py src/psi_agent/session/channel_adapter.py src/psi_agent/session/agent.py src/psi_agent/session/system_prompt.py tests/psi_agent/session/test_channel_adapter.py tests/psi_agent/session/test_session.py
git commit -m "feat: pass trusted turn identity to workspace hooks"
```

### Task 3: Replace the Profile Engine with Version-3 Multi-User Aggregates

**Files:**
- Modify: `examples/haitun-workspace/tools/_user_profile.py`
- Modify: `examples/haitun-workspace/tools/profile_tools.py`
- Test: `tests/integration/test_haitun_profile.py`

- [ ] **Step 1: Write failing persistence and identity tests**

```python
@pytest.mark.anyio
async def test_two_users_persist_to_independent_profile_files(tmp_path, monkeypatch) -> None:
    module = _load_profile_module(monkeypatch)
    alice = await module.get_profile(str(tmp_path), profile_id="alice", session_id="s1")
    bob = await module.get_profile(str(tmp_path), profile_id="bob", session_id="s2")
    alice.update("简单解释事务", "unused")
    bob.update("详细推导事务隔离机制", "unused")
    await alice.save()
    await bob.save()
    files = list((tmp_path / "wiki" / "profiles").glob("*/_profile.md"))
    assert len(files) == 2
    assert all("alice" not in str(path) and "bob" not in str(path) for path in files)
```

Add failing tests for raw-text absence, session fallback isolation, version-2 migration, raw-history migration without empty overwrite, and per-profile cache separation.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/integration/test_haitun_profile.py -v`

Expected: signature/schema/migration failures against the current workspace-global engine.

- [ ] **Step 3: Implement identity hashing, typed schema, locking, and migration**

Use built-in generics, a stable SHA-256 digest prefix, `anyio.Lock`, and atomic tempfile replacement. Required API:

```python
async def get_profile(
    workspace_raw: str = "",
    *,
    profile_id: str = "",
    user_id: str = "",
    channel: str = "",
    session_id: str = "",
) -> UserProfile: ...
```

Identity precedence is `profile_id`, then namespaced `user_id`, then `session_id`. Implement version-2 and raw-history migration in memory and ensure the saved version-3 file contains no raw messages.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/integration/test_haitun_profile.py -k "identity or persistence or migration" -v`

Expected: independent files, safe digest paths, non-empty migration, and no raw transcript text.

- [ ] **Step 5: Commit**

```bash
git add examples/haitun-workspace/tools/_user_profile.py examples/haitun-workspace/tools/profile_tools.py tests/integration/test_haitun_profile.py
git commit -m "feat: isolate versioned learner profiles by user"
```

### Task 4: Implement Topic Resolution, Signed Double-Speed EMA, and Global Warm Start

**Files:**
- Modify: `examples/haitun-workspace/tools/_user_profile.py`
- Test: `tests/integration/test_haitun_profile.py`

- [ ] **Step 1: Write failing behavioral tests**

```python
def test_style_instruction_inherits_last_topic(profile) -> None:
    key = profile.update("解释数据库事务", "unused")
    assert profile.update("继续详细推导底层原理和公式", "unused") == key

def test_signed_volatility_detects_low_to_high_switch(profile) -> None:
    key = profile.update("简单说", "unused")
    profile.update("详细推导", "unused")
    assert profile.short_weight(profile.topics[key], "depth") > 0.5
```

Add tests for unrelated topic creation, similar-topic merge, unrelated non-merge, low-phrase precedence, one-turn response, stable long-term convergence, and `70% global + 30% neutral` initialization.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/integration/test_haitun_profile.py -k "topic or ema or volatility or warm" -v`

Expected: current fixed-width tokenizer creates style topics and volatility misses direction switches.

- [ ] **Step 3: Implement deterministic resolver and signed directions**

Store recent directions as `-1`, `0`, `1`. Detect style/meta instructions before extracting a new topic. Build candidate labels from quoted text, English technical terms, `X是什么/解释X/关于X` question objects, and normalized Chinese phrases with generic prefixes removed. Match label/aliases before weighted keyword overlap. Clamp short weight to `[0.25, 0.85]` and preserve `alpha_long=0.35`, `alpha_short=0.80`.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/integration/test_haitun_profile.py -k "topic or ema or volatility or warm" -v`

Expected: style switches update the same topic within one turn and unrelated topics remain independent.

- [ ] **Step 5: Commit**

```bash
git add examples/haitun-workspace/tools/_user_profile.py tests/integration/test_haitun_profile.py
git commit -m "feat: add adaptive topic and double-speed profile updates"
```

### Task 5: Consolidate Prompt Injection and Add Grounded Wiki Fallbacks

**Files:**
- Modify: `examples/haitun-workspace/systems/system.py`
- Modify: `examples/haitun-workspace/tools/_llm_wiki_impl.py`
- Test: `tests/integration/test_haitun_profile.py`

- [ ] **Step 1: Write failing prompt and Wiki tests**

```python
@pytest.mark.anyio
async def test_prompt_contains_exactly_one_adaptive_policy(haitun_system, context) -> None:
    prompt = await haitun_system.system_prompt_builder(
        {"role": "user", "content": "简单解释过拟合"}, context
    )
    assert prompt.count("## 当前知识点学习画像") == 1
    assert prompt.count("## 强制监督规则") == 1
    assert "基于当前知识点画像的学习监督规则" not in prompt
```

Add tests proving base prompt construction creates no topic, third response uses Socratic policy, fifth response uses breakout when familiarity exceeds 0.5, Wiki search resolves a non-identical slug, and an empty Wiki injects no fabricated title.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/integration/test_haitun_profile.py -k "prompt or wiki or socratic or breakout" -v`

Expected: duplicate profile sections, off-by-one Socratic trigger, and exact-slug Wiki failure.

- [ ] **Step 3: Implement one integration point and Wiki recommendation helper**

Remove all profile work from `System.build_system_prompt`. The module-level builder accepts `(user_message, turn_context)`, builds the base once, loads the correct user profile, computes `current_turn = turns + 1`, and injects one policy below `CACHE_BOUNDARY`. Implement `wiki_recommend_impl` using search candidate, exact page slug, co-citation, direct links/backlinks, then tag similarity. Return existing page titles only.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/integration/test_haitun_profile.py -k "prompt or wiki or socratic or breakout" -v`

Expected: one profile section, correct turn triggers, grounded Wiki fallback, no empty general topics.

- [ ] **Step 5: Commit**

```bash
git add examples/haitun-workspace/systems/system.py examples/haitun-workspace/tools/_llm_wiki_impl.py tests/integration/test_haitun_profile.py
git commit -m "feat: inject one adaptive policy with wiki guidance"
```

### Task 6: Add Turn Policy Validation and One Correction Round

**Files:**
- Modify: `src/psi_agent/session/protocol.py`
- Modify: `src/psi_agent/session/system_prompt.py`
- Modify: `src/psi_agent/session/agent.py`
- Test: `tests/psi_agent/session/test_agent.py`
- Test: `tests/psi_agent/session/test_protocol.py`

- [ ] **Step 1: Write failing validator tests**

```python
def test_turn_policy_reports_missing_structures() -> None:
    policy = TurnPolicy(
        require_certainty=True,
        require_counterexample=True,
        require_socratic=True,
        require_breakout=True,
    )
    result = policy.validate("普通回答")
    assert result.violations == (
        "certainty_marker",
        "counterexample",
        "socratic_question",
        "breakout",
    )
```

Add an inline AI server test returning one invalid stop response then one corrected response. Assert only the corrected response is committed and after-turn receives corrected content. Add a second test proving correction is capped and fallback terminates.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/psi_agent/session/test_protocol.py tests/psi_agent/session/test_agent.py -k "policy or correction" -v`

Expected: missing policy types and no correction round.

- [ ] **Step 3: Implement typed validation and buffered final-answer rounds**

Add `TurnPolicy` and `ValidationResult` dataclasses. `SystemPrompt.ensure` stores the current policy returned by a workspace `system_turn_policy` hook or defaults to no requirements. In `SessionAgent`, buffer content for stop rounds when policy requirements exist; validate before yielding/committing, append precise correction feedback as an internal non-display message, and request one corrected completion. After the second failure, append only deterministic headings for missing counterexample/breakout and log remaining violations.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/psi_agent/session/test_protocol.py tests/psi_agent/session/test_agent.py -k "policy or correction" -v`

Expected: corrected content is the only committed/displayed final answer and retry count never exceeds one.

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/protocol.py src/psi_agent/session/system_prompt.py src/psi_agent/session/agent.py tests/psi_agent/session/test_protocol.py tests/psi_agent/session/test_agent.py
git commit -m "feat: validate adaptive coaching responses"
```

### Task 7: Wire After-Turn Identity, Locking, and Final Content Persistence

**Files:**
- Modify: `examples/haitun-workspace/systems/system.py`
- Modify: `examples/haitun-workspace/tools/_user_profile.py`
- Modify: `src/psi_agent/session/agent.py`
- Test: `tests/integration/test_haitun_profile.py`
- Test: `tests/psi_agent/session/test_agent.py`

- [ ] **Step 1: Write failing end-to-end lifecycle tests**

```python
@pytest.mark.anyio
async def test_after_turn_updates_only_selected_user(tmp_path, monkeypatch) -> None:
    module = _load_profile_module(monkeypatch)
    alice_context = TurnContext(session_id="s1", profile_id="alice")
    bob_context = TurnContext(session_id="s2", profile_id="bob")
    user = {"role": "user", "content": "简单解释事务"}
    await system_after_turn(user, {"role": "assistant", "content": "A"}, alice_context)
    await system_after_turn(user, {"role": "assistant", "content": "B"}, bob_context)
    alice = await module.get_profile(str(tmp_path), profile_id="alice", session_id="s1")
    bob = await module.get_profile(str(tmp_path), profile_id="bob", session_id="s2")
    assert alice.profile_key != bob.profile_key
    assert alice.topics != bob.topics
```

Add tests for concurrent same-profile updates, save failure warning without response rollback, schedule turns not mutating user profiles, and final corrected assistant content passed to after-turn.

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/integration/test_haitun_profile.py tests/psi_agent/session/test_agent.py -k "after_turn or concurrent or save_failure" -v`

Expected: global cache/path mixing or unsupported context signature.

- [ ] **Step 3: Implement locked record-and-save and context-aware hook**

Expose one atomic API:

```python
async def record_turn(self, user_text: str) -> str:
    async with self._lock:
        topic_key = self.update(user_text)
        await self.save()
        return topic_key
```

`system_after_turn` resolves the same identity as the builder and calls `record_turn`. It ignores non-chat schedule kinds. The assistant text is not passed into or persisted by the profile engine.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/integration/test_haitun_profile.py tests/psi_agent/session/test_agent.py -k "after_turn or concurrent or save_failure" -v`

Expected: identity-isolated atomic updates and no rollback of delivered replies on profile failure.

- [ ] **Step 5: Commit**

```bash
git add examples/haitun-workspace/systems/system.py examples/haitun-workspace/tools/_user_profile.py src/psi_agent/session/agent.py tests/integration/test_haitun_profile.py tests/psi_agent/session/test_agent.py
git commit -m "feat: persist final adaptive profile updates safely"
```

### Task 8: Documentation and Full Verification

**Files:**
- Modify: `src/psi_agent/session/AGENTS.md`
- Modify: `examples/haitun-workspace/AGENTS.md`
- Modify: `README.md`
- Modify: `README_en.md`
- Test: all relevant test suites

- [ ] **Step 1: Update behavior documentation**

Document reserved identity request fields, fallback semantics, lifecycle hook signatures, profile paths/schema, response buffering/correction behavior, Wiki fallback chain, migration, schedule exclusion, and explicit limitations.

- [ ] **Step 2: Run source-integrity and targeted verification**

```powershell
rg -n "^(<<<<<<<|=======|>>>>>>>)" src tests examples
uv run ruff check src tests examples/haitun-workspace/systems/system.py examples/haitun-workspace/tools/_user_profile.py examples/haitun-workspace/tools/_llm_wiki_impl.py
uv run ruff format --check .
uv run ty check
uv run pytest -q -m "not schedule"
```

Expected: no conflict markers; all commands exit 0; no failed tests.

- [ ] **Step 3: Run schedule and end-to-end smoke verification**

```powershell
uv run pytest -q -m schedule
uv run python examples/haitun-workspace/systems/system.py
```

Expected: schedule tests pass; prompt smoke contains exactly one profile/policy section and no traceback.

- [ ] **Step 4: Inspect persisted privacy invariants**

Run a temporary two-user conversation smoke, then verify profile files contain aggregate keys and do not contain the exact test user/assistant sentences.

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/session/AGENTS.md examples/haitun-workspace/AGENTS.md README.md README_en.md
git commit -m "docs: document adaptive multi-user coaching"
```

## Plan Self-Review

- Spec coverage: identity, storage, migration, topic resolution, double-speed EMA, global warm start, single injection, Wiki fallback, validation, one correction, after-turn, cancellation/error behavior, tests, and limitations are assigned to Tasks 1–8.
- Scope: tasks are sequential because Session repair and context contracts precede profile integration; no independent subsystem is silently deferred.
- Type consistency: all workspace hooks use `TurnContext`; all profile retrieval uses the same keyword-only identity fields; `TurnPolicy` is the single validator contract.
- Privacy consistency: assistant content may be used by Session validation but never enters profile persistence.
- Retry consistency: exactly one correction generation is allowed, followed by deterministic fallback and commit.
