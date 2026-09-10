"""Guards against the shape that made ``feishu_attendance_query`` fail to converge.

Production, 2026-09-10, one scheduler session
(``.psi/appdata/histories/scheduler-cedce38a1e5fcfab.jsonl``): the *same* arguments were
sent **800 times**, 754 of them byte-identical, and every response was ``ok: true`` with
identical rows. Split by turn, 78 of 84 turns converged in a single call while 6 blew up
into ``128, 128, 128, 128, 108, 50`` and ended on ``[Max tool rounds reached]``.

The decisive measurement is that **turn 46 and turn 47 had a byte-identical user prompt
and a byte-identical tool response**, yet one converged in 1 call and the other hit the
round cap at 128. So no deterministic bad field explains it. What the response did was
leave the asked-for judgement *underdetermined*: the SOP asked "is the 缺卡 cleared for
8/17-8/19", and the rows stated neither a range-level verdict nor that a re-query is
pointless. Sampling then decided which way a borderline turn fell.

Two things follow, and both are tested here rather than in prose:

* The **model's behaviour is not reproducible** — it is a sampling outcome, so a test that
  asserts "the model stops calling" would be flaky by construction. These tests are
  therefore built on the *observable artifact* instead: the payload the model reads, and
  the repeat-call shape as counted off a transcript. Nothing here runs a model.
* A repeat with identical arguments is now a **detectable event**, so the runaway shape
  itself is asserted against, not just the fields that invite it.

What is deliberately **not** covered: whether the fix actually stops the retries in
production. That needs the model in the loop at temperature, and cannot be established
locally — see the PR body.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from lark_channel.core.model import BaseRequest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
ATTENDANCE_SKILL = SKILLS_DIR / "feishu-attendance" / "SKILL.md"
PAYROLL_SKILL = SKILLS_DIR / "feishu-attendance-payroll" / "SKILL.md"

#: The exact arguments that were repeated 754 times in production.
PROD_ARGS = {
    "user_ids": "159548cf",
    "date_from": "20260817",
    "date_to": "20260819",
    "employee_type": "employee_id",
}

#: The exact rows production returned, byte-for-byte, on every one of those calls. Two
#: ``Lack`` days and one ``Normal`` day, and **no timestamp on any of them** — including
#: the ``Normal`` one, which is the pair that reads as a contradiction.
PROD_RESPONSE: dict[str, Any] = {
    "ok": True,
    "data": {
        "user_task_results": [
            {
                "user_id": "159548cf",
                "employee_name": "周玉洁",
                "day": 20260817,
                "records": [{"check_in_result": "Lack", "check_out_result": "Lack"}],
            },
            {
                "user_id": "159548cf",
                "employee_name": "周玉洁",
                "day": 20260818,
                "records": [{"check_in_result": "Lack", "check_out_result": "Lack"}],
            },
            {
                "user_id": "159548cf",
                "employee_name": "周玉洁",
                "day": 20260819,
                "records": [{"check_in_result": "Normal", "check_out_result": "Normal"}],
            },
        ],
        "invalid_user_ids": [],
        "unauthorized_user_ids": [],
    },
}


class _FixedInvoke:
    """Stand in for ``_invoke``, return one fixed response, and record every request.

    A fixed response is the point: production sent the same bytes back every time, so the
    only thing that can vary across calls here is what the caller does with them.
    """

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[BaseRequest] = []

    async def __call__(self, request: BaseRequest, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(request)
        return self.response


async def _query(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any] | None = None, **overrides: Any) -> Any:
    inv = _FixedInvoke(response if response is not None else PROD_RESPONSE)
    monkeypatch.setattr(_impl, "_invoke", inv)
    args = {**PROD_ARGS, **overrides}
    out = await _impl.query_attendance_impl(
        args["user_ids"], args["date_from"], args["date_to"], args["employee_type"], False
    )
    return out, inv


# ------------------------------------------------------- the repeat-call shape itself


def _identical_call_runs(transcript: list[dict[str, Any]], tool: str) -> Counter[str]:
    """Count, per argument string, how many times ``tool`` was called with it.

    This is the production forensic reduced to a function: read assistant messages off a
    transcript, key each ``tool_calls`` entry by its verbatim ``arguments`` string, and
    count. Keying on the raw string is what makes it byte-identity rather than semantic
    similarity — the 754 production repeats were identical at that level.
    """
    counts: Counter[str] = Counter()
    for message in transcript:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            if function.get("name") == tool:
                counts[function.get("arguments", "")] += 1
    return counts


def _transcript_with_repeats(count: int) -> list[dict[str, Any]]:
    """A transcript in the exact shape the production history used, with ``count`` calls."""
    arguments = json.dumps(PROD_ARGS, ensure_ascii=False, sort_keys=True)
    messages: list[dict[str, Any]] = []
    for index in range(count):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": "feishu_attendance_query", "arguments": arguments},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "content": json.dumps(PROD_RESPONSE)})
    return messages


def test_the_repeat_detector_catches_the_production_runaway() -> None:
    """The detector must fire on the real shape — 128 identical calls in one turn.

    This is the calibration step. If this assertion can pass while the detector is blind,
    the guard below is decoration, so the detector is first pointed at a transcript
    rebuilt to the production dimensions and required to report them exactly.
    """
    runs = _identical_call_runs(_transcript_with_repeats(128), "feishu_attendance_query")
    assert len(runs) == 1, f"one distinct argument string expected, got {len(runs)}"
    assert max(runs.values()) == 128, f"detector under-counted the runaway: {runs}"


def test_a_converged_turn_is_not_flagged() -> None:
    """One call with those arguments is the healthy case and must read as clean.

    Without this, a detector that flags everything would satisfy the test above.
    """
    runs = _identical_call_runs(_transcript_with_repeats(1), "feishu_attendance_query")
    assert max(runs.values()) == 1, f"a single call must not count as a repeat: {runs}"


def test_one_call_answers_the_range_so_a_second_is_never_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool must reach the wire exactly once per invocation, and say so in the payload.

    Two separate claims, because they fail differently: a hidden internal retry would make
    the *tool* the source of duplicate traffic, while a payload that stays silent about
    determinism leaves the *model* free to retry. Production was the second.
    """
    out, inv = pytest.importorskip("anyio").run(lambda: _query(monkeypatch))
    assert len(inv.calls) == 1, f"one query must be one request, got {len(inv.calls)}"
    assert out["retry_will_return_identical_data"] is True, out
    assert out["query_is_complete"] is True, out


