"""``/feishu/sessions/{id}/…`` 这一族**带鉴权的对等路由** —— 未登录、越权、路径越界的边界。

## 为什么这些用例必须存在

云上拓扑是 Caddy → ``oauth-proxy.py``(白名单反代) → gateway 容器, 而**骨架**的几条会话级
路由既在反代白名单之外、又一行鉴权都没有:

* ``GET /sessions/{id}/todos`` / ``todo-segments`` —— 网页应用的「任务进度 / 执行步骤 /
  历史子任务」全由它们驱动。云上恒 404, 表现是左侧任务上下文永远停在「待继续」、进度恒为 0。
* ``GET /workspace/file`` 按**任意路径**读文件且无鉴权 —— 不能拿它当交付物下载口。

所以 ToB 前端改打 ``/feishu/`` 前缀下的对等物(该前缀本就在白名单里)。它们必须做到:

1. 未登录 → 401(不是「看到全部」);
2. 别人的 / 群聊的会话 → 403;
3. 不存在的 → 404;
4. 下载**只放行本会话声明过的文件** —— 这是这一族里唯一会读磁盘的入口, 边界必须钉住;
5. 导出回的必须是**原始 jsonl**, 不是 ``/history`` 那种投影(投影丢了工具调用参数与
   ``thinking_ms``, 拿回去与磁盘上的记录对不上)。
"""

from __future__ import annotations

import json
import os

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from psi_agent._appdata import appdata_history_path
from psi_agent.gateway.feishu._auth import FeishuAuth, Identity
from psi_agent.gateway.feishu._routes import SID_COOKIE, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port

#: 这一族里全部 GET 路由的形状 —— 参数化跑「未登录 / 不存在」两组边界。
_SESSION_GET_SUFFIXES = ("todos", "todo-segments", "export")


async def _make_app(tg, tmp_path: str):
    aim = AIManager(_prefix="peer-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="peer-test", _tg=tg)
    app = register_feishu_routes(
        await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(tmp_path, "appdata")),
        feishu_ai_id="ai1",
        feishu_workspace_root=os.path.join(tmp_path, "ws"),
    )
    return aim, sm, app


