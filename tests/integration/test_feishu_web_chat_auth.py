"""守 ``POST /feishu/sessions/{id}/chat`` 的三段判定 —— 401 / 通 / 403。

## 为什么值得单开一个文件

骨架的 ``POST /sessions/{id}/chat`` **一行身份校验都没有**(它在容器内回环 8080 服务本机,
那是它的合理用途), 而它恰好是能**驱动 agent 执行工具**的那条: 跑 bash、读公司表格、往飞书
发消息。飞书网页应用要上公网, 暴露裸的那条等于任何知道一个 session id 的人都能让公司 agent
干活、且不问他是谁。所以网页应用改打这条带鉴权的对等物, 裸的那条行为一字不改。

判定与 ``/feishu/sessions/{id}/history`` 同一套 ``owns_session``, 但**共用实现不等于共用判据**:
history 那条有用例(``test_feishu_web_sessions.py``), chat 这条没有的话, 谁把 ``_web_chat`` 里
那行归属校验删掉、或者把 handler 换回裸的 ``_handle_chat``, 全库都不会红。

## 本地测得到 / 测不到

测得到的是**用例层面**这三条: 无 cookie 401、有效 cookie + 自己的会话通到 SSE、有效 cookie
打别人的会话 403。**跨身份隔离在真实飞书环境下的表现测不到** —— 那要真 open_id、真
``tt.requestAccess`` 换回来的 code、真容器拓扑, 只能上云在飞书真机验。

## 变异复核

这三条本身也要能被判假。``test_ownership_check_is_load_bearing`` 把归属校验那行改坏
(``owns_session`` 恒真), 确认「打别人的会话」那条**真的会红**而不是被别处的兜底提前吃掉 ——
本仓踩过: 一个分支撞四次兜底分支, 用例全绿但判据根本没吃劲。
"""

from __future__ import annotations

import json
import os

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout
from anyio.abc import TaskGroup

from psi_agent.gateway.feishu import _routes
from psi_agent.gateway.feishu._auth import FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.conftest import MockAIServer
from tests.integration.test_gateway import _chunk, _start_app_on_free_port

CHAT_BODY = {"chunks": [{"type": "text", "text": "hello"}]}


async def _read_sse_text(resp: object) -> str:
    """把 SSE 流里的 ``type: text`` 拼起来。``[DONE]`` 收尾。"""
    out: list[str] = []
    async for raw in resp.content:  # ty: ignore
        line = raw.decode().strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            break
        evt = json.loads(payload)
        if evt.get("type") == "text":
            out.append(evt.get("text") or "")
    return "".join(out)


class _Fixture:
    """一套跑起来的 gateway + 两个身份 + 各自一个会话。"""

    def __init__(self) -> None:
        self.base_url = ""
        self.ck_a: dict[str, str] = {}
        self.ck_b: dict[str, str] = {}
        self.sid_a = ""  # A 自己的会话 id
        self.sid_b = ""  # B 自己的会话 id


