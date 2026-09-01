from __future__ import annotations

import os

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from psi_agent.gateway.feishu._auth import FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port


async def _rows(http: ClientSession, base_url: str, cookies: dict[str, str]) -> list[dict[str, object]]:
    """读某个身份可见的会话列表 —— 用来拿到「别人的」workspace 真值做劫持尝试。"""
    async with http.get(f"{base_url}/feishu/sessions", cookies=cookies) as resp:
        assert resp.status == 200
        rows: list[dict[str, object]] = await resp.json()
        return rows


@pytest.mark.anyio
async def test_feishu_web_sessions_are_isolated_per_identity(tmp_path: str) -> None:
    """A 看不到 B 的会话; 直取 B 的 history 被拒; 多会话共享 workspace 而各有 jsonl。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    # ``appdata`` 走 create_core_app 的关键字参数 —— 历史 jsonl 落在这个根下, 用 tmp_path
    # 隔开才不会写到开发者真实的 AppData 里。
    app = register_feishu_routes(
        await create_core_app(
            aim,
            sm,
            TitleManager(),
            appdata=os.path.join(str(tmp_path), "appdata"),
        ),
        feishu_ai_id="ai1",
        feishu_workspace_root=os.path.join(str(tmp_path), "ws"),
    )
    auth: FeishuAuth = app["feishu_auth"]
    sid_a = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    sid_b = auth.issue(Identity(open_id="ou_bob", name="Bob"))

    base_url, runner = await _start_app_on_free_port(app)
    created: list[str] = []
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

            # 未登录 → 401, 不是「看到全部」。
            async with http.get(f"{base_url}/feishu/sessions") as resp:
                assert resp.status == 401
            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}) as resp:
                assert resp.status == 401
            async with http.get(f"{base_url}/feishu/summaries") as resp:
                assert resp.status == 401

            ck_a = {SID_COOKIE: sid_a}
            ck_b = {SID_COOKIE: sid_b}

            # A 连开 3 个会话 → 3 个不同 id, 同一个 workspace。
            workspaces: set[str] = set()
            for _ in range(3):
                async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_a) as resp:
                    assert resp.status == 201
                    data = await resp.json()
                    created.append(data["id"])
                    workspaces.add(data["workspace"])
                    assert data["from_im"] is False
            assert len(set(created)) == 3
            assert len(workspaces) == 1  # 决定一: 共享 workspace

            # 机器人那条私聊 session (IM 侧建的) 走 /feishu/route。
            async with http.post(f"{base_url}/feishu/route", json={"open_id": "ou_alice", "ai_id": "ai1"}) as resp:
                assert resp.status == 201
                bot_sid = (await resp.json())["session_id"]
            created.append(bot_sid)

            # 群聊 session 也建一个 —— 它必须**不出现**在私聊列表里。
            async with http.post(
                f"{base_url}/feishu/route",
                json={"open_id": "ou_alice", "chat_id": "oc_room", "chat_type": "group", "ai_id": "ai1"},
            ) as resp:
                assert resp.status == 201
                group_sid = (await resp.json())["session_id"]
            created.append(group_sid)

            # B 也建一个自己的。
            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_b) as resp:
                assert resp.status == 201
                b_sid = (await resp.json())["id"]
            created.append(b_sid)

            # A 传 body 里的 id/workspace 一律不采信 —— 否则 A 发 B 的 workspace 就能把
            # 会话建到 B 的目录里, 而 owns_session 按 workspace 认主, 这条会话随后归 B
            # 所有、出现在 B 的列表里(共享 B 的文件与交付物)。派生只认 cookie 里的身份。
            b_ws = next(r["workspace"] for r in await _rows(http, base_url, ck_b))
            async with http.post(
                f"{base_url}/feishu/sessions",
                json={"backend_id": "ai1", "id": "hijacked-id", "workspace": b_ws},
                cookies=ck_a,
            ) as resp:
                assert resp.status == 201
                hijack = await resp.json()
            created.append(hijack["id"])
            assert hijack["id"] != "hijacked-id"  # body 的 id 没被采信
            assert hijack["workspace"] != b_ws  # body 的 workspace 没被采信
            assert hijack["workspace"] in workspaces  # 仍落在 A 自己那个共享目录里

            # A 的列表: 3 个自建 + 上面那条 + 机器人那条(带角标), 无群聊, 无 B 的。
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_a) as resp:
                assert resp.status == 200
                rows = await resp.json()
            ids = {r["id"] for r in rows}
            assert ids == {*created[:3], hijack["id"], bot_sid}
            assert group_sid not in ids
            assert b_sid not in ids
            assert [r["from_im"] for r in rows if r["id"] == bot_sid] == [True]

            # B 的列表里只有 B 自己那条 —— 上面 A 那次劫持尝试没有落进 B 名下。
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_b) as resp:
                assert {r["id"] for r in await resp.json()} == {b_sid}

            # 直取别人的 history → 403, 不是内容。
            async with http.get(f"{base_url}/feishu/sessions/{b_sid}/history", cookies=ck_a) as resp:
                assert resp.status == 403
            # 群聊的也不行。
            async with http.get(f"{base_url}/feishu/sessions/{group_sid}/history", cookies=ck_a) as resp:
                assert resp.status == 403
            # 自己的可以。
            async with http.get(f"{base_url}/feishu/sessions/{created[0]}/history", cookies=ck_a) as resp:
                assert resp.status == 200
            # 不存在的 → 404。
            async with http.get(f"{base_url}/feishu/sessions/no-such-session/history", cookies=ck_a) as resp:
                assert resp.status == 404

            # titles/summaries 也按身份过滤。
            async with http.post(f"{base_url}/titles", json={"id": b_sid, "title": "B 的秘密"}) as resp:
                assert resp.status == 200
            async with http.get(f"{base_url}/feishu/titles", cookies=ck_a) as resp:
                assert b_sid not in await resp.json()
            async with http.get(f"{base_url}/feishu/titles", cookies=ck_b) as resp:
                assert (await resp.json())[b_sid] == "B 的秘密"

            async with http.post(f"{base_url}/summaries", json={"id": b_sid, "summary": "B 的秘密摘要"}) as resp:
                assert resp.status == 200
            async with http.get(f"{base_url}/feishu/summaries", cookies=ck_a) as resp:
                assert b_sid not in await resp.json()
            async with http.get(f"{base_url}/feishu/summaries", cookies=ck_b) as resp:
                assert (await resp.json())[b_sid] == "B 的秘密摘要"
    finally:
        await runner.cleanup()
        for sid in created:
            with anyio.CancelScope(shield=True):
                await sm.delete(sid)
        await aim.delete("ai1")
        await tg.__aexit__(None, None, None)