@pytest.mark.anyio
async def test_peer_routes_require_identity_and_respect_ownership(tmp_path: str) -> None:
    """401 / 404 / 403 三段判定, 逐条打一遍 —— 顺序错了会把「被删」和「越权」糊成一个。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    aim, sm, app = await _make_app(tg, str(tmp_path))
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

            ck_a = {SID_COOKIE: sid_a}
            ck_b = {SID_COOKIE: sid_b}

            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_a) as resp:
                assert resp.status == 201
                a_ws = (await resp.json())["workspace"]
                # 会话 id 只在这一步拿得到 —— POST 的响应体里有, GET 列表里也有。
                assert resp.status == 201
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_a) as resp:
                a_sid = (await resp.json())[0]["id"]
            created.append(a_sid)

            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_b) as resp:
                b_sid = (await resp.json())["id"]
            created.append(b_sid)

            # 存在一条历史, 后面的下载与导出才有东西可读。
            deliverable = anyio.Path(str(tmp_path)) / "交付物.md"
            await deliverable.write_text("这是交付物的正文", encoding="utf-8")
            history = appdata_history_path(os.path.join(str(tmp_path), "appdata"), a_sid)
            await anyio.Path(history).parent.mkdir(parents=True, exist_ok=True)
            await anyio.Path(history).write_text(
                json.dumps({"role": "user", "content": "给我一份文件"}, ensure_ascii=False)
                + "\n"
                + json.dumps(
                    {"role": "assistant", "content": f"好了 [SEND:{deliverable}]"},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            # --- 未登录: 401, 且不是「回空集合」 ---
            for suffix in _SESSION_GET_SUFFIXES:
                async with http.get(f"{base_url}/feishu/sessions/{a_sid}/{suffix}") as resp:
                    assert resp.status == 401, suffix
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/todo-segments/seg-1") as resp:
                assert resp.status == 401
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/files?path={deliverable}") as resp:
                assert resp.status == 401

            # --- 别人的会话: 403 ---
            for suffix in _SESSION_GET_SUFFIXES:
                async with http.get(f"{base_url}/feishu/sessions/{b_sid}/{suffix}", cookies=ck_a) as resp:
                    assert resp.status == 403, suffix
            async with http.get(f"{base_url}/feishu/sessions/{b_sid}/files?path=x", cookies=ck_a) as resp:
                assert resp.status == 403

            # --- 不存在: 404(在归属之前判, 于是「被删」与「越权」分得开) ---
            for suffix in _SESSION_GET_SUFFIXES:
                async with http.get(f"{base_url}/feishu/sessions/nope/{suffix}", cookies=ck_a) as resp:
                    assert resp.status == 404, suffix
            async with http.get(f"{base_url}/feishu/sessions/nope/todo-segments/seg-1", cookies=ck_a) as resp:
                assert resp.status == 404

            # --- 自己的: 200, 形状是前端消费的那两个键 ---
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/todos", cookies=ck_a) as resp:
                assert resp.status == 200
                body = await resp.json()
                assert set(body) == {"todos", "summary"}
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/todo-segments", cookies=ck_a) as resp:
                assert resp.status == 200
                assert isinstance(await resp.json(), list)
            # 分段不存在 → handler 自己判的 404(json), 与「路由不存在」的 text/plain 区分开。
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/todo-segments/seg-404", cookies=ck_a) as resp:
                assert resp.status == 404
            assert resp.content_type == "application/json"

            # --- 下载: 只放行本会话声明过的文件 ---
            async with http.get(
                f"{base_url}/feishu/sessions/{a_sid}/files",
                params={"path": str(deliverable)},
                cookies=ck_a,
            ) as resp:
                assert resp.status == 200
                assert await resp.text() == "这是交付物的正文"
                # 中文名的附件头必须走 RFC 5987, 否则浏览器拿到的是乱码文件名。
                assert "filename*=UTF-8''" in resp.headers["Content-Disposition"]

            stranger = anyio.Path(str(tmp_path)) / "别人的文件.md"
            await stranger.write_text("不该被下载", encoding="utf-8")
            async with http.get(
                f"{base_url}/feishu/sessions/{a_sid}/files", params={"path": str(stranger)}, cookies=ck_a
            ) as resp:
                assert resp.status == 403  # 存在、但不是这条会话的交付物

            async with http.get(
                f"{base_url}/feishu/sessions/{a_sid}/files",
                params={"path": str(anyio.Path(str(tmp_path)) / "不存在.md")},
                cookies=ck_a,
            ) as resp:
                assert resp.status == 404  # 路径本身不存在

            # 越界: 拿系统文件当交付物 → 不在白名单里 → 403(不是「不存在」)。
            system_file = "/etc/hostname" if os.name != "nt" else r"C:\Windows\win.ini"
            if await anyio.Path(system_file).is_file():
                async with http.get(
                    f"{base_url}/feishu/sessions/{a_sid}/files", params={"path": system_file}, cookies=ck_a
                ) as resp:
                    assert resp.status == 403

            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/files", cookies=ck_a) as resp:
                assert resp.status == 400  # 缺 path 参数

            # --- 导出: 原始 jsonl(带工具调用/thinking_ms 那种), 不是 /history 的投影 ---
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/export", cookies=ck_a) as resp:
                assert resp.status == 200
                exported = await resp.read()
                assert f"{a_sid}.jsonl" in resp.headers["Content-Disposition"]
            raw = await anyio.Path(history).read_bytes()
            assert exported == raw, "导出的必须是磁盘上那份原文, 不能被 /history 的投影改写"
            # 对照: /history 那一份已经不含 [SEND:] 标记了(它被解析进 sends 字段)。
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/history", cookies=ck_a) as resp:
                rows = await resp.json()
            assert any(str(deliverable) in (r.get("sends") or []) for r in rows), rows
            assert "[SEND:" not in json.dumps(rows, ensure_ascii=False)

            # 没写过历史的会话导出 → 404(而不是回一个空文件)。
            async with http.get(f"{base_url}/feishu/sessions/{b_sid}/export", cookies=ck_b) as resp:
                assert resp.status == 404
            assert a_ws  # workspace 取自 POST 响应, 这里只是防止上面那次 assert 被当成无用赋值
    finally:
        await runner.cleanup()
        for sid in created:
            with anyio.CancelScope(shield=True):
                await sm.delete(sid)
        await aim.delete("ai1")
        await tg.__aexit__(None, None, None)
