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

import re
from typing import Any

from psi_agent._send_markers import iter_send_paths
from psi_agent.channel._markers import SendMarkerScanner
from psi_agent.protocol import (
    DEFAULT_MAX_CONTEXT_TOKENS as PROTOCOL_DEFAULT_MAX_CONTEXT_TOKENS,
)
from psi_agent.session.history_display import render_sent_files_note
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


def test_rows_at_or_after_the_watermark_are_exempt() -> None:
    """This turn's own output cannot be elided, however large it gets.

    The bug this forbids: the assistant row a multi-round turn wrote in round 1
    is no longer within the ``paired[:-2]`` guard by round 3 (several tool rows
    have piled up behind it), so it became an ordinary elision candidate.  The
    model then saw "my last message said nothing", apologised, and re-sent —
    and the re-send was elided the same way on the next round.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    produced_this_turn = len(history)
    history.append({"role": "assistant", "content": "本回合产出 " + "甲" * 2000})
    for i in range(4):
        history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "工具结果 " + "乙" * 2000})

    assembler.begin_turn(produced_this_turn)
    result = _assemble(assembler, history)

    messages = result.body["messages"]
    assert result.elided_rows > 0, "fixture must still exercise elision"
    for row in messages[produced_this_turn:]:
        assert not row["content"].startswith("[已省略"), f"this turn's own row was elided: {row['content'][:60]}"
    # And the exemption is not achieved by giving up on the budget.
    assert result.chars <= result.budget_chars
    assert result.within_budget


def test_send_marker_in_this_turns_output_survives_assembly() -> None:
    """The direct cause of users never receiving their document.

    ``[SEND:/path]`` is how the assistant hands a file to the Channel.  When the
    row carrying it was replaced by a handle, the upstream saw a turn in which
    nothing was sent, so the model apologised and re-sent — forever.  Asserted on
    the marker itself rather than on "some row was spared", because the marker is
    the payload whose loss the user actually felt.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    watermark = len(history)
    marker = "[SEND:/workspace/交付文档.md]"
    history.append({"role": "assistant", "content": "文档写好了 " + "丙" * 3000 + marker})
    for i in range(4):
        history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "结果 " + "丁" * 3000})

    assembler.begin_turn(watermark)
    result = _assemble(assembler, history)

    wire = "".join(m["content"] for m in result.body["messages"] if isinstance(m.get("content"), str))
    assert marker in wire, "the [SEND:] marker was elided out of the request"
    assert result.elided_rows > 0, "fixture must still exercise elision"


def _elide_a_row_carrying(content: str) -> tuple[str, AssembledRequest]:
    """Force ``content`` into an elided row; return its handle and the result.

    The row is placed in the oldest half of an over-budget history and no turn is
    begun, so nothing is exempt and ``_elidible_candidates`` reaches it.  Going
    through ``build`` rather than calling ``_handle_for`` directly is deliberate:
    the claim under test is about what the *wire payload* carries, and a test
    that pokes the private helper would stay green if the helper stopped being
    reached from the assembly path.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    history.insert(1, {"role": "assistant", "content": content})

    result = _assemble(assembler, history)

    handles = [
        m["content"]
        for m in result.body["messages"]
        if isinstance(m.get("content"), str) and m["content"].startswith("[已省略")
    ]
    marked = [h for h in handles if "已送达" in h]
    assert len(marked) == 1, f"expected exactly one handle to carry a delivery note, got {handles}"
    return marked[0], result


def test_elided_row_with_send_marker_names_the_delivered_file() -> None:
    """The cross-turn half of "the user never got their document".

    Last turn's assistant row really did carry ``[SEND:/…/方案.pdf]``, and the
    file really was delivered — but this turn that row is a handle, so the model
    reads a transcript in which it never sent anything.  Production symptom: the
    user asks "where is the document?" and the model either re-sends or denies
    having sent it.

    Widening the elision exemption cannot fix this: the exempt region would have
    to cover the whole of "one piece of work", which has no bound, and elision is
    the *only* hard budget guarantee (level 1).  So the elided row states what it
    delivered instead — passive, bounded, and paid only by rows that sent files.
    """
    handle, result = _elide_a_row_carrying("方案写好了 " + "丙" * 3000 + "[SEND:/workspace/方案.pdf]")

    assert "方案.pdf" in handle, handle
    # The directory is not worth the bytes; the file name is what the user named.
    assert "/workspace/" not in handle, handle
    assert result.within_budget


def test_delivery_note_is_not_scannable_as_a_send_marker() -> None:
    """The one criterion this card exists for: do not re-send the file.

    The Channel triggers delivery by scanning the model's output stream for
    ``[SEND:]`` (``channel/_markers.SendMarkerScanner``), and this model is known
    to copy handle formatting back out verbatim (production line 5874).  So a
    handle that quoted the marker literally would be transcribed by the model,
    scanned by the Channel, and the file would be delivered to the user a second
    time — a worse bug than the one being fixed.

    Asserted with the Channel's own scanner rather than a local regex, including
    a path crafted to smuggle a marker through the file name, because the
    property that matters is "that side finds nothing", not "this side thinks it
    escaped it".
    """
    handle, _ = _elide_a_row_carrying(
        "两份都发了 " + "丙" * 3000 + "[SEND:/workspace/正常.pdf][SEND:/workspace/[SEND:偷渡.pdf].md]"
    )

    scanner = SendMarkerScanner()
    assert scanner.feed(handle) == [], f"the Channel scanner found a marker in a handle: {handle!r}"
    assert list(iter_send_paths(handle)) == [], handle
    # Fed one character at a time, too: the scanner is stateful across deltas, so
    # a marker split by streaming is still a marker.
    split = SendMarkerScanner()
    assert [chunk for char in handle for chunk in split.feed(char)] == [], handle
    # And the note really is present — otherwise this passes by saying nothing.
    assert "已送达" in handle, handle


def test_handles_for_rows_without_send_markers_stay_byte_identical() -> None:
    """No marker, no note, not one extra byte.

    The handle is paid **once per elided row**, so text added here multiplies by
    the row count on exactly the histories that were already too big.  The first
    draft of this module carried a ~220-character explanation and turned into
    tens of kilobytes of un-elidible floor; a run against a tight budget then
    could not get under it no matter how many rows it dropped.  The note is
    therefore conditional, and this pins the unconditional path to the byte.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    result = _assemble(assembler, _history(20))

    handles = [
        m["content"]
        for m in result.body["messages"]
        if isinstance(m.get("content"), str) and m["content"].startswith("[已省略")
    ]
    assert handles
    for handle in handles:
        assert re.fullmatch(
            r"\[已省略 200[34] 字符, 句柄 assistant#\d{6}\]|"
            r"\[已省略 200[34] 字符, 句柄 user#\d{6}\]",
            handle,
        ), handle


