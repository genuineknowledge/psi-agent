from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._session_manager import SessionManager


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
        assert info.socket.endswith(".sock")

        items = await mgr.list_all()
        assert len(items) == 1
        assert items[0].id == info.id

        await mgr.delete(info.id)

        items = await mgr.list_all()
        assert len(items) == 0
    finally:
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_aimanager_delete_nonexistent(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        with pytest.raises(LookupError, match="not found"):
            await mgr.delete("no-such-id")
    finally:
        await tg.__aexit__(None, None, None)


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
        await tg.__aexit__(None, None, None)


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
        with pytest.raises(LookupError):
            mgr.get_socket("nonexistent")
        await mgr.delete(info.id)
    finally:
        await tg.__aexit__(None, None, None)


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
        await tg.__aexit__(None, None, None)


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
        assert info.channel_socket.endswith(".sock")

        items = await sm.list_all()
        assert len(items) == 1

        await sm.delete(info.id)
        items = await sm.list_all()
        assert len(items) == 0

        await am.delete("ai1")
    finally:
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_sessionmanager_missing_ai(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)
        with pytest.raises(LookupError, match="not found"):
            await sm.create(ai_id="no-such-ai", workspace=str(tmp_path))
    finally:
        await tg.__aexit__(None, None, None)


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
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_sessionmanager_has_and_get_channel_socket(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)

        await am.create(provider="o", model="m", api_key="k", base_url="b", id="ai1")

        info = await sm.create(ai_id="ai1", workspace=str(tmp_path))
        assert sm.has(info.id)
        assert not sm.has("nonexistent")
        assert sm.get_channel_socket(info.id) == info.channel_socket
        with pytest.raises(LookupError):
            sm.get_channel_socket("nonexistent")
        await sm.delete(info.id)
        await am.delete("ai1")
    finally:
        await tg.__aexit__(None, None, None)


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
        await tg.__aexit__(None, None, None)


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
        await tg.__aexit__(None, None, None)


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
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_aimanager_rollback_when_wait_socket_fails(tmp_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # G: if the service never becomes ready, create() must roll back the entry
    # and cancel the task instead of leaving a zombie registration.
    async def _never_ready(*args: object, **kwargs: object) -> None:
        raise TimeoutError("not ready")

    monkeypatch.setattr("psi_agent.gateway._ai_manager._wait_socket", _never_ready)

    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        with pytest.raises(TimeoutError):
            await mgr.create(provider="o", model="m", api_key="k", base_url="b", id="rollback")
        assert not mgr.has("rollback")
        assert await mgr.list_all() == []
    finally:
        await tg.__aexit__(None, None, None)
