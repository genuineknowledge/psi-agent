"""docs add-on 端点: 鉴权闸门 / CORS 白名单 / 路由隔离。

重点测的是**安全属性**, 因为这是全仓唯一一个对浏览器跨源开放的端点: 没配 token 时整体
不可用、token 错就 401、Origin 不在白名单就不发 CORS 头。路由部分只测键的隔离
(不同文档/不同人不共享 session), 不测 Session 真的起得来 —— 那需要真 AI 实例, 属集成测试。
"""

from __future__ import annotations

import socket
from typing import Any

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout, web

from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._docs_addon import DocsAddonManager
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._title_manager import TitleManager
from psi_agent.gateway.server import create_app

_ALLOWED_ORIGIN = "https://addon.feishu.cn"
_TOKEN = "test-shared-secret"


async def _start_app_on_free_port(app: web.Application) -> tuple[str, web.AppRunner]:
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    return f"http://127.0.0.1:{port}", runner


class _FakeSessionManager:
    """只回答 ``has`` 的替身 —— 路由键测试不需要真 Session。"""

    def has(self, _session_id: str) -> bool:
        return False


def _manager(**kwargs: Any) -> DocsAddonManager:
    return DocsAddonManager(_sm=_FakeSessionManager(), **kwargs)  # ty: ignore


def test_disabled_without_token() -> None:
    """没配 token 就不算启用 —— 无认证的对话端点不该默默对外开着。"""
    assert _manager().enabled is False
    assert _manager(_token=_TOKEN).enabled is True


def test_token_comparison_rejects_wrong_and_empty() -> None:
    dam = _manager(_token=_TOKEN)
    assert dam.check_token(_TOKEN) is True
    assert dam.check_token("wrong") is False
    assert dam.check_token("") is False
    # 未配 token 时任何输入都不该通过, 包括空串对空串。
    assert _manager().check_token("") is False


def test_route_keys_isolate_docs_and_users() -> None:
    """(文档, 人) 两个维度都必须隔离, 且拼接不产生歧义。"""
    dam = _manager(_token=_TOKEN)
    same = dam._session_id("docA", "userA")
    assert dam._session_id("docA", "userA") == same, "同一 (文档,人) 必须稳定复用"
    assert dam._session_id("docB", "userA") != same, "换文档必须换 session"
    assert dam._session_id("docA", "userB") != same, "换人必须换 session"
    # ("ab","c") 与 ("a","bc") 拼出的键不能相撞。
    assert dam._route_key("ab", "c") != dam._route_key("a", "bc")


@pytest.mark.anyio
async def test_route_rejects_empty_identifiers() -> None:
    dam = _manager(_token=_TOKEN, _ai_id="ai-1")
    with pytest.raises(ValueError, match="doc_token"):
        await dam.route("", "ou_1")
    with pytest.raises(ValueError, match="user_id"):
        await dam.route("doccn1", "")


def _app_kwargs(*, token: str = _TOKEN) -> dict[str, Any]:
    return {
        "docs_addon_token": token,
        "docs_addon_origins": (_ALLOWED_ORIGIN,),
        "docs_addon_ai_id": "ai-1",
    }


async def _make_app(tg: anyio.abc.TaskGroup, **kwargs: Any) -> web.Application:
    aim = AIManager(_prefix="docs-addon-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="docs-addon-test", _tg=tg)
    return await create_app(aim, sm, TitleManager(), **kwargs)


@pytest.mark.anyio
async def test_endpoint_requires_token_and_honours_origin_allowlist() -> None:
    tg = anyio.create_task_group()
    await tg.__aenter__()
    app = await _make_app(tg, **_app_kwargs())
    base_url, runner = await _start_app_on_free_port(app)
    timeout = ClientTimeout(total=10)
    try:
        async with ClientSession(timeout=timeout) as http:
            body = {"doc_token": "doccn1", "user_id": "ou_1"}

            # 缺 token → 401, 且不能泄漏出 session 信息。
            async with http.post(f"{base_url}/docs-addon/session", json=body) as resp:
                assert resp.status == 401
                assert "session_id" not in await resp.text()

            # token 错 → 401。
            async with http.post(
                f"{base_url}/docs-addon/session",
                json=body,
                headers={"X-Psi-Addon-Token": "wrong"},
            ) as resp:
                assert resp.status == 401

            # 白名单内的 Origin → 回 CORS 头, 且带 Vary: Origin。
            async with http.options(
                f"{base_url}/docs-addon/session",
                headers={"Origin": _ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"},
            ) as resp:
                assert resp.status == 204
                assert resp.headers["Access-Control-Allow-Origin"] == _ALLOWED_ORIGIN
                assert resp.headers.get("Vary") == "Origin"
                # 显式 token 鉴权, 故绝不允许浏览器捎带 cookie。
                assert "Access-Control-Allow-Credentials" not in resp.headers

            # 白名单外的 Origin → 不发 CORS 头 (浏览器据此拦下响应)。
            for bad_origin in ("https://evil.example", "https://evil-feishu.cn"):
                async with http.options(
                    f"{base_url}/docs-addon/session",
                    headers={"Origin": bad_origin, "Access-Control-Request-Method": "POST"},
                ) as resp:
                    assert "Access-Control-Allow-Origin" not in resp.headers, bad_origin
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_endpoint_absent_when_token_unset() -> None:
    """不配 token 时端点整体 404, 而不是「存在但拒绝」。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    app = await _make_app(tg, **_app_kwargs(token=""))
    base_url, runner = await _start_app_on_free_port(app)
    timeout = ClientTimeout(total=10)
    try:
        async with (
            ClientSession(timeout=timeout) as http,
            http.post(
                f"{base_url}/docs-addon/session",
                json={"doc_token": "doccn1", "user_id": "ou_1"},
                headers={"X-Psi-Addon-Token": _TOKEN},
            ) as resp,
        ):
            assert resp.status == 404
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_other_paths_never_get_cors_headers() -> None:
    """CORS 只开给 ``/docs-addon/*`` —— 别的端点不能因此变成网页可驱动。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    app = await _make_app(tg, **_app_kwargs())
    base_url, runner = await _start_app_on_free_port(app)
    timeout = ClientTimeout(total=10)
    try:
        async with ClientSession(timeout=timeout) as http:
            for path in ("/sessions", "/ais", "/defaults"):
                async with http.get(f"{base_url}{path}", headers={"Origin": _ALLOWED_ORIGIN}) as resp:
                    assert "Access-Control-Allow-Origin" not in resp.headers, path
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_chat_rejects_bad_body_before_touching_sessions() -> None:
    """chunks 不是数组要 400; 且该端点不接受调用方指定 session_id。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    app = await _make_app(tg, **_app_kwargs())
    base_url, runner = await _start_app_on_free_port(app)
    timeout = ClientTimeout(total=10)
    headers = {"X-Psi-Addon-Token": _TOKEN}
    try:
        async with ClientSession(timeout=timeout) as http:
            async with http.post(
                f"{base_url}/docs-addon/chat",
                json={"doc_token": "doccn1", "user_id": "ou_1", "chunks": "not-a-list"},
                headers=headers,
            ) as resp:
                assert resp.status == 400

            # doc_token 缺失 → 400 (而不是拿空键建出一个无主 session)。
            async with http.post(
                f"{base_url}/docs-addon/chat",
                json={"user_id": "ou_1", "chunks": []},
                headers=headers,
            ) as resp:
                assert resp.status == 400
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)