def test_all_send_markers_in_one_row_are_named() -> None:
    """One row can deliver several files; naming only the first re-opens the bug.

    A single assistant turn that writes a report and its chart emits two markers
    in one row.  A note that stopped at the first would leave the model believing
    the second file was never sent, which is the same denial-and-re-send loop for
    one file instead of two.
    """
    handle, _ = _elide_a_row_carrying(
        "三份都发了 " + "丙" * 3000 + "[SEND:/w/一.pdf]\n[SEND:/w/二.md]\n[ send: /w/三.txt ]"
    )

    assert "一.pdf" in handle, handle
    assert "二.md" in handle, handle
    # Case and space padding are the Channel's tolerated variants, so they are
    # delivered files too and must be reported as such.
    assert "三.txt" in handle, handle


def test_delivery_note_is_length_capped() -> None:
    """The note is bounded whatever the row did, because the floor must be.

    A row that delivers twenty files, or one file with a pathological name,
    cannot be allowed to set the per-row handle cost — that is the un-elidible
    floor growing with content again, and the budget stops being a guarantee.
    """
    many = "".join(f"[SEND:/w/文件{i:02d}-{'长' * 40}.pdf]" for i in range(20))
    modest = render_sent_files_note("[SEND:/w/a.pdf]")
    extravagant = render_sent_files_note(many)

    # Asserted on the note itself rather than on total handle length: the rest of
    # the handle carries a tool_call_id and a length, both of unbounded width and
    # both pre-existing, so a total-length bound would pass or fail for reasons
    # this card did not introduce.
    # Two bounds, because two different things could regress: the worst case must
    # stay bounded at all (uncapped, these 20 long names run past 900 chars), and
    # the ordinary one-file case — which is what production actually pays on
    # almost every marked row — must stay genuinely cheap.
    assert len(extravagant) <= 96, f"note grew to {len(extravagant)} chars: {extravagant!r}"
    assert len(modest) <= 24, f"a one-file note costs {len(modest)} chars: {modest!r}"
    assert len(modest) < len(extravagant), "sanity: the fixture must exercise the caps"

    # And end to end: bounded, without silently dropping the fact files went out.
    handle, result = _elide_a_row_carrying("全发了 " + "丙" * 3000 + many)
    assert "已送达" in handle, handle
    assert "20" in handle, f"the files past the cap must still be accounted for: {handle!r}"
    assert result.within_budget


