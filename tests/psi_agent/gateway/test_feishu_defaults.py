"""守「网页应用与机器人用同一个模型」—— ``GET /feishu/defaults`` 与前端不碰 ``/ais``。

为什么值得一组用例: 原先前端自己打 ``GET /ais`` 取 ``ais[0].id``。生产上恰好只有一条 AI,
所以**这个缺陷在生产上看不出来** —— 而 appdata 里存了多条时数组顺序无保证, 网页应用会静默
挂上一个与机器人不同的模型。会话照样能建、能聊, 只是模型换了人, 没有任何报错。

判据因此分两层:

* **行为层**(下面前几条): 后端给的 ``ai_id`` 必须是 ``--feishu-ai-id`` 指定的那一个, 且在
  ``/ais`` 里有多条、指定的那条**不是第一条**时仍然准。只有一条 AI 时任何实现都能通过, 那
  样的用例守不住东西。
* **结构层**(最后几条): 前端源码里不能再出现 ``listAis`` / ``/ais``。缺了这层, 判据回到
  「后端给了唯一答案, 但前端可以不用」——靠纪律, 而纪律正是原缺陷的成因。
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio
import pytest
from aiohttp import ClientSession, ClientTimeout

from psi_agent.gateway.feishu._feishu_manager import FeishuManager
from psi_agent.gateway.feishu._routes import register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager
from tests.integration.test_gateway import _start_app_on_free_port

_WEB = Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web"

_AI_KEYS = ("api_key", "base_url", "provider", "model")
"""一个都不许出现在 ``/feishu/defaults`` 的响应里 —— 下发即等于摊给每个 B 端访问者。"""


def test_default_ai_id_is_the_configured_one() -> None:
    """``FeishuManager.default_ai_id`` 就是注入的 ``feishu_ai_id``, 不是别的来源。

    ``_sm=None`` 是刻意的: 本判据只读 ``_ai_id``, 传真 SessionManager 会把「没碰过它」
    这件事藏起来。抑制注释必须写成 ``ty: ignore`` —— ty 不认 mypy 的 ``type: ignore``。
    """
    fm = FeishuManager(_sm=None, _ai_id="ai-bot")  # ty: ignore[invalid-argument-type]
    assert fm.default_ai_id == "ai-bot"
    assert FeishuManager(_sm=None).default_ai_id == ""  # ty: ignore[invalid-argument-type]


@pytest.mark.anyio
async def test_defaults_names_configured_ai_not_first_of_ais(tmp_path: str) -> None:
    """**多条 AI 时**, ``/feishu/defaults`` 给的是 ``--feishu-ai-id`` 那条, 不是 ``/ais`` 的第一条。

    这条是本文件的核心判据, 也是原缺陷唯一暴得出来的形状: 注册三条 AI, 让指定的那条
    (``ai-bot``) 排在**最后**, 于是「取数组第一个」会拿到 ``ai-zzz-first`` —— 与正确答案不同。
    只注册一条 AI 的话两种实现给出同一个值, 用例就白跑了。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = register_feishu_routes(
        await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(str(tmp_path), "appdata")),
        feishu_ai_id="ai-bot",
        feishu_workspace_root=os.path.join(str(tmp_path), "ws"),
    )
    base_url, runner = await _start_app_on_free_port(app)
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as http:
            # 顺序有意: 指定的 ``ai-bot`` 夹在中间且不是第一条。
            for ai_id in ("ai-zzz-first", "ai-bot", "ai-mmm-last"):
                async with http.post(
                    f"{base_url}/ais",
                    json={
                        "provider": "openai",
                        "model": "placeholder-model",
                        "api_key": "sk-test",
                        "base_url": "https://api.example.com",
                        "id": ai_id,
                    },
                ) as resp:
                    assert resp.status == 201

            # 前提核对: /ais 真有多条, 且第一条不是我们指定的那条。否则下面那条断言
            # 会「因为只有一条所以通过」, 守不住任何东西。
            async with http.get(f"{base_url}/ais") as resp:
                assert resp.status == 200
                listed = await resp.json()
            rows = listed if isinstance(listed, list) else listed.get("value", [])
            ids = [r["id"] for r in rows]
            assert len(ids) >= 3, f"/ais 只有 {ids}, 本用例需要多条才吃劲"
            assert ids[0] != "ai-bot", f"ai-bot 恰好排在第一位 ({ids}), 本用例失去判别力"

            async with http.get(f"{base_url}/feishu/defaults") as resp:
                assert resp.status == 200
                body = await resp.json()
            assert body["ai_id"] == "ai-bot", (
                f"网页应用会用 {body['ai_id']!r} 建会话, 而机器人用的是 --feishu-ai-id 指定的 "
                f"'ai-bot' —— 两侧模型不一致 (/ais 顺序是 {ids})"
            )

            # 只下发 id: 凭证与模型名一个都不给。
            assert set(body) == {"ai_id"}, f"响应多带了字段: {sorted(set(body) - {'ai_id'})}"
            for key in _AI_KEYS:
                assert key not in body

            # 建会话拿到的 backend_id 与它一致 —— 端点给的答案真的被用上了。
            async with http.post(f"{base_url}/feishu/sessions", json={"backend_id": body["ai_id"]}) as resp:
                assert resp.status == 401  # 未登录: 这里只确认路由存在, 归属另有用例
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_defaults_is_empty_string_when_deployment_configured_none(tmp_path: str) -> None:
    """``--feishu-ai-id`` 为空 → ``{"ai_id": ""}`` 且 **200**, 不是 404 也不是 500。

    空串是「这次部署没配 AI」的正确表达: 前端据此显示可读提示 (指向部署配置), 而 404/500
    在前端只能变成一句语义不明的报错。**不回落到 /ais 里随便挑一条** —— 那正是要消灭的行为。
    """
    tg = anyio.create_task_group()
    await tg.__aenter__()
    aim = AIManager(_prefix="gw-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
    app = register_feishu_routes(
        await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(str(tmp_path), "appdata")),
        feishu_workspace_root=os.path.join(str(tmp_path), "ws"),
    )
    base_url, runner = await _start_app_on_free_port(app)
    try:
        async with ClientSession(timeout=ClientTimeout(total=10)) as http:
            # 库里**有**一条 AI, 但部署没指名 —— 回落实现会在这里给出 'ai1'。
            async with http.post(
                f"{base_url}/ais",
                json={
                    "provider": "openai",
                    "model": "placeholder-model",
                    "api_key": "sk-test",
                    "base_url": "https://api.example.com",
                    "id": "ai1",
                },
            ) as resp:
                assert resp.status == 201

            async with http.get(f"{base_url}/feishu/defaults") as resp:
                assert resp.status == 200
                body = await resp.json()
            assert body == {"ai_id": ""}, "部署未指名时不该替它挑一个 —— 静默换模型比报错难查"
    finally:
        await runner.cleanup()
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


