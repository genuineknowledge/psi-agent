"""Criteria for the one request assembly point.

These tests exist because the failure they guard against is invisible from a
single turn.  An over-budget request used to be *assembled fine* and rejected by
the upstream with an HTTP 400, at which point the session was wedged: every
retry rebuilt the same too-large body.  Recovering meant hand-editing a history
file in production.  So the load-bearing claim here is not "we try to shrink" but
"a body that exceeds the budget cannot be built".

The hysteresis criterion is the one most easily written green-but-not-load-
bearing: assert only "it shrank" and an implementation that shaves one row per
turn passes, while being *more* expensive than never shrinking at all (every
shrink rewrites the prefix and voids the upstream cache).  So it asserts the
byte-for-byte identity of the second turn's prefix, which is the property the
cache actually keys on.
"""

from __future__ import annotations

from typing import Any

from psi_agent.protocol import DEFAULT_MAX_CONTEXT_TOKENS as PROTOCOL_DEFAULT_MAX_CONTEXT_TOKENS
from psi_agent.session.request_assembly import (
    DEFAULT_CHARS_PER_TOKEN,
    DEFAULT_MAX_CONTEXT_TOKENS,
    MAX_CHARS_PER_TOKEN,
    MIN_ADOPTABLE_TOKENS,
    SHRINK_TARGET_FRACTION,
    AssembledRequest,
    RequestAssembler,
    payload_chars,
    resolve_max_context_tokens,
)

_TOOLS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}
]


def _history(rows: int, *, chars: int = 2000) -> list[dict[str, Any]]:
    """A system prompt plus ``rows`` turns of user/assistant chatter."""
    history: list[dict[str, Any]] = [{"role": "system", "content": "You are an agent."}]
    for i in range(rows):
        history.append({"role": "user", "content": f"q{i} " + "问" * chars})
        history.append({"role": "assistant", "content": f"a{i} " + "答" * chars})
    return history


def _assemble(assembler: RequestAssembler, history: list[dict[str, Any]]) -> AssembledRequest:
    return assembler.build(history, _TOOLS, {"routing": {"session_id": "s1"}})


def test_over_budget_history_yields_within_budget_payload() -> None:
    """The core promise: too-large history in, budget-respecting payload out."""
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)

    unbudgeted = payload_chars({"messages": history, "tools": _TOOLS, "stream": True})
    assert unbudgeted > assembler.budget_chars, "fixture must actually exceed the budget"

    result = _assemble(assembler, history)

    assert result.chars <= result.budget_chars
    assert result.within_budget
    assert result.elided_rows > 0
    # And the measurement is the real thing, not a stored estimate.
    assert payload_chars(result.body) == result.chars


def test_shrink_drops_well_below_budget_not_to_just_barely_fitting() -> None:
    """One decisive shrink, so later turns grow on a prefix that stays put.

    The bound is written as a literal fraction on purpose.  Deriving it from
    ``SHRINK_TARGET_FRACTION`` makes the test move with the implementation: set
    the constant to 1.0 — i.e. shave to just-fit, the exact behaviour this
    criterion exists to forbid — and a derived assertion still passes.  Verified
    by mutation, not assumed.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    result = _assemble(assembler, _history(20))

    assert result.chars <= result.budget_chars * 0.6, (
        f"shrank only to {result.chars} of budget {result.budget_chars}; "
        f"hysteresis requires dropping well clear of the ceiling"
    )
    # And the shipped constant is in fact a real shrink, not a no-op.
    assert 0 < SHRINK_TARGET_FRACTION <= 0.6


def test_consecutive_turns_trigger_exactly_one_shrink() -> None:
    """Hysteresis, asserted on the bytes the upstream cache keys on.

    Turn 1 goes over budget and shrinks.  Turn 2 appends a new exchange to the
    same history.  The assertion is that turn 2's payload *starts with* turn 1's
    messages byte-for-byte: the shrink already happened, so growth lands at the
    tail and the cached prefix survives.  A one-row-per-turn implementation
    changes an early row on turn 2 and fails this.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)

    first = _assemble(assembler, history)
    assert first.elided_rows > 0

    # A realistically sized new turn, not a token gesture: a three-character
    # append cannot push the payload back over budget, so a tiny one would let
    # a shave-to-just-fit implementation pass this criterion by accident.
    history.append({"role": "user", "content": "接着说 " + "续" * 2000})
    second = _assemble(assembler, history)

    first_msgs = first.body["messages"]
    second_msgs = second.body["messages"]
    assert second_msgs[: len(first_msgs)] == first_msgs, "turn 2 rewrote the cached prefix"
    assert len(second_msgs) == len(first_msgs) + 1
    assert second.within_budget

    # No *fresh* elision on turn 2: the sticky re-application is all that ran.
    assert second.elided_rows == first.elided_rows


