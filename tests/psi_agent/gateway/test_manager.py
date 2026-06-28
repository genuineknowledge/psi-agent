from __future__ import annotations

import anyio
import pytest

from psi_agent.gateway._manager import AiCreateRequest, AIManager, SessionCreateRequest, SessionManager


@pytest.mark.anyio
async def test_aimanager_create_list_delete(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)

        req = AiCreateRequest(provider="openai", model="gpt-4o", api_key="sk-test", base_url="https://api.example.com")
        info = await mgr.create(req)
        assert info.provider == "openai"
        assert info.model == "gpt-4o"
        assert info.socket.endswith(".sock")

        items = await mgr.list_all()
        assert len(items) == 1
        assert items[0].id == info.id

        resp = await mgr.delete(info.id)
        assert resp.id == info.id
        assert resp.status == "stopped"

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
        req = AiCreateRequest(provider="o", model="m", api_key="k", base_url="b", id="dup")
        info = await mgr.create(req)
        with pytest.raises(ValueError, match="already exists"):
            await mgr.create(req)
        await mgr.delete(info.id)
    finally:
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_aimanager_has_and_get_socket(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        mgr = AIManager(_prefix="gw-test", _tg=tg)
        req = AiCreateRequest(provider="o", model="m", api_key="k", base_url="b")
        info = await mgr.create(req)
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
        req = AiCreateRequest(provider="o", model="m", api_key="k", base_url="b")
        info = await mgr.create(req)
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

        ai_req = AiCreateRequest(provider="o", model="m", api_key="k", base_url="b", id="ai1")
        await am.create(ai_req)

        sess_req = SessionCreateRequest(ai_id="ai1", workspace=str(tmp_path))
        info = await sm.create(sess_req)
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
        req = SessionCreateRequest(ai_id="no-such-ai", workspace=str(tmp_path))
        with pytest.raises(LookupError, match="not found"):
            await sm.create(req)
    finally:
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_sessionmanager_duplicate_id(tmp_path: str) -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        am = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=am, _prefix="gw-test", _tg=tg)

        ai_req = AiCreateRequest(provider="o", model="m", api_key="k", base_url="b", id="ai1")
        await am.create(ai_req)

        sess_req = SessionCreateRequest(ai_id="ai1", workspace=str(tmp_path), id="dup")
        info = await sm.create(sess_req)
        with pytest.raises(ValueError, match="already exists"):
            await sm.create(sess_req)
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

        ai_req = AiCreateRequest(provider="o", model="m", api_key="k", base_url="b", id="ai1")
        await am.create(ai_req)

        req = SessionCreateRequest(ai_id="ai1", workspace=str(tmp_path))
        info = await sm.create(req)
        assert sm.has(info.id)
        assert not sm.has("nonexistent")
        assert sm.get_channel_socket(info.id) == info.channel_socket
        with pytest.raises(LookupError):
            sm.get_channel_socket("nonexistent")
        await sm.delete(info.id)
        await am.delete("ai1")
    finally:
        await tg.__aexit__(None, None, None)
