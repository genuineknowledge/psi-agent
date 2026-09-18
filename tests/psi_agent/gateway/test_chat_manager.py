from __future__ import annotations

import json
import os
from pathlib import Path

import anyio
import pytest

from psi_agent._appdata import appdata_uploads_path
from psi_agent.runtime._chat_manager import ChatManager


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` to *tmp_path*.

    ``_downloads_path`` resolves the home directory through ``Path.home()``,
    which reads ``USERPROFILE`` on Windows and ``HOME`` elsewhere - patching
    only ``HOME`` left these tests writing into the developer's real Downloads
    folder (and failing the location assertion on Windows).
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.mark.anyio
async def test__save_upload_writes_to_downloads(fake_home: Path) -> None:
    cm = ChatManager()

    path = await cm._save_upload("hello.png", b"payload")

    assert os.path.basename(path) == "hello.png"
    assert str(fake_home) in path
    assert await anyio.Path(path).read_bytes() == b"payload"


@pytest.mark.anyio
async def test__save_upload_sanitizes_filename(fake_home: Path) -> None:
    cm = ChatManager()

    path = await cm._save_upload("../../evil.txt", b"x")

    assert os.path.basename(path) == "evil.txt"
    assert ".." not in path
    assert str(fake_home) in path
    assert await anyio.Path(path).exists()


@pytest.mark.anyio
async def test__record_upload_writes_the_session_ledger(tmp_path: Path) -> None:
    """入向文件要**在落盘那一刻**被登记 —— 下载路由的白名单只认服务端写下的记录。

    它与 ``chat.handle()`` 里 ``_save_upload`` 之后那一行成对: 少了登记, 用户上传的附件在
    刷新之后就下不动了; 而白名单能由别的东西 (用户正文) 拼出来, 那条路由就没有闸门。
    """
    cm = ChatManager()
    appdata = str(tmp_path / "appdata")

    await cm._record_upload("s1", appdata, str(tmp_path / "a.pdf"))

    ledger = appdata_uploads_path(appdata, "s1")
    assert json.loads((await anyio.Path(ledger).read_text(encoding="utf-8")).strip()) == {
        "path": str(tmp_path / "a.pdf")
    }
    # 追加而不是覆盖: 一个会话可以收下多个文件。
    await cm._record_upload("s1", appdata, str(tmp_path / "b.pdf"))
    assert len((await anyio.Path(ledger).read_text(encoding="utf-8")).splitlines()) == 2
    # 按会话分文件: 别人的登记簿里不该出现这条路径。
    assert not await anyio.Path(appdata_uploads_path(appdata, "s2")).exists()


@pytest.mark.anyio
async def test__record_upload_without_session_or_appdata_is_a_noop(tmp_path: Path) -> None:
    """没有会话 / 没有 AppData 时不写、也不抛 —— 登记是尽力而为, 不该拖垮一次对话。"""
    cm = ChatManager()

    await cm._record_upload("", str(tmp_path), str(tmp_path / "a.pdf"))
    await cm._record_upload("s1", "", str(tmp_path / "a.pdf"))

    assert [str(p) async for p in anyio.Path(tmp_path).rglob("*.jsonl")] == []
