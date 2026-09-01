from __future__ import annotations

import os
from pathlib import Path

import anyio
import pytest

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