def test_the_payload_tells_the_caller_not_to_repeat_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """``next_step`` must name the tool and forbid a repeat, in the text the model reads.

    The instruction has to travel *with the data*. The SKILL.md wording is checked
    separately below, but a skill can be out of context on a scheduler-driven turn while
    the tool response never is — that is the path the production session took.
    """
    out, _ = pytest.importorskip("anyio").run(lambda: _query(monkeypatch))
    step = out["next_step"]
    assert "feishu_attendance_query" in step, step
    assert "again" in step, step
    assert "cannot change" in step, step


# --------------------------------------------- the fields the judgement actually needs


def test_the_range_verdict_is_stated_not_left_to_be_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 8/17-8/19 range must come back with its unsettled days named.

    This is what the SOP asked for and the response did not carry. Both lists are checked:
    naming only the unsettled days would let a range be silently dropped from both.
    """
    out, _ = pytest.importorskip("anyio").run(lambda: _query(monkeypatch))
    assert out["unsettled_days"] == ["20260817", "20260818"], out
    assert out["settled_days"] == ["20260819"], out
    assert out["days_returned"] == ["20260817", "20260818", "20260819"], out


def test_a_clear_range_reports_an_empty_unsettled_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "缺卡 cleared" case — the branch that ends the follow-up task — must be legible.

    The production task was told to delete its own schedule once the range came back
    clear. If a clear range and an unsettled range are not distinguishable in one field,
    that exit condition can never be recognised.
    """
    cleared = json.loads(json.dumps(PROD_RESPONSE))
    for result in cleared["data"]["user_task_results"]:
        result["records"] = [{"check_in_result": "Normal", "check_out_result": "Normal"}]
    out, _ = pytest.importorskip("anyio").run(lambda: _query(monkeypatch, response=cleared))
    assert out["unsettled_days"] == [], out
    assert len(out["settled_days"]) == 3, out
    assert "No unsettled days" in out["next_step"], out


