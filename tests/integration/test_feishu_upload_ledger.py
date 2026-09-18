"""入向文件登记簿的**端到端**链路: blob 上传 -> 登记簿落盘 -> 白名单放行 -> 下载 200.

为什么单开一条: 这条链上有三个**各自独立、都可能悄悄断掉**的接缝 --

1. ``_serve_chat_sse`` 有没有把 ``session_id`` / ``appdata`` 真的传给 ``ChatManager.handle``;
2. ``_save_upload()`` 之后有没有真的调 ``_record_upload()`` (修复前压根没有这一步);
3. 下载侧的 ``_session_deliverable_paths`` 读的是不是**同一份**登记簿、同一个路径形状.

单元用例只钉得住第 2 条 (``test_chat_manager.py`` 直接调 ``_record_upload``); 而
``tests/integration/test_gateway.py::test_gateway_blob_send`` 走的是**没配 appdata** 的 app, 于是
``_record_upload`` 早退成 no-op -- 它在修复前后都绿, **挡不住这条链断掉** (这一点是并行回归
验证时实测出来的覆盖边界).

这条用例的判据是一条闭环: **上传成功但下载 403, 就是链断了**. 它不关心 agent 回没回答
(这里压根没有可用的上游模型), 只关心字节落盘与登记这两件事.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from psi_agent._appdata import appdata_uploads_path
from psi_agent.gateway.feishu._auth import FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port

APPDATA_DIR = "appdata"
BLOB_BODY = b"UPLOAD-LEDGER-E2E"


@pytest.mark.anyio
async def test_blob_upload_lands_in_the_ledger_and_is_downloadable(
    tmp_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 入向 blob 落 ``~/Downloads/.psi/<date>/`` (真实家目录) -- 不重定向会往开发者盘里堆垃圾.
    # 必须 patch ``Path.home`` 本身: Windows 上它读 USERPROFILE, 只 setenv("HOME") 不生效
    # (见根 AGENTS.md 坑 21).
    monkeypatch.setattr(Path, "home", lambda: Path(str(tmp_path)))

    appdata = os.path.join(str(tmp_path), APPDATA_DIR)
    tg = anyio.create_task_group()
    await tg.__aenter__()
    aim = AIManager(_prefix="ledger-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="ledger-test", _tg=tg)
    app = register_feishu_routes(
        # **appdata 必须传**: 不传的话 ``_record_upload`` 早退, 这条用例就退化成
        # "上传能通、下载 403 也算过" -- 那正是它要防的那种悄悄断链.
        await create_core_app(aim, sm, TitleManager(), appdata=appdata),
        feishu_ai_id="ai1",
        feishu_workspace_root=os.path.join(str(tmp_path), "ws"),
    )
    auth: FeishuAuth = app["feishu_auth"]
    cookies = {SID_COOKIE: auth.issue(Identity(open_id="ou_alice", name="Alice"))}
    base_url, runner = await _start_app_on_free_port(app)
    try:
        timeout = ClientTimeout(total=20)
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

            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=cookies) as resp:
                assert resp.status == 201, await resp.text()
            async with http.get(f"{base_url}/feishu/sessions", cookies=cookies) as resp:
                assert resp.status == 200
                session_id = (await resp.json())[0]["id"]

            blob = base64.b64encode(BLOB_BODY).decode()
            async with http.post(
                f"{base_url}/feishu/sessions/{session_id}/chat",
                json={"chunks": [{"type": "blob", "name": "upload.txt", "data": blob}]},
                cookies=cookies,
            ) as resp:
                # agent 那边没有可用上游, 所以这里**只要求请求被受理** -- 本用例的判据是
                # "字节落盘 + 登记" 这两件事, 与模型能不能回答无关.
                assert resp.status == 200, await resp.text()
                await resp.read()

        # (1) 登记簿落盘了, 且记的就是这个会话、这条路径.
        ledger = appdata_uploads_path(appdata, session_id)
        assert await anyio.Path(ledger).is_file(), (
            f"上传登记簿没落盘 ({ledger}) -- _serve_chat_sse 没把 session_id/appdata 传给 "
            "ChatManager.handle, 或者 _save_upload 之后没调 _record_upload。"
        )
        text = await anyio.Path(ledger).read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines() if line]
        assert len(rows) == 1, rows
        saved_path = rows[0]["path"]
        assert await anyio.Path(saved_path).read_bytes() == BLOB_BODY
        assert os.path.basename(saved_path) == "upload.txt"

        # (2) 同一个路径能经**带鉴权的下载路由**取回 -- 白名单读的是同一份登记簿.
        async with (
            ClientSession(timeout=timeout) as http,
            http.get(
                f"{base_url}/feishu/sessions/{session_id}/files",
                params={"path": saved_path},
                cookies=cookies,
            ) as resp,
        ):
            assert resp.status == 200, (
                f"上传成功但下载失败 (HTTP {resp.status}) -- 登记簿与白名单不是同一份, 用户上传的附件刷新后就下不动了。"
            )
            assert await resp.read() == BLOB_BODY
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)
