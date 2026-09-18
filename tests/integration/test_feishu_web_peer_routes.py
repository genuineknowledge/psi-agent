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
from psi_agent.gateway.feishu._stats import current_month
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port
from tests.psi_agent.gateway._route_signing import TEST_APP_SECRET, signed_json_post

#: 这一族里全部 GET 路由的形状 —— 参数化跑「未登录 / 不存在」两组边界。
_SESSION_GET_SUFFIXES = ("todos", "todo-segments", "export")

#: 「本月」的判据不能写死日期: 测试跑在任何一个月都得成立。用**当前自然月**的第一天 00:00
#: (UTC) 造一条问答, 再拿同一个月去查 —— 于是它既在窗口内, 又不依赖运行时刻。
THIS_MONTH = current_month()
THIS_MONTH_UTC = f"{THIS_MONTH}-01T00:00:00Z"


async def _make_app(tg, tmp_path: str):
    aim = AIManager(_prefix="peer-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="peer-test", _tg=tg)
    app = register_feishu_routes(
        await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(tmp_path, "appdata")),
        feishu_ai_id="ai1",
        feishu_app_secret=TEST_APP_SECRET,
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


@pytest.mark.anyio
async def test_title_routes_are_ownership_checked(tmp_path: str) -> None:
    """标题那两条的 id 在 **body** 里 —— 判定必须与路径参数版同一份, 否则这里先漏。"""
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
                a_sid = (await resp.json())["id"]
            created.append(a_sid)
            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_b) as resp:
                b_sid = (await resp.json())["id"]
            created.append(b_sid)

            # --- 未登录: 401(标题那两条都在写身份表之前就该挡住) ---
            async with http.post(f"{base_url}/feishu/titles", json={"id": a_sid, "title": "x"}) as resp:
                assert resp.status == 401
            async with http.post(f"{base_url}/feishu/titles/generate", json={"id": a_sid, "user_text": "hi"}) as resp:
                assert resp.status == 401

            # --- 缺字段: 400(而不是拿空 id 去查会话, 那会变成 404 而看不出是调用方写错了) ---
            async with http.post(f"{base_url}/feishu/titles", json={"title": "没有 id"}, cookies=ck_a) as resp:
                assert resp.status == 400
            async with http.post(f"{base_url}/feishu/titles", json={"id": a_sid}, cookies=ck_a) as resp:
                assert resp.status == 400
            async with http.post(f"{base_url}/feishu/titles/generate", json={}, cookies=ck_a) as resp:
                assert resp.status == 400

            # --- 别人的会话: 403, 而且**在生成之前**就挡住 —— 那条会在服务端跑一次模型 ---
            async with http.post(
                f"{base_url}/feishu/titles", json={"id": b_sid, "title": "B 的标题"}, cookies=ck_a
            ) as resp:
                assert resp.status == 403
            async with http.post(
                f"{base_url}/feishu/titles/generate",
                json={"id": b_sid, "user_text": "hi", "assistant_text": "yo"},
                cookies=ck_a,
            ) as resp:
                assert resp.status == 403
            # 不存在的会话 → 404。
            async with http.post(
                f"{base_url}/feishu/titles", json={"id": "no-such", "title": "x"}, cookies=ck_a
            ) as resp:
                assert resp.status == 404

            # --- 自己的: 200, 且真的写进了标题表(前端下次 listTitles 能看到) ---
            async with http.post(
                f"{base_url}/feishu/titles", json={"id": a_sid, "title": "周会纪要"}, cookies=ck_a
            ) as resp:
                assert resp.status == 200
                assert (await resp.json())["title"] == "周会纪要"
            async with http.get(f"{base_url}/feishu/titles", cookies=ck_a) as resp:
                assert (await resp.json())[a_sid] == "周会纪要"
            # B 看不到 A 的标题(过滤在服务端, 不是显示层)。
            async with http.get(f"{base_url}/feishu/titles", cookies=ck_b) as resp:
                assert a_sid not in await resp.json()
    finally:
        await runner.cleanup()
        for sid in created:
            with anyio.CancelScope(shield=True):
                await sm.delete(sid)
        await aim.delete("ai1")
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_delete_route_has_a_hard_gate_on_the_im_shared_session(tmp_path: str) -> None:
    """删除那条: 归属校验 + **与机器人共用那条不许删**(前端藏按钮只是显示层的闸)。"""
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
                a_sid = (await resp.json())["id"]
            created.append(a_sid)
            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_b) as resp:
                b_sid = (await resp.json())["id"]
            created.append(b_sid)
            # 机器人那条私聊会话 —— 与网页自建的那条是**不同** id。这条打的是进程间接口,
            # 得像 channel 那样签 (app_secret 的 HMAC), 否则先撞上 401。
            raw, headers = signed_json_post("/feishu/route", {"open_id": "ou_alice", "ai_id": "ai1"})
            async with http.post(f"{base_url}/feishu/route", data=raw, headers=headers) as resp:
                assert resp.status == 201
                im_sid = (await resp.json())["session_id"]
            created.append(im_sid)
            assert im_sid != a_sid, "IM 会话与网页自建会话撞成同一个 id, 本用例的判据就失效了"

            # --- 未登录 / 别人的 / 不存在 ---
            async with http.delete(f"{base_url}/feishu/sessions/{a_sid}") as resp:
                assert resp.status == 401
            async with http.delete(f"{base_url}/feishu/sessions/{b_sid}", cookies=ck_a) as resp:
                assert resp.status == 403
            async with http.delete(f"{base_url}/feishu/sessions/no-such-session", cookies=ck_a) as resp:
                assert resp.status == 404
            # 越权那次不能真把它删掉。
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_b) as resp:
                assert b_sid in {r["id"] for r in await resp.json()}

            # --- 硬闸: 自己那条与机器人共用的, 谁都不能删(包括本人) ---
            async with http.delete(f"{base_url}/feishu/sessions/{im_sid}", cookies=ck_a) as resp:
                assert resp.status == 403
                assert "cannot be deleted" in (await resp.json())["error"]
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_a) as resp:
                assert im_sid in {r["id"] for r in await resp.json()}, "硬闸没挡住, 会话真被删了"

            # --- 自己那条网页自建的: 删得掉, 且五处一起清 ---
            async with http.post(f"{base_url}/feishu/titles", json={"id": a_sid, "title": "待删"}, cookies=ck_a) as r:
                assert r.status == 200
            async with http.delete(f"{base_url}/feishu/sessions/{a_sid}", cookies=ck_a) as resp:
                assert resp.status == 200
            created.remove(a_sid)
            async with http.get(f"{base_url}/feishu/sessions", cookies=ck_a) as resp:
                assert a_sid not in {r["id"] for r in await resp.json()}
            async with http.get(f"{base_url}/feishu/sessions/{a_sid}/history", cookies=ck_a) as resp:
                assert resp.status == 404
            async with http.get(f"{base_url}/feishu/titles", cookies=ck_a) as resp:
                assert a_sid not in await resp.json(), "标题没跟着清 —— 删掉的会话会在标题表里留一条"
    finally:
        await runner.cleanup()
        for sid in created:
            with anyio.CancelScope(shield=True):
                await sm.delete(sid)
        await aim.delete("ai1")
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_monthly_stats_are_per_identity_and_count_replies(tmp_path: str) -> None:
    """``GET /feishu/stats/monthly`` —— 「本月执行」那一格: 只数自己的, 且**没写 todo 也算跑过**。

    口径是这次改动的重点: agent 直接回答的回合不写 todo, 旧实现(前端只看 todo 段)会把它们算成
    「这个月没干活」, 而列表里它们的状态早就显示「已完成」了 —— 同一屏两个数字互相打架。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    aim, sm, app = await _make_app(tg, str(tmp_path))
    auth: FeishuAuth = app["feishu_auth"]
    sid_a = auth.issue(Identity(open_id="ou_alice", name="Alice"))
    sid_b = auth.issue(Identity(open_id="ou_bob", name="Bob"))
    base_url, runner = await _start_app_on_free_port(app)
    created: list[str] = []
    appdata = os.path.join(str(tmp_path), "appdata")
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
                a_sid = (await resp.json())["id"]
            created.append(a_sid)
            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": "ai1"}, cookies=ck_b) as resp:
                b_sid = (await resp.json())["id"]
            created.append(b_sid)

            # 未登录 → 401(不是「回 0」)。
            async with http.get(f"{base_url}/feishu/stats/monthly") as resp:
                assert resp.status == 401
            # month 形状不对 → 400。
            async with http.get(f"{base_url}/feishu/stats/monthly?month=2026-13", cookies=ck_a) as resp:
                assert resp.status == 400

            # A 的会话里写一条**本月**的问答 —— 不写 todo, 这正是旧实现漏掉的那一类。
            history = appdata_history_path(appdata, a_sid)
            await anyio.Path(history).parent.mkdir(parents=True, exist_ok=True)
            await anyio.Path(history).write_text(
                json.dumps({"role": "user", "content": "你好", "created_at": THIS_MONTH_UTC}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )

            async with http.get(f"{base_url}/feishu/stats/monthly", cookies=ck_a) as resp:
                assert resp.status == 200
                mine = await resp.json()
            assert mine["count"] == 1, mine
            assert mine["reply"] == 1, f"只回了话、没写 todo 的会话必须算进本月: {mine}"
            assert mine["checklist"] == 0
            assert mine["month"] == THIS_MONTH, mine

            # 只数自己的: B 那边是 0。
            async with http.get(f"{base_url}/feishu/stats/monthly", cookies=ck_b) as resp:
                assert (await resp.json())["count"] == 0

            # 换成另一个月 → 那条问答不在窗口里, 归零(切月真的生效, 不是恒等于总数)。
            async with http.get(f"{base_url}/feishu/stats/monthly?month=2026-01", cookies=ck_a) as resp:
                assert (await resp.json())["count"] == 0
    finally:
        await runner.cleanup()
        for sid in created:
            with anyio.CancelScope(shield=True):
                await sm.delete(sid)
        await aim.delete("ai1")
        await tg.__aexit__(None, None, None)