def test_elision_leaves_a_handle_and_reports_the_original_size() -> None:
    """Nothing vanishes silently: every elided row says so, and how to find it."""
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    original_lengths = {len(row["content"]) for row in history[1:]}

    result = _assemble(assembler, history)

    handles = [
        m["content"]
        for m in result.body["messages"]
        if isinstance(m.get("content"), str) and m["content"].startswith("[已省略")
    ]
    assert len(handles) == result.elided_rows
    for handle in handles:
        assert "字符" in handle, handle
        assert "句柄" in handle, handle
        assert any(str(n) in handle for n in original_lengths), handle
        # Terseness is load-bearing: this is paid once per elided row.
        assert len(handle) < 80, handle

    # The stored history is untouched — the projection is what shrank.
    assert all(not row["content"].startswith("[已省略") for row in history[1:])
    # Row count is preserved, so tool_calls/tool pairing cannot break.
    assert len(result.body["messages"]) == len(history)


def test_handles_are_idempotent_across_turns() -> None:
    """Re-eliding an already-elided row must not nest handles.

    Without the sentinel check, turn N's handle becomes turn N+1's "original
    content", so the handle grows a layer every turn — the payload creeps back
    up and the prefix changes every turn, defeating the point.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)

    first = _assemble(assembler, history)
    for _ in range(3):
        history.append({"role": "user", "content": "再说"})
        latest = _assemble(assembler, history)

    handles = [
        m["content"]
        for m in latest.body["messages"]
        if isinstance(m.get("content"), str) and m["content"].startswith("[已省略")
    ]
    assert handles, "expected the sticky elisions to still be in place"
    for handle in handles:
        assert handle.count("[已省略") == 1, handle
        # The size reported is the *original* row's, not a previous handle's:
        # eliding an elided row would report ~40 chars and change the text, and
        # text that changes every turn is a rewritten prefix every turn.
        assert "2003 字符" in handle or "2004 字符" in handle, handle
    assert latest.chars >= first.chars  # grew at the tail, never re-shrank


def test_elision_succeeds_when_compaction_never_ran() -> None:
    """Level 1 does not depend on level 2.

    Compaction failing (LLM error, timeout, lock contention) used to leave the
    session with no way to get under the ceiling.  Here the history carries no
    ``compacted`` marker at all — the state after every compaction attempt has
    failed — and the budget is still met.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    assert not any(row["role"] == "compacted" for row in history)

    result = _assemble(assembler, history)

    assert result.within_budget
    assert result.chars <= result.budget_chars


