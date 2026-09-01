"""守「前端会打的路径」与「后端真有这条路由」不脱节。

## 为什么值得这些用例

云上拓扑是 Caddy → `oauth-proxy.py`(**白名单**反代) → gateway 容器。白名单少一条,
那条路径就静默 404, 而前端只显示一个笼统的加载失败。**本地直连 gateway 时这些路径全通**,
所以本地测得再全也碰不到这类失败 —— 差异面本身此前没有任何东西守着。

清单不能人手抄: 前端加一个端点没人会想起来更新它, 而漂移的表现恰好是云上 404。所以清单由
`scripts/feishu_web_paths.py` 从源码提取, 这里把两个方向都钉住:

* **源码多一条而清单没更新 → 红**(`test_manifest_covers_every_frontend_call`)
* **清单多一条而源码没有 → 红**(`test_manifest_has_no_stale_entries`)

单向绑定不够: 只查前者时, 从清单里删一条不会红(源码那条还在, 但清单说没有 —— 于是那条永远
不会被拿去核对白名单)。

再往下一层, `test_extract_is_not_fooled_by_nested_generics` 是**变异复核**: 提取器只要少提
一条, 上面两条依旧全绿(清单是它生成的, 两边一起错)。

## 判据是路由存在性, 不是状态码为 200

`/feishu/sessions` 一族未登录时是 **401**, 写成 `== 200` 会因为没带身份而假红。而
`/sessions/{id}/todos` 拿哨兵 id 打会回 handler 自己判出的 **404**(会话不存在) —— 与
「路由不存在」的 404 必须分开, 前者 `application/json`, 后者 aiohttp 的 `text/plain`
`404: Not Found`。`classify()` 就是这条判据。
"""

from __future__ import annotations

import importlib.util
import re
import socket
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

import anyio
import anyio.abc
import pytest
from aiohttp import ClientSession, ClientTimeout, web

from psi_agent.gateway.desktop._routes import register_desktop_routes
from psi_agent.gateway.feishu._routes import register_feishu_routes
from psi_agent.gateway.server import create_core_app
from psi_agent.runtime._ai_manager import AIManager
from psi_agent.runtime._session_manager import SessionManager
from psi_agent.runtime._title_manager import TitleManager

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "feishu_web_paths.py"


