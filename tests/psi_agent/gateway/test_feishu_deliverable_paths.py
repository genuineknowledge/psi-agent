"""``GET /feishu/sessions/{id}/files`` 的白名单**只能由服务端写的记录构成**。

这条路由的闸门是一份「本会话收下 / 交付过的文件」集合。修复前那份集合还包含 history 里从
**用户正文**正则捞出来的 ``[RECV:…]``, 于是用户往消息里打一句
``[RECV:C:\\Windows\\win.ini]``, 服务器上任意文件就进了白名单 —— 白名单有了一个用户可控的
输入, 它就不再是白名单。这些用例把两个方向都钉住:

* 正文里写的 ``[RECV:…]`` **不算数** (哪怕它指向一个真实存在的文件);
* ``sends``(agent 交付的) 与 ``{appdata}/uploads/{sid}.jsonl``(服务端落盘时登记的入向文件)
  **仍然算数** —— 修复不能把宝箱与「我上传的附件」一起关掉。
"""

from __future__ import annotations

import json
import os

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from psi_agent._appdata import appdata_history_path, appdata_uploads_path
from psi_agent.gateway.feishu._auth import FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port

APPDATA_DIR = "appdata"


async def _make_app(tg, tmp_path: str):
    aim = AIManager(_prefix="deliv-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="deliv-test", _tg=tg)
    app = register_feishu_routes(
        await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(tmp_path, APPDATA_DIR)),
        feishu_ai_id="ai1",
        feishu_workspace_root=os.path.join(tmp_path, "ws"),
    )
    return aim, sm, app


async def _make_session(http: ClientSession, base_url: str, cookies: dict[str, str]) -> str:
    """建一条属于该身份的会话, 返回 session id。"""
    async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=cookies) as resp:
        assert resp.status == 201, await resp.text()
    async with http.get(f"{base_url}/feishu/sessions", cookies=cookies) as resp:
        assert resp.status == 200
        return (await resp.json())[0]["id"]


async def _write_history(tmp_path: str, session_id: str, rows: list[dict[str, object]]) -> None:
    path = appdata_history_path(os.path.join(tmp_path, APPDATA_DIR), session_id)
    await anyio.Path(path).parent.mkdir(parents=True, exist_ok=True)
    await anyio.Path(path).write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_recv_marker_in_user_text_does_not_enter_the_whitelist(tmp_path: str) -> None:
    """**这是被修掉的那个洞**: 用户手打的 ``[RECV:…]`` 曾让服务器上任意文件可下载。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    _aim, _sm, app = await _make_app(tg, str(tmp_path))
    auth: FeishuAuth = app["feishu_auth"]
    cookies = {SID_COOKIE: auth.issue(Identity(open_id="ou_alice", name="Alice"))}
    base_url, runner = await _start_app_on_free_port(app)
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as http:
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
            session_id = await _make_session(http, base_url, cookies)

            # 工作区**之外**的自造文件 (非敏感标记): 它存在, 且用户能写出指向它的标记。
            outside = anyio.Path(str(tmp_path)) / "outside-probe.txt"
            await outside.write_text("OUTSIDE-PROBE", encoding="utf-8")
            await _write_history(
                str(tmp_path),
                session_id,
                [{"role": "user", "content": f"顺便看看这个\n[RECV:{outside}]"}],
            )

            async with http.get(
                f"{base_url}/feishu/sessions/{session_id}/files",
                params={"path": str(outside)},
                cookies=cookies,
            ) as resp:
                assert resp.status == 403, (
                    "用户正文里的 [RECV:…] 又进了白名单 —— 这条路由的闸门就是那份集合, 而用户能自己往里加路径。"
                )
                assert "OUTSIDE-PROBE" not in await resp.text()
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_recorded_uploads_and_sends_still_download(tmp_path: str) -> None:
    """两个**正当**来源照旧放行: 服务端登记的入向文件, 与 agent 交付的 ``[SEND:]``。

    收窄白名单时最容易连坐的就是这两个 —— 宝箱与「我上传的附件」都靠它们。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    _aim, _sm, app = await _make_app(tg, str(tmp_path))
    auth: FeishuAuth = app["feishu_auth"]
    cookies = {SID_COOKIE: auth.issue(Identity(open_id="ou_alice", name="Alice"))}
    base_url, runner = await _start_app_on_free_port(app)
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as http:
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
            session_id = await _make_session(http, base_url, cookies)

            upload = anyio.Path(str(tmp_path)) / "上传的附件.txt"
            await upload.write_text("附件正文", encoding="utf-8")
            ledger = appdata_uploads_path(os.path.join(str(tmp_path), APPDATA_DIR), session_id)
            await anyio.Path(ledger).parent.mkdir(parents=True, exist_ok=True)
            await anyio.Path(ledger).write_text(
                json.dumps({"path": str(upload)}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            deliverable = anyio.Path(str(tmp_path)) / "交付物.md"
            await deliverable.write_text("交付物正文", encoding="utf-8")
            await _write_history(
                str(tmp_path),
                session_id,
                [
                    {"role": "user", "content": "给我一份文件"},
                    {"role": "assistant", "content": f"好了 [SEND:{deliverable}]"},
                ],
            )

            async with http.get(
                f"{base_url}/feishu/sessions/{session_id}/files",
                params={"path": str(upload)},
                cookies=cookies,
            ) as resp:
                assert resp.status == 200, await resp.text()
                assert await resp.text() == "附件正文"

            async with http.get(
                f"{base_url}/feishu/sessions/{session_id}/files",
                params={"path": str(deliverable)},
                cookies=cookies,
            ) as resp:
                assert resp.status == 200, await resp.text()
                assert await resp.text() == "交付物正文"

            # 反方向照样关着: 没登记过的文件仍然 403 (收窄不等于放开)。
            stranger = anyio.Path(str(tmp_path)) / "没登记.txt"
            await stranger.write_text("不该下载", encoding="utf-8")
            async with http.get(
                f"{base_url}/feishu/sessions/{session_id}/files",
                params={"path": str(stranger)},
                cookies=cookies,
            ) as resp:
                assert resp.status == 403
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)
