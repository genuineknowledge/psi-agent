from __future__ import annotations

import os

import anyio
import pytest

from psi_agent.gateway._chat_manager import ChatManager


@pytest.mark.anyio
async def test__save_upload_writes_to_downloads(tmp_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cm = ChatManager()

    path = await cm._save_upload("hello.png", b"payload")

    assert os.path.basename(path) == "hello.png"
    assert str(tmp_path) in path
    assert await anyio.Path(path).read_bytes() == b"payload"


@pytest.mark.anyio
async def test__save_upload_sanitizes_filename(tmp_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cm = ChatManager()

    path = await cm._save_upload("../../evil.txt", b"x")

    assert os.path.basename(path) == "evil.txt"
    assert ".." not in path
    assert await anyio.Path(path).exists()