def test_frontend_has_no_ai_list_concept() -> None:
    """前端源码里不能再出现 ``listAis`` / ``AiInfo`` / ``/ais``。

    结构判据: 「网页应用与机器人用不同模型」要在**结构上不可能**, 而不是靠后端给了唯一答案、
    前端自觉去用。任何一处写回 ``/ais`` 都让缺陷复活, 且复活后在只有一条 AI 的生产环境上看
    不出来 —— 所以只能靠这条钉住。
    """
    src = _WEB / "src"
    assert src.is_dir(), f"找不到前端源码目录 {src}"
    files = sorted(p for p in src.rglob("*.ts") if p.is_file()) + sorted(p for p in src.rglob("*.tsx") if p.is_file())
    assert files, "一个前端源文件都没扫到, 本用例的判据失效了"

    offenders: list[str] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//", 1)[0]  # 注释里提这些名字是解释历史, 不算调用
            if "*" in line and "//" not in line:
                code = ""  # 块注释行
            if "listAis" in code or "AiInfo" in code or '"/ais' in code or "'/ais" in code:
                offenders.append(f"{path.relative_to(_WEB)}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "前端又有了「AI 列表」的概念:\n"
        + "\n".join(offenders)
        + "\n建会话该挂哪个 AI 只能问 GET /feishu/defaults (api.ts 的 getFeishuDefaultAiId)。"
    )


def test_frontend_asks_defaults_endpoint() -> None:
    """正向判据: ``api.ts`` 里确实打 ``/feishu/defaults``, 且 ``useSessions`` 用它。

    没有这条时上一条可以「把两处都删光」而通过 —— 那样新建会话直接坏掉。
    """
    api = (_WEB / "src" / "api.ts").read_text(encoding="utf-8")
    assert "/feishu/defaults" in api, "api.ts 不再请求 /feishu/defaults, 那 ai_id 从哪来?"
    hook = (_WEB / "src" / "hooks" / "useSessions.ts").read_text(encoding="utf-8")
    assert "getFeishuDefaultAiId" in hook, "useSessions 没用后端给的缺省 AI"


def test_no_ai_message_points_at_deployment_not_user() -> None:
    """没配 AI 时的文案必须指向**部署配置/管理员**, 不能让 B 端用户以为自己该去配模型。

    飞书是 ToB: AI 由部署者用 ``--feishu-ai-id`` 定死, 用户既看不见也改不了。旧文案
    「没有可用模型, 无法新建会话」读起来像用户自己该去配点什么, 于是这条报错会被当成用户
    操作问题而不是部署问题 —— 排查方向一开始就错了。
    """
    hook = (_WEB / "src" / "hooks" / "useSessions.ts").read_text(encoding="utf-8")
    assert "feishu-ai-id" in hook, "文案没点名 --feishu-ai-id, 看到的人不知道该去配哪个参数"
    assert "管理员" in hook or "部署" in hook, "文案没指向部署方/管理员"