def test_second_shrink_leaves_earlier_handles_byte_identical() -> None:
    """A later shrink must not rewrite the handles an earlier shrink installed.

    Reachable in production because history keeps growing: eventually a session
    that already shrank once crosses the budget again.  When fresh elision runs
    at that point it walks rows that are *already* handles, and re-eliding one
    replaces "original was 2003 chars" with "original was 40 chars" — a text
    change on an early row, which is a rewritten prefix and a voided cache, in
    the middle of the operation whose entire purpose is to protect the prefix.

    Added after mutation review: removing the already-handled guard in
    ``_elidible_candidates`` left every other test green.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)

    first = _assemble(assembler, history)
    assert first.elided_rows > 0
    first_handles = {
        i: m["content"]
        for i, m in enumerate(first.body["messages"])
        if isinstance(m.get("content"), str) and m["content"].startswith("[已省略")
    }

    # Grow until a *second* shrink is genuinely required.
    for i in range(20):
        history.append({"role": "user", "content": f"more{i} " + "增" * 2000})
        history.append({"role": "assistant", "content": f"ok{i} " + "回" * 2000})
    second = _assemble(assembler, history)

    assert second.elided_rows > first.elided_rows, "fixture must force a second shrink"
    for i, text in first_handles.items():
        assert second.body["messages"][i]["content"] == text, (
            f"row {i} handle rewritten by the second shrink: {text!r} -> {second.body['messages'][i]['content']!r}"
        )


def test_un_elidible_floor_is_reported_not_hidden() -> None:
    """When even the floor exceeds the budget, say so instead of pretending.

    The floor is the system prompt, the tool schemas, the two rows of the turn in
    flight, and one handle per elided row.  Below that, elision has nothing left
    to give.  This is reachable in practice: the design doc notes the shipped
    default of 100k tokens is *under* the ~117k of fixed overhead in one
    deployment.  The contract is that ``within_budget`` goes False and the caller
    can see it — a silently-oversized payload is what wedged production before.
    """
    assembler = RequestAssembler(max_context_tokens=400)
    result = _assemble(assembler, _history(20))

    assert not result.within_budget
    assert result.chars > result.budget_chars
    assert result.elided_rows > 0, "it must still have shrunk as far as it could"


def test_ratio_defaults_on_the_first_turn_then_calibrates() -> None:
    """Budget is denominated in chars, so the chars/token ratio must be measured.

    Measured spread across real payloads is 2.6x (about 1.56 for CJK prose,
    3.5-4 for ASCII tool JSON), which is why a single hardcoded coefficient is
    wrong for somebody.  Turn 1 has nothing to go on and uses the conservative
    default; every later turn uses the previous turn's own ``prompt_tokens``.
    """
    assembler = RequestAssembler(max_context_tokens=10_000)
    assert not assembler.calibrated
    assert assembler.chars_per_token == DEFAULT_CHARS_PER_TOKEN
    assert assembler.budget_chars == int(10_000 * DEFAULT_CHARS_PER_TOKEN)

    assembler.calibrate(sent_chars=35_000, prompt_tokens=10_000)

    assert assembler.calibrated
    assert assembler.chars_per_token == 3.5
    assert assembler.budget_chars == 35_000, "a roomier real ratio must widen the budget"


def test_calibration_ignores_junk_and_clamps_outliers() -> None:
    """A bad number from upstream must not silently move the budget."""
    assembler = RequestAssembler(max_context_tokens=10_000)

    for sent, tokens in ((0, 100), (100, 0), (-5, 10), (100, -1)):
        assembler.calibrate(sent_chars=sent, prompt_tokens=tokens)
        assert not assembler.calibrated, (sent, tokens)
        assert assembler.chars_per_token == DEFAULT_CHARS_PER_TOKEN

    assembler.calibrate(sent_chars=1_000_000, prompt_tokens=10)
    assert assembler.chars_per_token == MAX_CHARS_PER_TOKEN


def test_zero_max_context_tokens_disables_the_ceiling() -> None:
    """``0`` means "no ceiling", matching the AI layer's sentinel."""
    assembler = RequestAssembler(max_context_tokens=0)
    assert assembler.budget_chars == 0

    result = _assemble(assembler, _history(20))

    assert result.within_budget
    assert result.elided_rows == 0