def test_delivery_note_is_byte_identical_across_turns() -> None:
    """Hysteresis covers the note as well, or the cache misses every turn.

    ``_reapply_sticky_elisions`` recomputes the handle from the same row on every
    later turn.  A note that varied — iteration order, a count, anything — would
    rewrite an early row's text each turn, and the upstream prefix cache (99.7%
    hit rate measured here) would miss in full, which is the outcome that makes
    eliding worse than not eliding.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    history.insert(1, {"role": "assistant", "content": "发了 " + "丙" * 3000 + "[SEND:/w/甲.pdf][SEND:/w/乙.md]"})

    seen: list[str] = []
    for i in range(4):
        result = _assemble(assembler, history)
        notes = [
            m["content"]
            for m in result.body["messages"]
            if isinstance(m.get("content"), str) and "已送达" in m["content"]
        ]
        assert len(notes) == 1, notes
        seen.append(notes[0])
        history.append({"role": "user", "content": f"再说{i}"})

    assert len(set(seen)) == 1, f"the note changed between turns: {seen}"


def test_handle_carrying_a_delivery_note_is_not_elided_again() -> None:
    """Idempotence has to survive the longer handle.

    The sentinel check is what stops turn N's handle from becoming turn N+1's
    "original content" — which would report an ever-shrinking original length and
    lose the real one.  The note makes handles longer, i.e. closer to
    ``_MIN_ELIDIBLE_CHARS``, so the guard is worth re-asserting with it present.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    row = "发了 " + "丙" * 3000 + "[SEND:/w/甲.pdf]"
    original_chars = len(row)
    history.insert(1, {"role": "assistant", "content": row})

    for i in range(4):
        result = _assemble(assembler, history)
        notes = [
            m["content"]
            for m in result.body["messages"]
            if isinstance(m.get("content"), str) and "已送达" in m["content"]
        ]
        assert len(notes) == 1, notes
        assert notes[0].count("[已省略") == 1, notes[0]
        # The *original* row's length, not a previous handle's: re-eliding a
        # handle would report ~50 chars and lose the real number.
        assert f"{original_chars} 字符" in notes[0], notes[0]
        history.append({"role": "user", "content": f"接着{i}"})


def test_no_watermark_keeps_the_previous_behaviour() -> None:
    """Omitting the watermark elides exactly as before — the default is opt-out.

    ``RequestAssembler`` is constructed in places that do not run turns (tests,
    future callers), so "no watermark given" has to mean "nothing is exempt"
    rather than "everything is".
    """
    history = _history(20)
    with_default = RequestAssembler(max_context_tokens=20_000).build(history, _TOOLS, None)

    ended = RequestAssembler(max_context_tokens=20_000)
    ended.begin_turn(0)
    ended.end_turn()
    after_end = ended.build(history, _TOOLS, None)

    at_end = RequestAssembler(max_context_tokens=20_000)
    at_end.begin_turn(len(history))
    at_end_result = at_end.build(history, _TOOLS, None)

    assert with_default.elided_rows == after_end.elided_rows == at_end_result.elided_rows
    assert with_default.elided_rows > 0


def test_watermark_is_ignored_once_the_turn_is_over() -> None:
    """Exemption is per-turn, so last turn's rows are elidible this turn.

    Otherwise the exempt region would grow without bound and elision — the only
    hard guarantee under the budget — would effectively be switched off.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history = _history(20)
    watermark = len(history)
    # Round 1's assistant row, then tool rows behind it — so by the time the
    # next turn runs, the row sits well inside ``paired[:-2]`` and the only
    # thing that could still spare it is the exemption.
    history.append({"role": "assistant", "content": "上个回合的长输出 " + "戊" * 4000})
    for i in range(4):
        history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "结果 " + "庚" * 2000})

    assembler.begin_turn(watermark)
    during = assembler.build(history, _TOOLS, None)
    assert not during.body["messages"][watermark]["content"].startswith("[已省略")
    assembler.end_turn()

    # Next turn: a new user row arrives and the watermark moves past it, so the
    # rows above are ordinary history again.  It has to be big enough to put the
    # payload back over budget — otherwise sticky re-elision alone keeps turn 2
    # under the ceiling, no fresh elision is attempted at all, and the test would
    # be asserting against hysteresis rather than against the exemption.
    history.append({"role": "user", "content": "接着说 " + "续" * 20_000})
    assembler.begin_turn(len(history))
    after = assembler.build(history, _TOOLS, None)
    assert after.elided_rows > during.elided_rows, "turn 2 must actually run fresh elision"

    assert after.body["messages"][watermark]["content"].startswith("[已省略"), (
        "the previous turn's row stayed exempt after its turn ended"
    )


def test_over_budget_is_still_reported_when_exemption_blocks_the_shrink() -> None:
    """An unshrinkable turn reports ``within_budget=False`` instead of lying.

    The exemption can itself put a turn over budget (one enormous tool result
    produced *this* turn).  That is the accepted trade: the honest report is what
    an operator can act on, and it is the same contract the un-elidible floor
    already has.
    """
    assembler = RequestAssembler(max_context_tokens=2_000)
    history = _history(2)
    watermark = len(history)
    # Produced by this turn, and past the per-row storage cap in aggregate:
    # nothing elidible is left once the exemption is honoured.
    history.append({"role": "assistant", "content": "开工 " + "己" * 4000})
    for i in range(4):
        history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "巨大结果 " + "庚" * 4000})

    assembler.begin_turn(watermark)
    result = assembler.build(history, _TOOLS, None)

    assert result.chars > result.budget_chars, "fixture must be genuinely unshrinkable"
    assert not result.within_budget, "an unshrinkable turn must be reported, not silently accepted"
    assert not result.body["messages"][watermark]["content"].startswith("[已省略")


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
