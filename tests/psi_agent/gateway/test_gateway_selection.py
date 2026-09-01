"""``--gateway`` 各组合分别挂哪些路由。

判据对着的是两类静默故障:

1. **少挂一面是静默的** —— 少挂一面不报错, 只是某个前端 404, 排查方向完全跑偏。所以该参数
   **必填**: 把这个静默失败提前成启动期的显式失败 (不给就非 0 退出, 而不是替调用方猜一个
   默认值)。故既查「没有默认值」, 又查不传时 CLI 真的退出。
2. **``/oauth/*`` 漏挂** —— 回调地址登记在第三方应用后台, 不随本进程挂了哪些 gateway 而变;
   少这两条的表现是用户点完授权拿 404, 而不是某个功能没开。故每种组合都验。

这里不起 ``Gateway.run`` (它要建 socket、恢复 state、可能连云端), 只调它内部那段装配 ——
``--gateway`` 影响的正好是这一段, 见 ``gateway/__init__.py``。
"""

from __future__ import annotations

import dataclasses
from dataclasses import fields

import anyio
import pytest
import tyro
from aiohttp import web

from psi_agent.gateway import ALL_GATEWAYS, Gateway, _redirect_to_feishu_web, resolve_gateways
from psi_agent.gateway.desktop._routes import register_desktop_routes
from psi_agent.gateway.feishu._routes import register_feishu_routes, register_oauth_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager

_COMBINATIONS = (("desktop", "feishu"), ("desktop",), ("feishu",))


async def _assemble(*gateways: str) -> web.Application:
    """复刻 ``Gateway.run`` 的装配段。与那边同构 —— 改一边忘另一边测的就不是生产路径。"""
    tg = anyio.create_task_group()
    await tg.__aenter__()
    tag = "-".join(gateways)
    aim = AIManager(_prefix=f"gw-test-{tag}", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix=f"gw-test-{tag}", _tg=tg)
    app = await create_core_app(aim, sm, TitleManager())

    want_desktop = "desktop" in gateways
    want_feishu = "feishu" in gateways
    if want_desktop:
        await register_desktop_routes(app)
    if want_feishu:
        register_feishu_routes(app)
        if not want_desktop:
            app.router.add_get("/", _redirect_to_feishu_web)
    else:
        register_oauth_routes(app)
    tg.cancel_scope.cancel()
    await tg.__aexit__(None, None, None)
    return app


def _paths(app: web.Application) -> set[str]:
    return {getattr(res, "canonical", "") for res in app.router.resources()}


def test_the_gateway_field_has_no_default() -> None:
    """``--gateway`` **没有默认值** —— 挂哪些面是部署方的决定, 内核不替它猜。

    守的是「有人图省事又加回一个默认值」: 那会把「少挂一面」退回成静默 404。这里只查字段,
    用户可见的那半由 ``test_omitting_gateway_exits_nonzero`` 走真 CLI 验。

    实测: 用 ``tyro.MISSING`` 表达必填 (字段前面全是带默认值的字段, 真省掉默认值会撞
    dataclass 的字段顺序限制)。于是 ``default`` 是 ``tyro.MISSING`` 而 **不是**
    ``dataclasses.MISSING`` —— 只查后者会误判成「有默认值」, 两个都得查。
    """
    f = next(f for f in fields(Gateway) if f.name == "gateway")
    assert f.default is tyro.MISSING
    assert f.default is not dataclasses.MISSING
    assert f.default_factory is dataclasses.MISSING
    # 取值域全集还在 (resolve_gateways 的报错文案在用), 只是不再是默认值。
    assert list(ALL_GATEWAYS) == ["desktop", "feishu"]


def test_omitting_gateway_exits_nonzero() -> None:
    """不传 ``--gateway`` 时 CLI 退出, 且报错说清缺什么、可选值是什么。

    走 tyro 入口而不是直接构造 dataclass: 直接构造只会拿到 ``gateway=tyro.MISSING`` 这个
    哨兵 (dataclass 眼里它是个正常默认值, 不报错), 验不到用户实际看到的行为 —— 必填这件事
    整个由 tyro 落实, 绕开它就等于什么都没验。

    实测退出码 **2** (tyro 走 argparse 的 usage-error 路径), 只断言非 0: 判据是「起不来」,
    具体码值属 tyro 实现细节。
    """
    with pytest.raises(SystemExit) as exc:
        tyro.cli(Gateway, args=[])
    assert exc.value.code != 0


def test_gateway_accepts_values_when_given() -> None:
    """必填只改「不传时的行为」: 传了之后照原样收下, 三种组合都不受影响。

    ``--gateway`` 后不跟值仍是 ``[]`` 且**不报错** (实测: tyro 认为参数给过了), 所以
    ``resolve_gateways`` 里那条空列表拦截仍然是唯一防线, 没被必填替掉。
    """
    assert tyro.cli(Gateway, args=["--gateway", "desktop"]).gateway == ["desktop"]
    assert tyro.cli(Gateway, args=["--gateway", "feishu"]).gateway == ["feishu"]
    both = tyro.cli(Gateway, args=["--gateway", "desktop", "feishu"])
    assert both.gateway == ["desktop", "feishu"]
    assert tyro.cli(Gateway, args=["--gateway"]).gateway == []


