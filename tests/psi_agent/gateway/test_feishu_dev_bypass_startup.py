"""守开发旁路的提示从页面挪到**启动期**后仍然在。

改动前后的差别不是「少了一条通栏」而是「痕迹在哪」:

* 改动前: 页面一条常驻通栏 + **每次登录**一条 WARNING。启动时**什么都不打** —— 于是
  「这个进程开着旁路」在没人登录之前完全不可见。
* 改动后: 装配飞书这条线时打一条启动期 WARNING(开发者启动就看得见), 每次登录那条**保留**
  (旁路实际被用了的痕迹), 页面不再渲染任何东西。

所以「只删前端」是个真会踩的错法: 通栏没了、启动也不提示, 旁路彻底静默。下面第一条用例正是
钉这个 —— 它在删掉 ``warn_if_dev_bypass_enabled()`` 调用后会红。
"""

from __future__ import annotations

import os
from pathlib import Path

import anyio
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from loguru import logger

from psi_agent.gateway.feishu._auth import (
    DEV_OPEN_ID_ENV,
    FeishuAuth,
    Identity,
    dev_open_id,
)
from psi_agent.gateway.feishu._routes import register_auth_routes, register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager

_WEB = Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web"


class _Captured:
    """收 loguru 的 WARNING 文本。

    用 ``logger.add`` 而不是 pytest 的 ``caplog``: 本仓库用 loguru, 它默认不走 stdlib
    logging, ``caplog`` 会**一条都收不到**而断言「没有告警」的用例照样绿 —— 假阴性。
    既有 ``test__agent_events.py`` 用的也是这个写法。
    """

    def __init__(self) -> None:
        self.messages: list[str] = []
        self._handle = logger.add(lambda m: self.messages.append(m.record["message"]), level="WARNING")

    def stop(self) -> str:
        logger.remove(self._handle)
        return "\n".join(self.messages)


async def _assemble_feishu(tmp_path: str) -> None:
    """装配飞书这条线一次 —— 启动期告警就该在这时候打。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    try:
        aim = AIManager(_prefix="gw-test", _tg=tg)
        sm = SessionManager(_aim=aim, _prefix="gw-test", _tg=tg)
        register_feishu_routes(
            await create_core_app(aim, sm, TitleManager(), appdata=os.path.join(tmp_path, "appdata")),
            feishu_ai_id="ai1",
            feishu_workspace_root=os.path.join(tmp_path, "ws"),
        )
    finally:
        tg.cancel_scope.cancel()
        await tg.__aexit__(None, None, None)


@pytest.mark.anyio
async def test_startup_warns_when_dev_bypass_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """设了 ``PSI_FEISHU_DEV_OPEN_ID`` → 装配时就有一条带 open_id 的 WARNING。

    页面通栏撤掉之后这是开发者唯一的提示, 缺了它旁路就完全静默 (删掉
    ``register_feishu_routes`` 里那行 ``warn_if_dev_bypass_enabled()`` 本条即红)。
    """
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "ou_devtest_001")
    cap = _Captured()
    try:
        await _assemble_feishu(str(tmp_path))
    finally:
        blob = cap.stop()
    assert "ou_devtest_001" in blob, f"启动告警没带 open_id, 看不出是谁: {blob!r}"
    assert DEV_OPEN_ID_ENV in blob, "告警没点名是哪个环境变量开的"
    # 「别用于生产」这层意思必须在: 只说「已启用」的话读日志的人未必知道它有多危险。
    assert "production" in blob.lower(), f"告警没说不要用于生产: {blob!r}"


@pytest.mark.anyio
async def test_startup_is_silent_when_dev_bypass_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """没设就一声不响 —— 生产上这条不该出现, 否则告警天天在就等于没有。"""
    monkeypatch.delenv(DEV_OPEN_ID_ENV, raising=False)
    cap = _Captured()
    try:
        await _assemble_feishu(str(tmp_path))
    finally:
        blob = cap.stop()
    assert "dev bypass" not in blob.lower(), f"没开旁路却报了旁路: {blob!r}"
    assert DEV_OPEN_ID_ENV not in blob


def test_dev_open_id_is_a_pure_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """``dev_open_id()`` 只读值, **不打日志**。

    读值与留痕分开之后, 「谁在什么时机留痕」才看得见: 否则任何一处顺手读一下这个值都会多出
    一条同义告警, 而真正该留痕的两个时机(启动 / 每次旁路登录)混在噪音里。
    """
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "ou_pure_read")
    cap = _Captured()
    try:
        assert dev_open_id() == "ou_pure_read"
    finally:
        blob = cap.stop()
    assert blob == "", f"dev_open_id() 打了日志: {blob!r}"


@pytest.mark.anyio
async def test_each_bypass_login_still_leaves_a_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """每次旁路登录仍留一条 WARNING —— 与启动那条并存, 是有意的重复。

    启动那条只说明「开机时开关是开的」; 这条才是旁路**实际被用了**的痕迹。生产上旁路默认
    关着, 这行一次都不会打, 所以「刷日志」的代价在生产等于零; 而万一被误开, 刷出来的量正是
    要的信号。
    """
    monkeypatch.setenv(DEV_OPEN_ID_ENV, "ou_devtest_001")
    app = web.Application()
    app["feishu_auth"] = FeishuAuth()
    register_auth_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    cap = _Captured()
    try:
        for _ in range(2):
            resp = await client.post("/feishu/auth/login", json={})
            assert resp.status == 200
    finally:
        blob = cap.stop()
        await client.close()
    assert blob.lower().count("dev bypass") >= 2, f"两次旁路登录没各留一条痕迹: {blob!r}"
    assert "ou_devtest_001" in blob


def test_frontend_no_longer_renders_a_bypass_banner() -> None:
    """前端不再有通栏: 组件、prop 链、样式类三处都清掉。

    留着任意一处的表现是「代码在但永不渲染」, 下一个人看不出这是撤掉了还是接线断了。
    """
    app_tsx = (_WEB / "src" / "App.tsx").read_text(encoding="utf-8")
    css = (_WEB / "src" / "styles.css").read_text(encoding="utf-8")

    assert "DevBypassBanner" not in app_tsx, "组件还在"
    assert "devBypassOpenId" not in app_tsx, "prop 链还在"
    # 样式类只允许出现在注释里(说明它撤了), 不能再有选择器。
    assert ".ht-dev-bypass {" not in css, "样式选择器还在"
    assert 'className="ht-dev-bypass"' not in app_tsx


def test_backend_via_dev_bypass_field_is_kept() -> None:
    """后端 ``via_dev_bypass`` **保留** —— 撤掉一个渲染用法不等于这个判断没人要了。

    它是 ``login``/``me`` 响应形状的一部分, 也是「这个登录态是不是真身份」在服务端的唯一
    记录。这条与 ``test_feishu_auth_routes.py`` 里那两条形状用例一起守着它。
    """
    assert Identity(open_id="x", name="x").via_dev_bypass is False
    assert Identity(open_id="x", name="x", via_dev_bypass=True).via_dev_bypass is True
