from __future__ import annotations

import sys

import anyio
import pytest
from anyio.abc import TaskGroup

from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._manager import _socket_path, _wait_socket
from psi_agent.runtime._session_manager import SessionManager


def _is_socket_path(path: str) -> bool:
    """Whether *path* looks like the transport the current platform uses.

    ``_socket_path`` yields a ``.sock`` file on POSIX but a ``\\\\.\\pipe\\...``
    Named Pipe on Windows, so asserting on ``.sock`` alone passes in CI (Linux
    only) while failing on every Windows dev machine.
    """
    if sys.platform == "win32":
        return path.startswith("\\\\.\\pipe\\")
    return path.endswith(".sock")


async def _close(tg: TaskGroup) -> None:
    """Cancel the services started under *tg*, then exit the group.

    The managers spawn long-lived server tasks that never return on their own,
    so exiting the group normally would wait for them forever. Cancelling first
    also keeps a failed assertion in the test body a plain failure instead of
    turning it into a hang.
    """
    tg.cancel_scope.cancel()
    await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_aimanager_create_list_delete(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)

        info = await mgr.create(
            provider="openai", model="gpt-4o", api_key="sk-test", base_url="https://api.example.com"
        )
        assert info.provider == "openai"
        assert info.model == "gpt-4o"
        assert _is_socket_path(info.socket)

        items = await mgr.list_all()
        assert len(items) == 1
        assert items[0].id == info.id

        await mgr.delete(info.id)

        items = await mgr.list_all()
        assert len(items) == 0
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_aimanager_delete_nonexistent(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        with pytest.raises(LookupError, match="not found"):
            await mgr.delete("no-such-id")
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_aimanager_duplicate_id(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        info = await mgr.create(provider="o", model="m", api_key="k", base_url="b", id="dup")
        with pytest.raises(ValueError, match="already exists"):
            await mgr.create(provider="o", model="m", api_key="k", base_url="b", id="dup")
        await mgr.delete(info.id)
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_aimanager_has_and_get_socket(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        info = await mgr.create(provider="o", model="m", api_key="k", base_url="b")
        assert mgr.has(info.id)
        assert not mgr.has("nonexistent")
        assert mgr.get_socket(info.id) == info.socket
        socket = mgr.get_socket("nonexistent")
        assert _is_socket_path(socket)
        await mgr.delete(info.id)
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_aimanager_auto_uuid(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        info = await mgr.create(provider="o", model="m", api_key="k", base_url="b")
        assert len(info.id) == 32
        await mgr.delete(info.id)
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_sessionmanager_create_delete(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)

        await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")

        info = await sm.create(ai_id="ai1", workspace=str(tmp_path))
        assert info.ai_id == "ai1"
        assert _is_socket_path(info.channel_socket)

        items = await sm.list_all()
        assert len(items) == 1

        await sm.delete(info.id)
        items = await sm.list_all()
        assert len(items) == 0

        await am.delete("ai1")
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_sessionmanager_create_without_ai(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)
        info = await sm.create(ai_id="no-such-ai", workspace=str(tmp_path), id="s1")
        assert info.ai_id == "no-such-ai"
        await sm.delete("s1")
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_sessionmanager_duplicate_id(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)

        await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")

        info = await sm.create(ai_id="ai1", workspace=str(tmp_path), id="dup")
        with pytest.raises(ValueError, match="already exists"):
            await sm.create(ai_id="ai1", workspace=str(tmp_path), id="dup")
        await sm.delete(info.id)
        await am.delete("ai1")
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_sessionmanager_has_and_get_socket(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)

        await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")

        info = await sm.create(ai_id="ai1", workspace=str(tmp_path))
        assert sm.has(info.id)
        assert not sm.has("nonexistent")
        assert sm.get_socket(info.id) == info.channel_socket
        with pytest.raises(LookupError):
            sm.get_socket("nonexistent")
        await sm.delete(info.id)
        await am.delete("ai1")
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_aimanager_delete_removes_socket_file(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        info = await mgr.create(provider="o", model="m", api_key="k", base_url="b")
        assert await anyio.Path(info.socket).exists()
        await mgr.delete(info.id)
        assert not await anyio.Path(info.socket).exists()
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_aimanager_recreate_same_id_after_delete(tmp_path: str) -> None:
    # Regression (A1): delete must remove the socket file so the same id can
    # be recreated without hitting EADDRINUSE on the leftover socket.
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        await mgr.create(provider="o", model="m", api_key="k", base_url="b", id="reuse")
        await mgr.delete("reuse")
        info = await mgr.create(provider="o", model="m", api_key="k", base_url="b", id="reuse")
        assert info.id == "reuse"
        await mgr.delete("reuse")
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_sessionmanager_delete_removes_socket_file(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)
        await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")
        info = await sm.create(ai_id="ai1", workspace=str(tmp_path))
        assert await anyio.Path(info.channel_socket).exists()
        await sm.delete(info.id)
        assert not await anyio.Path(info.channel_socket).exists()
        await am.delete("ai1")
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_aimanager_rollback_when_wait_socket_fails(tmp_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # G: if the service never becomes ready, create() must roll back the entry
    # and cancel the task instead of leaving a zombie registration.
    async def _never_ready(*args: object, **kwargs: object) -> None:
        raise TimeoutError("not ready")

    monkeypatch.setattr("psi_agent.runtime._ai_manager._wait_socket", _never_ready)

    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        with pytest.raises(TimeoutError):
            await mgr.create(provider="o", model="m", api_key="k", base_url="b", id="rollback")
        assert not mgr.has("rollback")
        assert await mgr.list_all() == []
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_wait_socket_times_out_on_dead_socket() -> None:
    # Regression: _wait_socket used to retry forever, so a service that never
    # came up hung its caller instead of letting create() roll back.
    path = _socket_path("gw-test-dead", "ais", "never-listens")
    with anyio.fail_after(10):
        with pytest.raises(TimeoutError, match="not ready within"):
            await _wait_socket(path, timeout_sec=0.3)


@pytest.mark.anyio
async def test_aimanager_persist_called_on_create_delete(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    call_count = 0

    async def fake_persist() -> None:
        nonlocal call_count
        call_count += 1

    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg, _persist=fake_persist)
        info = await mgr.create(provider="o", model="m", api_key="k", base_url="b")
        assert call_count == 1
        await mgr.delete(info.id)
        assert call_count == 2
    finally:
        await _close(tg)


@pytest.mark.anyio
async def test_sessionmanager_persist_called_on_create_delete(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    call_count = 0

    async def fake_persist() -> None:
        nonlocal call_count
        call_count += 1

    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg, _persist=fake_persist)
        info = await sm.create(ai_id="ai1", workspace=str(tmp_path))
        assert call_count == 1
        await sm.delete(info.id)
        assert call_count == 2
    finally:
        await _close(tg)
