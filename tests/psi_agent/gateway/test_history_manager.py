from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from psi_agent.runtime._history_manager import HistoryManager
from psi_agent.session.history_display import ELISION_HANDLE_PREFIX
from psi_agent.session.request_assembly import RequestAssembler


@pytest.fixture
def appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(root))
    return root


async def _project(
    hm: HistoryManager,
    tmp_path: Path,
    appdata: Path,
    session_id: str,
    lines: list[str],
) -> list[dict[str, object]]:
    """Write JSONL rows and return what ``GET /history`` would serialise."""
    hist_dir = anyio.Path(str(appdata)) / "histories"
    await hist_dir.mkdir(parents=True, exist_ok=True)
    await (hist_dir / f"{session_id}.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return await hm.get(str(tmp_path / "ws"), session_id, appdata=str(appdata))


@pytest.mark.anyio
async def test_history_missing_file_returns_empty(tmp_path: Path, appdata: Path) -> None:
    hm = HistoryManager()
    assert await hm.get(str(tmp_path / "ws"), "nope", appdata=str(appdata)) == []


@pytest.mark.anyio
async def test_history_filters_roles_kind_and_markers(tmp_path: Path, appdata: Path) -> None:
    hm = HistoryManager()
    hist_dir = anyio.Path(str(appdata)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "system", "content": "sys"}',
            '{"role": "user", "content": "hi", "kind": "chat"}',
            '{"role": "assistant", "content": "\u4f60\u597d", "kind": "chat"}',
            '{"role": "assistant", "content": "\u6709\u601d\u8003", "kind": "chat", "reasoning": "\u5148\u5206\u6790"}',
            '{"role": "user", "content": "# Heartbeat Task", "kind": "schedule.silent"}',
            '{"role": "assistant", "content": "HEARTBEAT_OK", "kind": "schedule.silent"}',
            '{"role": "assistant", "content": "\u65e5\u62a5", "kind": "schedule.display"}',
            '{"role": "user", "content": "\u770b\u56fe\\n[RECV:/tmp/a.png]", "kind": "chat"}',
            '{"role": "assistant", "content": "\u597d\\n[SEND:/ws/out.md]", "kind": "chat"}',
            '{"role": "assistant", "content": "[SEND:/ws/only.html]", "kind": "chat"}',
            '{"role": "tool", "content": "ignored"}',
            "not json",
            '{"role": "assistant", "content": ["multimodal"]}',
            '{"role": "assistant"}',
            '{"role": "assistant", "content": "HEARTBEAT_OK"}',
            "",
        ]
    )
    await (hist_dir / "s1.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path / "ws"), "s1", appdata=str(appdata))

    assert result == [
        {"role": "user", "text": "hi"},
        {"role": "assistant", "text": "\u4f60\u597d"},
        {"role": "assistant", "text": "\u6709\u601d\u8003", "reasoning": "\u5148\u5206\u6790"},
        {"role": "assistant", "text": "\u65e5\u62a5", "kind": "schedule.display"},
        {"role": "user", "text": "\u770b\u56fe"},
        {"role": "assistant", "text": "\u597d", "sends": ["/ws/out.md", "/ws/only.html"]},
    ]


@pytest.mark.anyio
async def test_history_strips_elision_handles_from_user_visible_text(
    tmp_path: Path,
    appdata: Path,
) -> None:
    """Elision handles are internal placeholders; ``/history`` must not show them.

    Measured in production: four assistant rows carried
    ``[已省略 1072 字符, 句柄 assistant#466000]`` into the user-facing transcript.
    The handle exists so the *model* knows content was dropped and can go fetch
    it — see ``ELISION_HANDLE_TEMPLATE``.  A user reading the chat has no such
    affordance, so for them it is noise that looks like a bug.

    Asserted at this layer on purpose: ``HistoryManager.get`` is what
    ``GET /sessions/{id}/history`` serialises verbatim, so this is the text the
    user actually sees.  Testing the Session-layer strip helper instead would
    still pass if this projection stopped calling it.

    This is the production shape: handle at end of line.  Mid-line and
    several-per-line are separate cases below rather than extra rows here — a
    single whole-list assertion fails on its first differing index, so the later
    positions would ride along without ever being load-bearing.
    """
    hm = HistoryManager()
    result = await _project(
        hm,
        tmp_path,
        appdata,
        "elide-tail",
        ['{"role": "assistant", "content": "看这个 [已省略 1072 字符, 句柄 assistant#466000]", "kind": "chat"}'],
    )

    assert result == [{"role": "assistant", "text": "看这个"}]


@pytest.mark.anyio
async def test_history_strips_elision_handle_in_mid_line(tmp_path: Path, appdata: Path) -> None:
    """A handle between two sentences leaves the surrounding text joined by one space."""
    hm = HistoryManager()
    result = await _project(
        hm,
        tmp_path,
        appdata,
        "elide-mid",
        ['{"role": "user", "content": "前文 [已省略 300 字符, 句柄 call_9] 后文", "kind": "chat"}'],
    )

    assert result == [{"role": "user", "text": "前文 后文"}]


@pytest.mark.anyio
async def test_history_strips_multiple_elision_handles_in_one_line(tmp_path: Path, appdata: Path) -> None:
    """Several handles on one line all go, including the ``(name)`` label variant.

    A non-global substitution, or a regex anchored to the start or end of the
    string, passes both single-handle cases above and fails here.
    """
    hm = HistoryManager()
    result = await _project(
        hm,
        tmp_path,
        appdata,
        "elide-many",
        [
            '{"role": "assistant", "content": "第一 [已省略 12 字符 (read), 句柄 call_1] '
            '第二 [已省略 34 字符, 句柄 call_2] 第三", "kind": "chat"}'
        ],
    )

    assert result == [{"role": "assistant", "text": "第一 第二 第三"}]


@pytest.mark.anyio
async def test_history_strip_preserves_code_block_indentation(tmp_path: Path, appdata: Path) -> None:
    """Removing a handle must not disturb indentation elsewhere in the message.

    The obvious way to fix the double space a mid-sentence handle leaves behind
    is to collapse every run of horizontal whitespace afterwards.  That also
    flattens the indentation of any fenced code block in the same message —
    corrupting text the user *is* meant to read in order to tidy text they are
    not.  Hence the leading ``[ \\t]*`` on the pattern itself.
    """
    hm = HistoryManager()
    text = "改完了:\n\n```python\ndef f():\n    return 1\n```\n\n[已省略 900 字符, 句柄 call_7]"
    result = await _project(
        hm,
        tmp_path,
        appdata,
        "elide-code",
        [json.dumps({"role": "assistant", "content": text, "kind": "chat"}, ensure_ascii=False)],
    )

    assert result == [{"role": "assistant", "text": "改完了:\n\n```python\ndef f():\n    return 1\n```"}]


def test_request_side_keeps_elision_handles() -> None:
    """The reverse direction: handles are *legitimate* on the way to the model.

    Guarding against over-correction.  Stripping the handle from the request
    turns a recoverable elision into a silent deletion: the model would answer
    from a history it believes complete, with no sign that a row was dropped and
    no key to fetch it — the exact trap ``ELISION_HANDLE_TEMPLATE`` and
    ``truncate_tool_result`` both exist to avoid.

    Same layer rule as above: this asserts on the assembled request body, which
    is what the AI backend receives.
    """
    assembler = RequestAssembler(max_context_tokens=20_000)
    history: list[dict[str, object]] = [{"role": "system", "content": "You are an agent."}]
    for i in range(20):
        history.append({"role": "user", "content": f"q{i} " + "问" * 2000})
        history.append({"role": "assistant", "content": f"a{i} " + "答" * 2000})

    result = assembler.build(history, [], {"routing": {"session_id": "s1"}})

    assert result.elided_rows > 0, "fixture must actually elide something"
    handles = [
        m["content"]
        for m in result.body["messages"]
        if isinstance(m.get("content"), str) and ELISION_HANDLE_PREFIX in m["content"]
    ]
    assert len(handles) == result.elided_rows
    # And the handle is intact, not a stripped husk: both facts the model needs.
    for handle in handles:
        assert "字符" in handle, handle
        assert "句柄" in handle, handle


@pytest.mark.anyio
async def test_history_folds_tool_round_reasoning_into_next_assistant(
    tmp_path: Path,
    appdata: Path,
) -> None:
    """Empty-content tool_calls rows are not bubbles; thinking + tools survive as separate fields."""
    hm = HistoryManager()
    hist_dir = anyio.Path(str(appdata)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "user", "content": "\u505a\u4efb\u52a1", "kind": "chat"}',
            (
                '{"role": "assistant", "tool_calls": [{"id": "1", "type": "function", '
                '"function": {"name": "read", "arguments": "{\\"path\\": \\"a.py\\"}"}}], '
                '"reasoning": "\u5148\u8bfb\u6587\u4ef6", "kind": "chat"}'
            ),
            '{"role": "tool", "content": "ok", "tool_call_id": "1"}',
            '{"role": "assistant", "content": "\u5b8c\u6210", "reasoning": "\u518d\u603b\u7ed3", "kind": "chat"}',
        ]
    )
    await (hist_dir / "fold.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path / "ws"), "fold", appdata=str(appdata))

    assert result == [
        {"role": "user", "text": "\u505a\u4efb\u52a1"},
        {
            "role": "assistant",
            "text": "\u5b8c\u6210",
            "reasoning": "\u5148\u8bfb\u6587\u4ef6\n\u518d\u603b\u7ed3",
            "tools": [{"name": "read", "arguments": '{"path": "a.py"}'}],
        },
    ]


@pytest.mark.anyio
async def test_history_folds_tool_calls_without_reasoning(
    tmp_path: Path,
    appdata: Path,
) -> None:
    """Structured tool_calls alone project onto ``tools`` (not into ``reasoning``)."""
    hm = HistoryManager()
    hist_dir = anyio.Path(str(appdata)) / "histories"
    await hist_dir.mkdir(parents=True)
    content = "\n".join(
        [
            '{"role": "user", "content": "go", "kind": "chat"}',
            (
                '{"role": "assistant", "tool_calls": [{"id": "1", "type": "function", '
                '"function": {"name": "bash", "arguments": "{\\"command\\": \\"ls\\"}"}}], '
                '"kind": "chat"}'
            ),
            '{"role": "tool", "content": "ok", "tool_call_id": "1"}',
            '{"role": "assistant", "content": "done", "kind": "chat"}',
        ]
    )
    await (hist_dir / "tools-only.jsonl").write_text(content, encoding="utf-8")

    result = await hm.get(str(tmp_path / "ws"), "tools-only", appdata=str(appdata))

    assert result == [
        {"role": "user", "text": "go"},
        {
            "role": "assistant",
            "text": "done",
            "tools": [{"name": "bash", "arguments": '{"command": "ls"}'}],
        },
    ]


@pytest.mark.anyio
async def test_history_reads_legacy_workspace_file(tmp_path: Path, appdata: Path) -> None:
    hm = HistoryManager()
    await anyio.Path(str(appdata)).mkdir(parents=True)
    ws = tmp_path / "ws"
    hist_dir = anyio.Path(str(ws)) / "histories"
    await hist_dir.mkdir(parents=True)
    await (hist_dir / "legacy.jsonl").write_text(
        '{"role": "user", "content": "from-legacy", "kind": "chat"}\n',
        encoding="utf-8",
    )
    result = await hm.get(str(ws), "legacy", appdata=str(appdata))
    assert result == [{"role": "user", "text": "from-legacy"}]


@pytest.mark.anyio
async def test_history_appdata_wins_over_legacy(tmp_path: Path, appdata: Path) -> None:
    hm = HistoryManager()
    ws = tmp_path / "ws"
    legacy = anyio.Path(str(ws)) / "histories"
    await legacy.mkdir(parents=True)
    await (legacy / "s1.jsonl").write_text(
        '{"role": "user", "content": "legacy", "kind": "chat"}\n',
        encoding="utf-8",
    )
    primary = anyio.Path(str(appdata)) / "histories"
    await primary.mkdir(parents=True)
    await (primary / "s1.jsonl").write_text(
        '{"role": "user", "content": "appdata", "kind": "chat"}\n',
        encoding="utf-8",
    )
    result = await hm.get(str(ws), "s1", appdata=str(appdata))
    assert result == [{"role": "user", "text": "appdata"}]


@pytest.mark.anyio
async def test_history_delete_removes_appdata_and_legacy(tmp_path: Path, appdata: Path) -> None:
    hm = HistoryManager()
    ws = tmp_path / "ws"
    app_path = anyio.Path(str(appdata)) / "histories" / "s-del.jsonl"
    legacy_path = anyio.Path(str(ws)) / "histories" / "s-del.jsonl"
    await app_path.parent.mkdir(parents=True)
    await legacy_path.parent.mkdir(parents=True)
    await app_path.write_text('{"role": "user", "content": "a", "kind": "chat"}\n', encoding="utf-8")
    await legacy_path.write_text('{"role": "user", "content": "b", "kind": "chat"}\n', encoding="utf-8")
    await hm.delete(str(ws), "s-del", appdata=str(appdata))
    assert not await app_path.exists()
    assert not await legacy_path.exists()
    await hm.delete(str(ws), "s-del", appdata=str(appdata))
