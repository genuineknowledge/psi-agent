"""``POST /feishu/route`` 与 ``GET /feishu/routes`` —— **只服务 channel 进程**。

判据不是 cookie 而是 app_secret 的 HMAC (见 :mod:`psi_agent._service_auth`): 调用方是 channel
进程, 它没有、也不该有一份用户登录态。这些用例把「谁能打这两条」钉死 ——

* 未签名 → **401**。修复前是 201: 任何能打到 gateway 端口的东西 (同容器的其它进程、开发机上
  任何本地进程) 都能凭一句 ``{"open_id": …}`` 凭空 spawn 会话, 并拿到内部管道路径;
* 签名错 / 时间戳过期 / body 被改 → 401;
* **有效登录 cookie 也不行** —— 这两条给出的是**所有人**的路由表与 spawn 能力, 用户身份不是
  这套接口的合法凭据;
* 正确的签名照旧 201 / 200 —— 没把 channel 自己挡在门外。

云上那层反代白名单 (``deploy/haitun/oauth-proxy.py`` 刻意不含这两条) 是**部署形态**的缓解,
不是这条路由自身的判据: 用例必须能在没有反代的情况下判「未签名进不来」。
"""

from __future__ import annotations

import json
import os
import time

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from psi_agent._service_auth import sign
from psi_agent.gateway.feishu._auth import FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port
from tests.psi_agent.gateway._route_signing import TEST_APP_SECRET, signed_get, signed_json_post

ROUTE_PATH = "/feishu/route"
ROUTES_PATH = "/feishu/routes"

#: 恶意 / 无关调用方用的 body: 它若被受理, 就会凭空建出 ``feishu-ou_mallory``。
PAYLOAD: dict[str, object] = {"open_id": "ou_mallory", "ai_id": "ai1"}


async def _make_app(tg, tmp_path: str):
    aim = AIManager(_prefix="route-auth-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="route-auth-test", _tg=tg)
    app = register_feishu_routes(
        await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(tmp_path, "appdata")),
        feishu_ai_id="ai1",
        feishu_workspace_root=os.path.join(tmp_path, "ws"),
        feishu_app_secret=TEST_APP_SECRET,
    )
    return aim, sm, app


async def _create_ai(http: ClientSession, base_url: str) -> None:
    async with http.post(
        f"{base_url}/ais",
        json={
            "provider": "openai",
            "model": "gpt-4o",
            "api_key": "sk-test",
            "base_url": "https://api.example.com",
            "id": "ai1",
        },
    ) as resp:
        assert resp.status == 201


def _raw(payload: dict[str, object]) -> bytes:
    """与 ``_route_signing.signed_json_post`` 同一份序列化 —— body 必须逐字节可比。"""
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _post_headers(body: bytes, *, secret: str = TEST_APP_SECRET, timestamp: str = "") -> dict[str, str]:
    """按 *body* 签一份 POST 头。每条 401 用例只在一个维度上造次。"""
    return {
        "Content-Type": "application/json; charset=utf-8",
        **sign(secret, method="POST", path=ROUTE_PATH, body=body, timestamp=timestamp),
    }


@pytest.mark.anyio
async def test_route_family_rejects_everyone_without_a_valid_signature(tmp_path: str) -> None:
    """未签名 / 签名坏 / 凭 cookie —— 三种「不是 channel」的调用方全部 401, 且**什么都没建**。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    _aim, _sm, app = await _make_app(tg, str(tmp_path))
    auth: FeishuAuth = app["feishu_auth"]
    cookie = {SID_COOKIE: auth.issue(Identity(open_id="ou_alice", name="Alice"))}
    base_url, runner = await _start_app_on_free_port(app)
    body = _raw(PAYLOAD)
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as http:
            await _create_ai(http, base_url)

            # --- 未签名 (这条在修复前回 201, 把网关的内部管道路径交了出去) ---
            async with http.post(f"{base_url}{ROUTE_PATH}", json=PAYLOAD) as resp:
                assert resp.status == 401, await resp.text()
            async with http.get(f"{base_url}{ROUTES_PATH}") as resp:
                assert resp.status == 401

            # --- 有登录态也不行: cookie 是**用户**身份, 不是这套进程间接口的凭据 ---
            async with http.post(f"{base_url}{ROUTE_PATH}", json=PAYLOAD, cookies=cookie) as resp:
                assert resp.status == 401
            async with http.get(f"{base_url}{ROUTES_PATH}", cookies=cookie) as resp:
                assert resp.status == 401

            # --- 签名错 (两边 secret 不一致的形态; 也是 channel 侧那条 WARNING 的场景) ---
            async with http.post(
                f"{base_url}{ROUTE_PATH}", data=body, headers=_post_headers(body, secret="other-secret")
            ) as resp:
                assert resp.status == 401

            # --- 时间戳过期 (抓到旧请求重放) ---
            stale = str(int(time.time()) - 3600)
            async with http.post(
                f"{base_url}{ROUTE_PATH}", data=body, headers=_post_headers(body, timestamp=stale)
            ) as resp:
                assert resp.status == 401

            # --- 签的是甲、发的是乙: 签名覆盖 body 摘要, 改 open_id 就验不过 ---
            async with http.post(
                f"{base_url}{ROUTE_PATH}",
                data=_raw({**PAYLOAD, "open_id": "ou_someone_else"}),
                headers=_post_headers(body),
            ) as resp:
                assert resp.status == 401

            # **拒绝必须发生在 spawn 之前**: 上面这些尝试一次都不该留下路由记录。
            async with http.get(f"{base_url}{ROUTES_PATH}", headers=signed_get(ROUTES_PATH)) as resp:
                assert resp.status == 200
                assert await resp.json() == []
    finally:
        await runner.cleanup()
        # ``tg.__aexit__(None, None, None)`` 是「正常退出」语义, 任何 anyio **等**着子任务自己
        # 结束 —— 而 route 起来的 Session / AI 是常驻服务, 永不返回 (根 AGENTS.md 坑 19: 不退组
        # 会把断言失败放大成挂死)。
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_signed_channel_request_still_routes_and_lists(tmp_path: str) -> None:
    """正确签名的请求照旧 201 / 200 —— 判据是「谁在问」, 不是「关掉这条接口」。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    _aim, _sm, app = await _make_app(tg, str(tmp_path))
    base_url, runner = await _start_app_on_free_port(app)
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as http:
            await _create_ai(http, base_url)

            body, headers = signed_json_post(ROUTE_PATH, {"open_id": "ou_alice", "ai_id": "ai1"})
            async with http.post(f"{base_url}{ROUTE_PATH}", data=body, headers=headers) as resp:
                assert resp.status == 201, await resp.text()
                payload = await resp.json()
                assert payload["session_id"] == "feishu-ou_alice"
                assert payload["channel_socket"]

            async with http.get(f"{base_url}{ROUTES_PATH}", headers=signed_get(ROUTES_PATH)) as resp:
                assert resp.status == 200
                assert await resp.json() == [{"open_id": "ou_alice", "chat_id": "", "session_id": "feishu-ou_alice"}]
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)
