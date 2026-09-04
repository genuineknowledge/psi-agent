"""Tests for ``history_recall`` — pulling an elided row's original text back.

Elision leaves a handle that "names where the original still lives"
(``request_assembly``), so recall is the missing half of that promise. These
tests pin the four things that decide whether the tool is safe to hand a model:
the handle is untrusted input (traversal), the payload has a ceiling (recalling
a 3MB row would refund the whole budget elision just saved), the
``role#ordinal`` handle form is *not* resolvable from disk and must say so, and
the ``tool_call_id`` form must actually work.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from psi_agent.session.history_display import ELISION_HANDLE_TEMPLATE, MAX_TOOL_RESULT_CHARS
from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

recall: Any = importlib.import_module("history_recall")

DECOY_MARK = "TOP_SECRET_DECOY_PAYLOAD"


def _write_history(root: Path, session_id: str, rows: list[dict[str, Any]]) -> Path:
    histories = root / "histories"
    histories.mkdir(parents=True, exist_ok=True)
    path = histories / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _plant_decoy(root: Path, name: str = "decoy") -> Path:
    """A readable, well-formed history file *outside* the session's histories dir."""
    path = root / f"{name}.jsonl"
    path.write_text(
        json.dumps({"role": "tool", "tool_call_id": "call_decoy", "content": DECOY_MARK}) + "\n",
        encoding="utf-8",
    )
    return path


async def _call(**kwargs: Any) -> dict[str, Any]:
    raw = await recall.history_recall(**kwargs)
    assert isinstance(raw, str)
    return json.loads(raw)


def test_tool_metadata_exposes_handle_as_the_required_argument() -> None:
    meta = ToolFunction.from_callable(recall.history_recall)
    assert meta.name == "history_recall"
    assert meta.parameters["required"] == ["handle"]


@pytest.mark.anyio
async def test_recalls_original_text_for_a_tool_call_id_handle(tmp_path: Path) -> None:
    original = "第 1 页\n" + "x" * 500
    _write_history(
        tmp_path,
        "sess-a",
        [
            {"role": "user", "content": "查一下"},
            {"role": "tool", "tool_call_id": "call_abc123", "name": "feishu_api", "content": original},
        ],
    )

    payload = await _call(handle="call_abc123", session_id="sess-a", workspace=str(tmp_path))

    assert payload["ok"] is True
    assert payload["content"] == original
    assert payload["chars"] == len(original)
    assert payload["truncated"] is False
    assert payload["role"] == "tool"
    assert payload["name"] == "feishu_api"


@pytest.mark.anyio
async def test_accepts_the_whole_handle_text_pasted_from_the_transcript(tmp_path: Path) -> None:
    """The model sees the rendered ``[已省略 … 句柄 X]``, not a bare id.

    Parsing is derived from ``ELISION_HANDLE_TEMPLATE`` rather than a retyped
    literal, so this test builds its input from that template too — a drift in
    the format has to break both sides at once or neither.
    """
    original = "原文正文" * 20
    _write_history(
        tmp_path,
        "sess-b",
        [{"role": "tool", "tool_call_id": "call_xyz789", "name": "read", "content": original}],
    )
    rendered = ELISION_HANDLE_TEMPLATE.format(
        kind="tool",
        chars=len(original),
        label=" (read)",
        sent="",
        handle="call_xyz789",
    )

    payload = await _call(handle=rendered, session_id="sess-b", workspace=str(tmp_path))

    assert payload["ok"] is True
    assert payload["content"] == original


@pytest.mark.anyio
async def test_role_ordinal_handle_reports_that_it_cannot_be_resolved(tmp_path: Path) -> None:
    """``assistant#466000`` is ``id(row) % 1_000_000`` — an in-memory address.

    Nothing on disk carries it, so no amount of searching the JSONL can match
    it. The tool must say that plainly instead of returning an empty result
    (reads as "the row was empty") or guessing a row by role.
    """
    _write_history(
        tmp_path,
        "sess-c",
        [
            {"role": "assistant", "content": "第一段回答"},
            {"role": "assistant", "content": "第二段回答"},
        ],
    )

    payload = await _call(handle="assistant#466000", session_id="sess-c", workspace=str(tmp_path))

    assert payload["ok"] is False
    assert payload["error"] == "handle_not_on_disk"
    # Must not guess a row: neither candidate may be handed back as the answer.
    assert "第一段回答" not in payload["reason"]
    assert "content" not in payload
    # The reason has to name the actual limitation, not a generic "not found".
    assert "role#ordinal" in payload["reason"]


@pytest.mark.anyio
async def test_traversal_handle_is_refused_before_any_lookup(tmp_path: Path) -> None:
    """A handle carrying ``..`` is refused as unsafe, not searched-and-missed.

    The distinction is the whole test: dropping the guard would leave the
    traversal falling through to an ordinary "handle not found", which reads
    identical from the caller's side. Pinning the *error code* is what makes
    the guard load-bearing.
    """
    _write_history(tmp_path, "sess-d", [{"role": "tool", "tool_call_id": "call_ok", "content": "fine"}])
    _plant_decoy(tmp_path)

    payload = await _call(handle="../../decoy", session_id="sess-d", workspace=str(tmp_path))

    assert payload["ok"] is False
    assert payload["error"] == "unsafe_handle"
    assert DECOY_MARK not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.anyio