def _load() -> ModuleType:
    """脚本在 `scripts/` 下不属包, 按路径加载 —— 与 `test_gen_legal_html.py` 同款做法。"""
    spec = importlib.util.spec_from_file_location("feishu_web_paths", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_M = _load()


def _fmt(entries: Iterable[Any]) -> str:
    """`PathEntry` 住在按路径加载的脚本里, 没有可 import 的静态类型, 故标 `Any`。"""
    return ", ".join(f"{e.method} {e.path}" for e in entries)


# ---- 清单与源码的双向绑定 -----------------------------------------------


def test_script_and_manifest_exist() -> None:
    """存在性先钉住 —— 否则下面几条会「没解析出东西所以通过」。"""
    assert _SCRIPT.is_file(), f"找不到 {_SCRIPT}"
    assert _M.MANIFEST.is_file(), f"找不到清单 {_M.MANIFEST}, 跑 --regenerate 生成"
    assert _M.extract_paths(), "从前端源码一条路径都没提出来, 说明提取器已失效"


def test_manifest_covers_every_frontend_call() -> None:
    """前端新增一个 API 调用而清单没更新 → 这条红。整张卡的核心判据。"""
    missing, _ = _M.diff_manifest()
    assert missing == [], (
        f"前端在打这些路径, 但 {_M.MANIFEST.name} 里没有: {_fmt(missing)}。\n"
        "云上 oauth-proxy 的 ALLOWED_PATHS 靠这份清单核对, 漏一条就是那条路径静默 404、"
        "前端只显示笼统的加载失败。跑 `python scripts/feishu_web_paths.py --regenerate` 同步。"
    )


def test_manifest_has_no_stale_entries() -> None:
    """反向: 清单里有而前端已经不打了 → 也红。

    没有这条时「从清单里删一条」不会被发现 —— 而被删的那条恰好就是不会去核对白名单的那条。
    """
    _, stale = _M.diff_manifest()
    assert stale == [], (
        f"{_M.MANIFEST.name} 里这些路径前端已经不打了: {_fmt(stale)}。\n"
        "清单是描述现状的, 多出来的条目会让白名单核对放行不需要的路径。跑 --regenerate 同步。"
    )


def test_extract_is_not_fooled_by_nested_generics() -> None:
    """变异复核: 提取器少提一条时, 上面两条不会红(清单是它生成的, 两边一起错)。

    `requestJson<Record<string, string>>` 的类型参数自己带一层 `>`, 用 `<[^>]*>` 会停在
    里层那个 `>` 上, 于是 `/feishu/titles` 与 `/feishu/summaries` 被**静默漏掉**。实测过:
    朴素正则只提到 15 条, 真实是 19 条。这条拿真实源码钉住那几条必须在。
    """
    paths = {e.path for e in _M.extract_paths()}
    for expected in ("/feishu/titles", "/feishu/summaries"):
        assert expected in paths, (
            f"{expected} 没被提取到 —— 提取器大概退回了 `<[^>]*>` 这种非贪婪写法, "
            "它会被嵌套泛型 `Record<string, string>` 骗过去。清单于是静默少条, 全绿。"
        )
    # 模板字面量里的 `${...}` 要归一成参数, 且查询串不进路径。
    assert "/sessions/{param}/chat" in paths, "模板字面量里的路径没被归一"
    assert "/workspace/file" in paths, "带查询串的路径没被截掉 `?` 之后的部分"


def test_every_http_call_site_lives_in_a_scanned_file() -> None:
    """有人在第三个文件里直接 `fetch(` → 红。

    提取器只读 `SOURCE_FILES` 两个文件。多一个发请求的文件时它不报错, 只是少提一条:
    清单齐全、测试全绿、云上照旧 404。所以取材范围本身也要有判据。
    """
    scanned = set(_M.SOURCE_FILES)
    strays = [(f, line, ctor) for f, line, ctor in _M.http_call_sites() if f not in scanned]
    assert strays == [], (
        "这些位置在发 HTTP 请求, 但不在提取器的取材范围里: "
        + ", ".join(f"{f}:{line} ({ctor})" for f, line, ctor in strays)
        + f"\n它们打的路径不会进清单, 于是核对白名单时漏掉。要么把请求收敛回 {scanned}, "
        "要么把该文件加进 scripts/feishu_web_paths.py 的 SOURCE_FILES。"
    )


def test_manifest_paths_are_absolute_and_parameterized() -> None:
    """形状判据: 必须是绝对路径, 不带查询串, 参数写成 `{...}`。"""
    for entry in _M.load_manifest():
        assert entry.path.startswith("/"), f"{entry.path} 不是绝对路径"
        assert "?" not in entry.path, f"{entry.path} 带了查询串, 那不是路由的一部分"
        assert "$" not in entry.path, f"{entry.path} 里还留着模板插值, 没归一成 {{param}}"
        assert entry.method in ("GET", "POST", "DELETE", "PUT", "PATCH"), entry.method


# ---- 与真实路由表比对(不起 HTTP) ----------------------------------------


def _normalize(path: str) -> str:
    """`/sessions/{session_id}` 与 `/sessions/{param}` 归一成同一个形状。

    前端那侧是 `${encodeURIComponent(id)}`, 没有参数名可取, 所以只能按位置比对。
    """
    return re.sub(r"\{[^}]*\}", "{}", path)


async def _both_surfaces_app(tg: anyio.abc.TaskGroup) -> web.Application:
    """**两面全挂** —— 与生产一致。

    `--gateway feishu` 单挂时 `/workspace/*` 不注册(那两条 handler 住在
    `desktop/_routes.py`), 而生产的 `launch-gateway.sh` 两面都挂。测单挂会把一个
    「本地拓扑与云上不同」错报成「前端打了不存在的路由」。这个差异由
    `test_workspace_paths_need_the_desktop_surface` 单独记着。
    """
    aim = AIManager(_prefix="api-paths-test", _tg=tg)
    sm = SessionManager(_aim=aim, _prefix="api-paths-test", _tg=tg)
    app = await create_core_app(aim, sm, TitleManager())
    app = await register_desktop_routes(app, app_name="Haitun Agent")
    return register_feishu_routes(app)


def _canonical_routes(app: web.Application) -> set[tuple[str, str]]:
    return {
        (route.method.upper(), _normalize(route.resource.canonical))
        for route in app.router.routes()
        if route.resource is not None and route.method.upper() != "HEAD"
    }


@pytest.mark.anyio
async def test_every_manifest_path_is_a_registered_route() -> None:
    """清单每条都能在真实路由表里找到 —— 比打 HTTP 更直接, 且不受身份影响。"""
    async with anyio.create_task_group() as tg:
        app = await _both_surfaces_app(tg)
        registered = _canonical_routes(app)
        unmatched = [entry for entry in _M.load_manifest() if (entry.method, _normalize(entry.path)) not in registered]
        tg.cancel_scope.cancel()
    assert unmatched == [], (
        f"前端会打这些路径, 但两面全挂的 app 里没有对应路由: {_fmt(unmatched)}。\n"
        "这是「前端打了不存在的后端路由」, 本地就会 404, 不用等上云。"
    )


@pytest.mark.anyio
async def test_workspace_paths_need_the_desktop_surface() -> None:
    """记住一条真实的本地/云上差异: `/workspace/*` 归 desktop 那面。

    `--gateway feishu` 单挂(本地开发文档里的起法)时这两条**路由不存在**, 而 ToB 前端
    的交付物抽屉在打它们。生产两面全挂所以能通 —— 于是「本地单挂能重现的 404」和
    「云上白名单缺条的 404」长得一样, 排查时容易认错。这条把归属钉死: 归属变了(比如把
    handler 搬进骨架或 feishu 包)就该更新文档里那张差异表。
    """
    manifest_workspace = {e.path for e in _M.load_manifest() if e.path.startswith("/workspace/")}
    assert manifest_workspace, "前端不再打 /workspace/* 了? 那就把这条用例与文档里那行一起删掉"

    async with anyio.create_task_group() as tg:
        aim = AIManager(_prefix="api-paths-feishu-only", _tg=tg)
        sm = SessionManager(_aim=aim, _prefix="api-paths-feishu-only", _tg=tg)
        feishu_only = register_feishu_routes(await create_core_app(aim, sm, TitleManager()))
        paths = {_normalize(p) for _, p in _canonical_routes(feishu_only)}
        tg.cancel_scope.cancel()

    absent = sorted(p for p in manifest_workspace if _normalize(p) not in paths)
    assert absent == sorted(manifest_workspace), (
        "`--gateway feishu` 单挂时 /workspace/* 竟然有了 —— 归属变了。"
        "更新 feishu-web/AGENTS.md 里那张本地/云上差异表, 别让文档留着过期的坑。"
    )


# ---- 本地逐条打(真 HTTP) ------------------------------------------------


async def _start_on_free_port(app: web.Application) -> tuple[str, web.AppRunner]:
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    await web.SockSite(runner, sock).start()
    return f"http://127.0.0.1:{port}", runner


@pytest.mark.anyio
async def test_no_path_is_router_404_on_a_live_gateway() -> None:
    """真起一个 gateway, 清单逐条打, 断言**没有路由级 404**。

    刻意**不**断言 200: `/feishu/*` 一族未登录是 401, 哨兵 session id 打骨架那几条是
    handler 自己判出的 404(json), `POST /titles` 缺字段是 400。这些都说明路由在。
    """
    async with anyio.create_task_group() as tg:
        app = await _both_surfaces_app(tg)
        base_url, runner = await _start_on_free_port(app)
        rows: list[dict[str, object]] = []
        try:
            timeout = ClientTimeout(total=10)
            async with ClientSession(timeout=timeout) as http:
                for entry in _M.load_manifest():
                    url = base_url + entry.probe_path()
                    # Any 而非 object: ``**kwargs`` 展开时会去比对 request() 的每一个具名
                    # 参数, object 于是对着二十多个参数各报一次不兼容。
                    kwargs: dict[str, Any] = {}
                    if entry.method in ("POST", "PUT", "PATCH"):
                        kwargs["json"] = {}
                    async with http.request(entry.method, url, **kwargs) as resp:
                        body = await resp.text()
                    rows.append(
                        {
                            "method": entry.method,
                            "path": entry.path,
                            "status": resp.status,
                            "verdict": _M.classify(resp.status, resp.content_type, body),
                        }
                    )
        finally:
            await runner.cleanup()
            tg.cancel_scope.cancel()

    missing = [r for r in rows if r["verdict"] != "present"]
    assert missing == [], (
        "这些路径在本机 gateway 上就是路由级 404: "
        + ", ".join(f"{r['method']} {r['path']}" for r in missing)
        + "\n(判据是路由存在性: 401/400/405 与 handler 自己回的 json 404 都算路由在。)"
    )


def test_every_manifest_path_is_proxied_by_the_dev_server() -> None:
    """清单每条都得被 `vite.config.ts` 的 proxy 表覆盖到, 否则 `npm run dev` 下那条 404。

    proxy 表是**前缀**粒度、服务于 dev server, 与清单的**精确路径**不是一回事, 所以不合并
    成一处事实源。但两者漂移会出问题: 前端新增一个 `/foo` 端点时清单会自动带上它(提取器
    读源码), proxy 表却要人手加 —— 漏了的话 dev server 把 `/foo` 当前端路由处理, 回的是
    `index.html` 或 404, 而**上云反而是好的**(同源, 不过 proxy)。这条方向与云上那些判据相反:
    只在本地发作。

    判定复刻 vite 的语义: `^` 开头当正则, 否则前缀匹配(见 `test_feishu_web_dev_proxy.py`)。
    """
    config = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "psi_agent"
        / "gateway"
        / "feishu"
        / "feishu-web"
        / "vite.config.ts"
    )
    text = config.read_text(encoding="utf-8")
    keys = re.findall(r"^\s*'([^']+)'\s*:", text[text.index("proxy:") :], re.MULTILINE)
    assert keys, "proxy 表里一个 key 都没解析出来, 本用例的判据已失效"

    uncovered = []
    for entry in _M.load_manifest():
        probe = entry.probe_path()
        hit = any((re.match(key, probe) is not None) if key.startswith("^") else probe.startswith(key) for key in keys)
        if not hit:
            uncovered.append(entry)
    assert uncovered == [], (
        f"这些路径没被 vite.config.ts 的 proxy 表覆盖: {_fmt(uncovered)}。\n"
        f"当前 proxy key: {keys}\n"
        "`npm run dev` 下它们不会转给 gateway, 那条请求在本地直接失败(云上反而正常, "
        "因为同源不过 proxy)。往 proxy 表加对应前缀。"
    )


def test_classify_separates_router_404_from_handler_404() -> None:
    """`classify` 自己的判据 —— 上一条全靠它, 它错了那条会假绿。

    aiohttp 路由不到时回 `text/plain` 的 `404: Not Found`; handler 判出的「会话不存在」
    走 `_error()`, 是 `application/json`。两者状态码相同, 只有 content-type 分得开。
    """
    assert _M.classify(404, "text/plain", "404: Not Found") == "missing"
    assert _M.classify(404, "application/json", '{"error": "Session \'x\' not found"}') == "present"
    assert _M.classify(401, "application/json", '{"error": "not logged in"}') == "present"
    assert _M.classify(405, "text/plain", "405: Method Not Allowed") == "present"
    assert _M.classify(200, "application/json", "[]") == "present"
