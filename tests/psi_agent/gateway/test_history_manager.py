from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from psi_agent.gateway._history_manager import HistoryManager


@pytest.fixture
def appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "appdata"
    monkeypatch.setenv("PSI_APPDATA", str(root))
    return root


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
        {"role": "assistant", "text": "\u65e5\u62a5", "kind": "schedule.display"},
        {"role": "user", "text": "\u770b\u56fe"},
        {"role": "assistant", "text": "\u597d", "sends": ["/ws/out.md", "/ws/only.html"]},
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