async def _setup(
    tmp_path: str,
    mock_ai_server: MockAIServer,
    tg: TaskGroup,
) -> tuple[_Fixture, SessionManager, AIManager, object]:
    """建 app、起端口、给 A/B 各建一个会话。会话经 ``POST /feishu/sessions`` 建 —— workspace
    由后端按 cookie 里的 open_id 派生, 归属判定认的正是它。"""
    mock_ai_server.set_responses([_chunk(content="Hello from chat!", finish_reason="stop")])
    mock_base_url = await mock_ai_server.start()

    aim = AIManager(_prefix="fw-chat-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="fw-chat-test", _tg=tg)
    await aim.create(provider="openai", model="test", api_key="k", base_url=mock_base_url, id="ai1")

    app = register_feishu_routes(
        await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(tmp_path, "appdata")),
        feishu_ai_id="ai1",
        feishu_workspace_root=os.path.join(tmp_path, "ws"),
    )
    auth: FeishuAuth = app["feishu_auth"]
    fx = _Fixture()
    fx.ck_a = {SID_COOKIE: auth.issue(Identity(open_id="ou_alice", name="Alice"))}
    fx.ck_b = {SID_COOKIE: auth.issue(Identity(open_id="ou_bob", name="Bob"))}

    fx.base_url, runner = await _start_app_on_free_port(app)
    timeout = ClientTimeout(total=15)
    async with ClientSession(timeout=timeout) as http:
        for cookies, attr in ((fx.ck_a, "sid_a"), (fx.ck_b, "sid_b")):
            async with http.post(f"{fx.base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=cookies) as resp:
                assert resp.status == 201
                setattr(fx, attr, (await resp.json())["id"])
    return fx, sm, aim, runner


async def _teardown(fx: _Fixture, sm: SessionManager, aim: AIManager, runner: object) -> None:
    await runner.cleanup()  # ty: ignore
    for sid in (fx.sid_a, fx.sid_b):
        if sid:
            with anyio.CancelScope(shield=True):
                await sm.delete(sid)
    await aim.delete("ai1")


# ---- 三条验收判据 --------------------------------------------------------


@pytest.mark.anyio
async def test_chat_without_cookie_is_401(tmp_path: str, mock_ai_server: MockAIServer) -> None:
    """无 cookie → 401, 且**一个字都不流**。

    判 401 不判「流里没内容」: 后者会被一个空回复的 200 假绿骗过去。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    fx, sm, aim, runner = await _setup(str(tmp_path), mock_ai_server, tg)
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=15)) as http,
            http.post(f"{fx.base_url}/feishu/sessions/{fx.sid_a}/chat", json=CHAT_BODY) as resp,
        ):
            assert resp.status == 401
            assert (await resp.json())["error"] == "not logged in"
    finally:
        await _teardown(fx, sm, aim, runner)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_chat_on_own_session_streams(tmp_path: str, mock_ai_server: MockAIServer) -> None:
    """有效 cookie + 自己的会话 → 200 且真的把上游回复流出来。

    必须读到内容: 只判 200 的话, ``_serve_chat_sse`` 早退(比如 session 查不到 socket)
    也是 200 + 一条 ``[DONE]``。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    fx, sm, aim, runner = await _setup(str(tmp_path), mock_ai_server, tg)
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=15)) as http,
            http.post(f"{fx.base_url}/feishu/sessions/{fx.sid_a}/chat", json=CHAT_BODY, cookies=fx.ck_a) as resp,
        ):
            assert resp.status == 200
            assert "Hello from chat!" in await _read_sse_text(resp)
    finally:
        await _teardown(fx, sm, aim, runner)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_chat_on_someone_elses_session_is_403(tmp_path: str, mock_ai_server: MockAIServer) -> None:
    """有效 cookie 打**别人的**会话 → 403。整张卡的核心判据。

    只验登录不验归属的话这条会是 200 —— 登录任一账号就能驱动别人会话里的 agent。
    403 而非 404 是与 ``/feishu/sessions/{id}/history`` 对齐(真·不存在已占了 404)。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    fx, sm, aim, runner = await _setup(str(tmp_path), mock_ai_server, tg)
    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as http:
            # A 拿自己的 cookie 打 B 的会话。
            async with http.post(
                f"{fx.base_url}/feishu/sessions/{fx.sid_b}/chat", json=CHAT_BODY, cookies=fx.ck_a
            ) as resp:
                assert resp.status == 403
                assert (await resp.json())["error"] == "forbidden"
            # 反向也不行 —— 免得判定里写死了某一侧。
            async with http.post(
                f"{fx.base_url}/feishu/sessions/{fx.sid_a}/chat", json=CHAT_BODY, cookies=fx.ck_b
            ) as resp:
                assert resp.status == 403
            # 不存在的会话是 404 而非 403: 两种状态前端要分得开。
            async with http.post(
                f"{fx.base_url}/feishu/sessions/no-such-session/chat", json=CHAT_BODY, cookies=fx.ck_a
            ) as resp:
                assert resp.status == 404
    finally:
        await _teardown(fx, sm, aim, runner)
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_meeting_session_history_is_readable_but_chat_is_read_only(
    tmp_path: str, mock_ai_server: MockAIServer
) -> None:
    """The shared meeting Session exposes history but never accepts user prompts."""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    fx, sm, aim, runner = await _setup(str(tmp_path), mock_ai_server, tg)
    await sm.create(
        ai_id="ai1",
        id="meeting-session",
        workspace=os.path.join(str(tmp_path), "meeting-session"),
        active_schedules=("*",),
    )
    try:
        async with ClientSession(timeout=ClientTimeout(total=15)) as http:
            async with http.get(f"{fx.base_url}/feishu/sessions/meeting-session/history", cookies=fx.ck_a) as resp:
                assert resp.status == 200
                assert await resp.json() == []
            async with http.post(
                f"{fx.base_url}/feishu/sessions/meeting-session/chat",
                json=CHAT_BODY,
                cookies=fx.ck_a,
            ) as resp:
                assert resp.status == 403
                assert (await resp.json())["error"] == "meeting-session is read-only"
    finally:
        with anyio.CancelScope(shield=True):
            await sm.delete("meeting-session")
        await _teardown(fx, sm, aim, runner)
        await tg.__aexit__(None, None, None)


# ---- 变异复核 -----------------------------------------------------------


@pytest.mark.anyio
async def test_ownership_check_is_load_bearing(
    tmp_path: str, mock_ai_server: MockAIServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """把归属校验改坏 → 越权那条**必须**变成 200。

    这条是上面第三条的元判据: 它证明那个 403 是 ``owns_session`` 给的, 而不是别处兜底
    (比如 session 恰好查不到、上游恰好报错) 提前吃掉了结论。本仓踩过一次「用例绿但判据
    没吃劲」, 所以每加一条安全判据都要能这样被判假。

    改的是 ``_routes.owns_session`` 这个名字 —— handler 里那行 ``if not owns_session(...)``
    正是从这里取的。恒真之后越权应当直通到 SSE。
    """
    monkeypatch.setattr(_routes, "owns_session", lambda *a, **kw: True)
    tg = anyio.create_task_group()
    await tg.__aenter__()
    fx, sm, aim, runner = await _setup(str(tmp_path), mock_ai_server, tg)
    try:
        async with (
            ClientSession(timeout=ClientTimeout(total=15)) as http,
            http.post(f"{fx.base_url}/feishu/sessions/{fx.sid_b}/chat", json=CHAT_BODY, cookies=fx.ck_a) as resp,
        ):
            assert resp.status == 200, (
                "把 owns_session 打成恒真之后, 越权请求**仍然**不是 200 —— "
                "说明那条 403 用例守的不是归属校验, 而是别处的兜底。"
            )
    finally:
        await _teardown(fx, sm, aim, runner)
        await tg.__aexit__(None, None, None)