def test_an_unknown_result_code_counts_as_unsettled(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unfamiliar code must not pass as clear.

    The verdict is computed against an allowlist of settled codes rather than a denylist
    of ``Lack``/``Late``/``Early``, so a code nobody enumerated lands on the
    needs-attention side. The opposite default would silently mark a real problem clean.
    """
    odd = json.loads(json.dumps(PROD_RESPONSE))
    odd["data"]["user_task_results"][2]["records"] = [{"check_in_result": "SomeNewCode", "check_out_result": "Normal"}]
    out, _ = pytest.importorskip("anyio").run(lambda: _query(monkeypatch, response=odd))
    assert "20260819" in out["unsettled_days"], out


def test_normal_without_a_timestamp_is_explained_rather_than_left_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Normal`` + empty time is the pair that reads as a contradiction worth re-querying.

    Measured across all production histories: 800 ``Normal`` rows carried no timestamp
    against 12 that did, so the blank is the norm. An empty string states nothing about
    why it is empty; the row now says the response carried no timestamp.
    """
    out, _ = pytest.importorskip("anyio").run(lambda: _query(monkeypatch))
    normal = next(r for r in out["results"] if r["day"] == 20260819)
    assert normal["check_in_result"] == "Normal", normal
    assert normal["check_in_time"] != "", "a bare empty string is the shape that invited the retry"
    assert "no punch timestamp" in normal["check_in_time"], normal


def test_zero_rows_is_reported_as_an_answer_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty range is a fact about the 考勤组, and must not read as a failed lookup.

    ``ok: true`` with an empty list is the most retry-inviting response of all: it looks
    exactly like a lookup that came up short.
    """
    empty = {"ok": True, "data": {"user_task_results": [], "invalid_user_ids": [], "unauthorized_user_ids": []}}
    out, _ = pytest.importorskip("anyio").run(lambda: _query(monkeypatch, response=empty))
    assert out["ok"] is True and out["count"] == 0, out
    assert out["unsettled_days"] == [], out
    assert "not a failure" in out["next_step"], out
    assert "retry" not in out["next_step"].lower() or "identical" in str(out), out


# ------------------------------------------------------------------ the prompt layer


@pytest.mark.parametrize(
    "fact",
    [
        "754",  # the measured repeat count — the number is the reason this section exists
        "retry_will_return_identical_data",
        "unsettled_days",
    ],
)
def test_the_attendance_skill_documents_the_non_convergence(fact: str) -> None:
    """These live only as prose in the domain skill; a rewrite that drops one loses it."""
    assert fact in ATTENDANCE_SKILL.read_text(encoding="utf-8"), f"{fact} no longer documented"


def test_the_payroll_skill_warns_against_repeating_a_chunk() -> None:
    """Payroll chunks rosters into many calls, which is where a repeat hides best.

    With ≤50 ids per call a large roster is already many legitimate calls, so a repeated
    chunk does not stand out the way a repeated single query does.
    """
    prose = PAYROLL_SKILL.read_text(encoding="utf-8")
    assert "retry_will_return_identical_data" in prose, "the determinism flag is undocumented here"
    assert "unsettled_days" in prose, "payroll must be told to use the precomputed verdict"