async def test_absolute_path_handle_is_refused(tmp_path: Path) -> None:
    decoy = _plant_decoy(tmp_path, "abs-decoy")
    _write_history(tmp_path, "sess-e", [{"role": "tool", "tool_call_id": "call_ok", "content": "fine"}])

    payload = await _call(handle=str(decoy), session_id="sess-e", workspace=str(tmp_path))

    assert payload["ok"] is False
    assert payload["error"] == "unsafe_handle"
    assert DECOY_MARK not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.anyio
async def test_missing_handle_is_reported_as_not_found_not_as_unsafe(tmp_path: Path) -> None:
    """Keeps the traversal test honest: the two outcomes must not share a code."""
    _write_history(tmp_path, "sess-f", [{"role": "tool", "tool_call_id": "call_ok", "content": "fine"}])

    payload = await _call(handle="call_missing", session_id="sess-f", workspace=str(tmp_path))

    assert payload["ok"] is False
    assert payload["error"] == "handle_not_found"


@pytest.mark.anyio
async def test_session_scope_traversal_cannot_read_a_history_outside_the_dir(tmp_path: Path) -> None:
    """``session_id`` is model-supplied too, and it *is* a path component.

    Without the guard this resolves to ``{workspace}/histories/../decoy.jsonl``
    — a real, parseable file whose row the tool would happily return.
    """
    _plant_decoy(tmp_path)

    payload = await _call(handle="call_decoy", session_id="../decoy", workspace=str(tmp_path))

    assert payload["ok"] is False
    assert payload["error"] == "unsafe_session_id"
    assert DECOY_MARK not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.anyio
async def test_resolved_history_path_outside_the_histories_roots_is_refused() -> None:
    """Containment check at its own layer.

    The charset guard on ``session_id`` fires first for every input a model can
    send, so this is the only place the resolve-then-contain check is exercised
    directly (defence in depth against a future caller that skips the charset
    guard, and against symlinks, which ``resolve()`` expands).
    """
    with pytest.raises(recall.RecallRefusedError) as excinfo:
        await recall._ensure_within_histories(Path("/etc/passwd"), roots=[Path("/tmp/appdata/histories")])
    assert excinfo.value.code == "unsafe_history_path"


@pytest.mark.anyio
async def test_containment_resolves_dot_dot_before_comparing(tmp_path: Path) -> None:
    """A path that is a string prefix of the root but escapes it via ``..``.

    Honest note on what this does *not* pin: replacing ``resolve()`` with a
    no-op keeps this green, because ``_norm``'s ``normpath`` already collapses
    ``..`` textually — so for ``..`` the two are genuinely equivalent.
    ``resolve()`` is load-bearing only for **symlinks**, which ``normpath``
    cannot see through; that case is unverified here because creating a symlink
    on this Windows host requires privileges the test run does not have
    (``WinError 3``). Keep the ``resolve()`` call regardless: the symlink hole is
    real even though no criterion here closes it.
    """
    root = tmp_path / "histories"
    root.mkdir()
    (tmp_path / "outside.jsonl").write_text("{}\n", encoding="utf-8")
    escaping = root / ".." / "outside.jsonl"

    with pytest.raises(recall.RecallRefusedError) as excinfo:
        await recall._ensure_within_histories(escaping, roots=[root])
    assert excinfo.value.code == "unsafe_history_path"

    # Control: a real file *inside* the root still passes, so the check is not
    # simply rejecting everything.
    inside = root / "sess.jsonl"
    inside.write_text("{}\n", encoding="utf-8")
    assert await recall._ensure_within_histories(inside, roots=[root]) == inside.resolve()


@pytest.mark.anyio
async def test_recalled_content_is_capped_at_max_tool_result_chars(tmp_path: Path) -> None:
    """Recall must not refund the budget elision just saved.

    The cap is ``MAX_TOOL_RESULT_CHARS`` (the constant defined for the 2.34M
    character ``feishu_api`` row), imported rather than retyped.
    """
    original = "长" * (MAX_TOOL_RESULT_CHARS + 5_000)
    _write_history(
        tmp_path,
        "sess-g",
        [{"role": "tool", "tool_call_id": "call_big", "name": "feishu_api", "content": original}],
    )

    payload = await _call(handle="call_big", session_id="sess-g", workspace=str(tmp_path))

    assert payload["ok"] is True
    assert payload["truncated"] is True
    assert payload["chars"] == len(original)
    assert len(payload["content"]) <= MAX_TOOL_RESULT_CHARS
    # The prefix has to be the real head of the row, not a placeholder.
    assert payload["content"].startswith("长长长")


@pytest.mark.anyio
async def test_missing_history_file_is_reported(tmp_path: Path) -> None:
    payload = await _call(handle="call_ok", session_id="sess-none", workspace=str(tmp_path))

    assert payload["ok"] is False
    assert payload["error"] == "no_history"