def test_recent_rows_and_system_prompt_are_never_elided() -> None:
    """The instruction set and the turn in flight have to survive."""
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)

    result = _assemble(assembler, history)
    messages = result.body["messages"]

    assert messages[0]["content"] == "You are an agent."
    for row in messages[-2:]:
        assert not row["content"].startswith("[已省略"), row["content"][:60]


def test_extra_params_cannot_displace_the_measured_payload() -> None:
    """Budget is computed over the body that ships, so nothing may overwrite it."""
    assembler = RequestAssembler(max_context_tokens=0)
    history = _history(1)

    body = assembler.build(
        history,
        _TOOLS,
        {"messages": [{"role": "user", "content": "hijack"}], "tools": [], "stream": False, "temperature": 0.3},
    ).body

    assert body["messages"][0]["content"] == "You are an agent."
    assert body["tools"] == _TOOLS
    assert body["stream"] is True
    assert body["temperature"] == 0.3


def test_resolve_max_context_tokens_reads_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("PSI_MAX_CONTEXT_TOKENS", "12345")
    assert resolve_max_context_tokens(-1) == 12345
    assert resolve_max_context_tokens(999) == 999, "explicit value wins over env"

    monkeypatch.setenv("PSI_MAX_CONTEXT_TOKENS", "not-a-number")
    assert resolve_max_context_tokens(-1) > 0, "junk env falls back instead of crashing"


def test_adopt_threshold_converges_with_the_ai_layer() -> None:
    """The AI layer's ceiling wins when it turns out to differ.

    Deployments configure ``PSI_MAX_CONTEXT_TOKENS`` on the AI container only,
    so the session can start with the default and learn the real number the
    first time a compaction signal arrives.
    """
    assembler = RequestAssembler(max_context_tokens=10_000)

    assembler.adopt_threshold(0)
    assert assembler.max_context_tokens == 10_000

    assembler.adopt_threshold(300_000)
    assert assembler.max_context_tokens == 300_000


def test_adopt_threshold_refuses_a_ceiling_under_the_fixed_overhead() -> None:
    """Converging downward onto an unsatisfiable ceiling is worse than diverging.

    100000 was the AI layer's default until 2026-09-04 and remains a value an
    operator can set by hand, while this deployment's system prompt alone
    measures ~117k tokens.  Adopting it would put every request permanently over
    budget, so elision would strip all history every turn and still fail — the
    arithmetic behind the 50-times-compacted task in the design doc.  So the
    floor wins and the divergence is logged instead.
    """
    assembler = RequestAssembler(max_context_tokens=DEFAULT_MAX_CONTEXT_TOKENS)

    assembler.adopt_threshold(100_000)

    assert assembler.max_context_tokens == DEFAULT_MAX_CONTEXT_TOKENS
    assert DEFAULT_MAX_CONTEXT_TOKENS >= MIN_ADOPTABLE_TOKENS, "the shipped default must clear its own floor"


def test_both_layers_share_one_fallback_ceiling() -> None:
    """The AI and Session layers must not drift back into separate defaults.

    They shipped 100000 and 200000 respectively, which is how production ran for
    a day against a ceiling below its own fixed overhead.  ``protocol`` owns the
    number now; this asserts the identity rather than the value, so raising the
    ceiling later does not require editing this criterion.
    """
    assert DEFAULT_MAX_CONTEXT_TOKENS == PROTOCOL_DEFAULT_MAX_CONTEXT_TOKENS


def test_resolver_falls_back_to_the_shared_ceiling(monkeypatch: Any) -> None:
    """With no env var, this layer's resolver must land on ``protocol``'s number.

    Covers the Session side only.  The AI layer's own resolution path is asserted
    in ``tests/psi_agent/ai/test_ai.py`` — a criterion here cannot see it, and
    claiming otherwise would leave a reintroduced literal there green.
    """
    monkeypatch.delenv("PSI_MAX_CONTEXT_TOKENS", raising=False)

    assert resolve_max_context_tokens(-1) == PROTOCOL_DEFAULT_MAX_CONTEXT_TOKENS