@pytest.mark.anyio
async def test_both_gateways_together_mount_both_sides() -> None:
    """``--gateway desktop feishu`` 两面全挂时各自的路由一条不少。"""
    # 只列与 ``dist/`` 无关的那些 —— 静态挂载点见
    # test_dropping_one_gateway_drops_only_that_side 的说明 (构建过前端的机器才有)。
    paths = _paths(await _assemble("desktop", "feishu"))
    assert {"/feishu/route", "/feishu/routes"} <= paths
    assert {"/spa", "/spa/", "/spa/index.html", "/spa-v2/index.html"} <= paths
    assert {"/ui/attention", "/workspace/cwd"} <= paths
    assert {"/oauth/callback", "/oauth/code"} <= paths


@pytest.mark.anyio
async def test_dropping_one_gateway_drops_only_that_side() -> None:
    """只挂一面: 掉的恰好是另一面那批, 骨架 REST 一条不少。

    按**前缀**判而不是列举具体 path: 静态挂载点 (``/spa-v2`` ``/feishu-web``) 只在对应
    ``dist/`` 存在时才注册, 写死一份集合会让「构建过前端的机器」和「没构建的机器」测出
    不同结果 —— 那种测试只在作者机器上是绿的。
    """
    both = _paths(await _assemble("desktop", "feishu"))
    desktop = _paths(await _assemble("desktop"))
    feishu = _paths(await _assemble("feishu"))

    # 只挂一面不许多出任何东西 (根路径除外, 见下一条测试)。
    assert not desktop - both
    assert not feishu - both

    tob_prefixes = ("/feishu/", "/feishu-web")
    toc_prefixes = ("/spa", "/ui/", "/workspace/")
    # 只挂 desktop 时掉的全是 ToB 那批, 且 ToB 那批一条不留。
    assert all(p.startswith(tob_prefixes) for p in both - desktop)
    assert not [p for p in desktop if p.startswith(tob_prefixes)]
    # 只挂 feishu 反之。
    assert all(p.startswith(toc_prefixes) for p in both - feishu)
    assert not [p for p in feishu if p.startswith(toc_prefixes)]
    # 两面各自的业务路由 (与 dist 无关, 一定在) 确实掉了/留着。
    assert {"/feishu/route", "/feishu/routes"} <= both - desktop
    assert {"/ui/attention", "/workspace/cwd"} <= both - feishu

    # 骨架那批与挂了哪些 gateway 无关, 每种组合下都在。
    for paths in (both, desktop, feishu):
        assert {"/ais", "/sessions", "/titles", "/defaults", "/openapi.json"} <= paths


@pytest.mark.anyio
@pytest.mark.parametrize("gateways", _COMBINATIONS)
async def test_oauth_is_mounted_under_every_combination(gateways: tuple[str, ...]) -> None:
    """``/oauth/*`` 与挂了哪些 gateway 正交 —— 每种组合都在, 且恰好注册一次。"""
    app = await _assemble(*gateways)
    assert {"/oauth/callback", "/oauth/code"} <= _paths(app)
    assert app["oauth"] is not None
    assert app["openapi_oauth"] is True
    # 注册一次: 两面全挂时 register_feishu_routes 已经调过, 别再叠一层同名路由。
    dupes = [r for r in app.router.resources() if getattr(r, "canonical", "") == "/oauth/callback"]
    assert len(dupes) == 1


@pytest.mark.anyio
async def test_feishu_only_root_redirects_to_the_feishu_index_file() -> None:
    """只挂 ToB 时 ``GET /`` 要有明确去处, 且不能指向裸目录。

    实测: ``add_static(..., show_index=False)`` 对 ``/feishu-web/`` 回 **403**, 所以跳
    文件而非目录 —— 跳目录会让 ToB 单挂时的首页变成 403。
    """
    assert "/" in _paths(await _assemble("feishu"))
    resp = await _redirect_to_feishu_web(web.Request.__new__(web.Request))
    assert resp.status == 302
    assert resp.headers["Location"] == "/feishu-web/index.html"


@pytest.mark.anyio
async def test_desktop_only_keeps_the_toc_root_fallback() -> None:
    """只挂 ToC 时 ``GET /`` 仍归 desktop 那条降级链, 不被飞书的重定向顶掉。"""
    app = await _assemble("desktop")
    assert "/" in _paths(app)
    root = next(r for r in app.router.resources() if getattr(r, "canonical", "") == "/")
    handlers = {route.handler for route in root}
    assert _redirect_to_feishu_web not in handlers


def test_an_empty_selection_is_rejected() -> None:
    """``--gateway`` 后不跟值时 tyro 给 ``[]``, 那是起了服务但一个前端都没有。

    实测 tyro 照收不报错 (它认为参数「给过了」, 退出码 0), 所以必填拦不到这一种, 仍得拦在
    这里。报错而不是自己补一个取值: 该挂哪面只有部署方知道, 内核替它猜出来的那面一样是静默
    的 —— 用户显式写了 ``--gateway`` 却被悄悄补齐, 比拿到一个错误更难发现。
    """
    with pytest.raises(ValueError, match="at least one"):
        resolve_gateways([])


def test_repeated_values_are_deduplicated_in_order() -> None:
    """``--gateway feishu feishu`` 实测不报错, 去重 —— 意图无歧义, 但注册两次会叠同名路由。"""
    assert resolve_gateways(["feishu", "feishu"]) == ("feishu",)
    assert resolve_gateways(["feishu", "desktop", "feishu"]) == ("feishu", "desktop")
    # 单个值与全集原样通过, 顺序即传入顺序。
    assert resolve_gateways(["desktop"]) == ("desktop",)
    assert resolve_gateways(list(ALL_GATEWAYS)) == ALL_GATEWAYS
