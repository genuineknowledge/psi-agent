"""`deploy/haitun/oauth-proxy.py` 的判据 —— 假上游 + 真代理, 不 mock 任何一跳。

## 为什么这些判据存在

这个代理是**公网唯一入口**, 它同时承担两件互相拉扯的事:

1. 挡住 gateway 上没有鉴权的核心路由(`/sessions/{id}/chat` 能直接驱动 agent 跑 bash),
2. 放行飞书网页应用需要的那 11 条 API 与静态资源。

于是「漏放行」与「多放行」都是缺陷, 且两者的表现都不响: 漏放行是前端一句笼统的
「加载失败」, 多放行则**根本没有任何症状** —— 直到有人从公网打那条路径。所以白名单
的两侧都要有判据钉住, 见 `test_core_routes_stay_blocked`。

另有一类静默失败**只在代理这一跳发生, 本地直连 gateway 永远碰不到**: 头没转发。
飞书网页应用的登录态全靠 `HttpOnly` cookie(`_routes.py` 的 `SID_COOKIE`), 请求侧
不带 `Cookie` 头、响应侧不回 `Set-Cookie` 头, 表现就是「路由都通了但一直 401」。
`test_request_cookie_reaches_upstream` 与 `test_all_set_cookie_headers_reach_client`
守的是这条。后者**专门断言数量**: `resp.headers.get("Set-Cookie")` 只拿到第一条,
多条时静默丢数据, 而登录 + 清理旧 cookie 恰好就是多条的场合。

## 假上游不是 mock

上游是一个真的 `aiohttp` app, 它把收到的 method/body/headers 如实记下来。判据断言的
是**上游实际收到了什么**, 不是「代理调了什么函数」—— 后者在 `session.request` 的参数
拼错时照样绿。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "haitun" / "oauth-proxy.py"


def _load() -> ModuleType:
    """脚本住在 `deploy/` 下且带连字符, 不属包也不能 import, 故按路径加载。

    与 `tests/psi_agent/gateway/test_feishu_web_api_paths.py` 同款做法。
    """
    spec = importlib.util.spec_from_file_location("oauth_proxy", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_M = _load()

#: 前端会打的路径清单, 由 `scripts/feishu_web_paths.py` 从前端源码提取。
_MANIFEST = (
    Path(__file__).resolve().parents[2] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web" / "api-paths.json"
)


def _manifest_paths() -> list[dict[str, str]]:
    """在同步函数里读盘 —— 协程里碰 `pathlib` 会被 ruff 的 ASYNC240 拦下。"""
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))["paths"]


@dataclass
class _Hit:
    """上游收到的一次请求 —— 判据断言的对象。"""

    method: str
    path: str
    query: dict[str, str]
    body: bytes
    headers: dict[str, str]


@dataclass
class _Upstream:
    """假 gateway。`hits` 为空是「一次都没被调用」的判据(白名单外不许触达上游)。"""

    hits: list[_Hit] = field(default_factory=list)
    #: 下一次响应要下发的 Set-Cookie 值, 每个元素一条独立的头。
    set_cookies: list[str] = field(default_factory=list)
    status: int = 200
    body: bytes = b'{"ok":true}'
    #: 非空则按 SSE 流式响应, 逐块写。每块之间等 `release` 被 set。
    sse_chunks: list[bytes] = field(default_factory=list)
    #: 第一块写完后置位 —— 判据据此知道「上游已经吐了第一块」。
    first_chunk_sent: asyncio.Event = field(default_factory=asyncio.Event)
    #: 判据 set 它之后上游才写剩下的块。**流式的判据全靠这个闩**: 缓冲实现要等上游
    #: 写完才回, 而上游在这里等判据, 判据在等第一块 —— 死等到超时, 于是缓冲必被抓住。
    release: asyncio.Event = field(default_factory=asyncio.Event)


async def _upstream_handler(request: web.Request) -> web.StreamResponse:
    up: _Upstream = request.app["state"]
    up.hits.append(
        _Hit(
            method=request.method,
            path=request.path,
            query=dict(request.query),
            body=await request.read(),
            headers=dict(request.headers),
        )
    )
    if up.sse_chunks:
        return await _upstream_sse(request, up)
    resp = web.Response(status=up.status, body=up.body, content_type="application/json")
    for cookie in up.set_cookies:
        # 逐条 add 而非 set —— 要在一个响应里造出多条 Set-Cookie。
        resp.headers.add("Set-Cookie", cookie)
    return resp


async def _upstream_sse(request: web.Request, up: _Upstream) -> web.StreamResponse:
    """按 SSE 逐块写 —— 真 gateway 的 `_serve_chat_sse` 就是这个形状。"""
    resp = web.StreamResponse(status=up.status, headers={"Content-Type": "text/event-stream"})
    for cookie in up.set_cookies:
        resp.headers.add("Set-Cookie", cookie)
    await resp.prepare(request)
    first, *rest = up.sse_chunks
    await resp.write(first)
    up.first_chunk_sent.set()
    # 判据放行前不写第二块。缓冲的转发层会卡在这里等不到, 从而超时变红。
    await up.release.wait()
    for chunk in rest:
        await resp.write(chunk)
    await resp.write_eof()
    return resp


@dataclass
class _Rig:
    client: TestClient
    upstream: _Upstream


async def _raw_get(port: int, raw_path: str) -> int:
    """裸 socket 发一个 GET, 回状态码 —— 路径**原样**写进请求行, 不被任何客户端归一化。

    `aiohttp` 客户端(以及 `yarl.URL`)会在发出前把 `/a/../b` 解析成 `/b`, 于是路径穿越
    类的判据用它量不到: 请求以已经归一化的形状抵达代理, 被白名单挡掉, 而代理里那段
    `..` 检查有没有生效完全看不出来。
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(f"GET {raw_path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        status_line = await reader.readline()
        return int(status_line.split()[1])
    finally:
        writer.close()
        await writer.wait_closed()


async def _rig(monkeypatch: pytest.MonkeyPatch) -> _Rig:
    """起真上游 + 真代理, 把代理的 upstream 指到假上游。"""
    up = _Upstream()
    up_app = web.Application()
    up_app["state"] = up
    up_app.router.add_route("*", "/{tail:.*}", _upstream_handler)
    up_server = TestServer(up_app)
    await up_server.start_server()
    monkeypatch.setenv("OAUTH_PROXY_UPSTREAM", str(up_server.make_url("")).rstrip("/"))

    client = TestClient(TestServer(_M.build_app()))
    await client.start_server()
    return _Rig(client=client, upstream=up)


async def test_unlisted_path_is_404_and_never_touches_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """白名单外 404, 且**一次都不许打到上游**。

    只断言 404 是不够的: 「先转发、拿到上游的 404 再回」也是 404, 但那已经把公网请求
    送进 gateway 了。`hits == []` 才是这条真正要守的东西。

    第二个断言管**判断顺序**: 未放行的路径配上不支持的方法, 必须仍是 404 而不是 405 ——
    405 等于告诉外面「这条路径存在, 只是方法不符」, 白送一份路径枚举的信道。把方法判断
    写在白名单判断之前就会这样, 而只用 GET 量的话看不出来(GET 本就在支持列表里)。
    """
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.get("/nope")
        assert resp.status == 404
        resp = await rig.client.put("/nope")
        assert resp.status == 404, "未放行的路径配不支持的方法应是 404, 不是 405"
        assert rig.upstream.hits == []
    finally:
        await rig.client.close()


async def test_prefix_match_requires_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """放行前缀必须带尾斜杠 —— 只是「以放行前缀的字面量开头」不算在它底下。

    前缀写成 `/feishu-web` 或 `/feishu`(丢了尾斜杠)时, `/feishu-web-evil/...` 与
    `/feishuXXX` 会被认成放行路径。这一条与 `test_core_routes_stay_blocked` 抓的不是
    同一件事: 那些核心路由都不以 `/feishu` 开头, 丢尾斜杠它们照旧被挡, 于是那条用例
    对这个缺陷完全无感 —— 实测过(变异复核里它仍绿)。
    """
    rig = await _rig(monkeypatch)
    try:
        for path in ("/feishu-web-evil/x", "/feishu-webhook", "/feishuXXX", "/feishu"):
            resp = await rig.client.get(path)
            assert resp.status == 404, f"{path} 不在放行前缀底下, 却被放行了"
        assert rig.upstream.hits == []
    finally:
        await rig.client.close()


async def test_core_routes_stay_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """gateway 上无鉴权的核心路由一条都不许过。

    `/sessions/{id}/chat` 能直接驱动 agent 执行工具(含 bash), `/workspace/file` 能读
    文件。它们与放行的 `/feishu/*` 同住一个进程, 挡住的唯一一层就是这个白名单。
    """
    rig = await _rig(monkeypatch)
    try:
        for method, path in (
            ("GET", "/sessions"),
            ("POST", "/sessions"),
            ("POST", "/sessions/abc/chat"),
            ("GET", "/sessions/abc/history"),
            ("DELETE", "/sessions/abc"),
            ("POST", "/titles"),
            ("POST", "/titles/generate"),
            ("GET", "/workspace/file"),
            ("POST", "/workspace/reveal"),
            ("POST", "/chat/completions"),
        ):
            resp = await rig.client.request(method, path)
            assert resp.status == 404, f"{method} {path} 竟然被放行了"
        assert rig.upstream.hits == []
    finally:
        await rig.client.close()


async def test_feishu_internal_routes_stay_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/feishu/route` `/feishu/routes` 仍然挡住 —— **同族不等于同待遇**。

    `test_core_routes_stay_blocked` 抓不到这两条: 它列的都是 `/sessions` 一族, 而这两条
    以 `/feishu/` 开头。会需要单列一条, 是因为白名单里 `/feishu/*` 已经放行了一堆前端接口
    (`/feishu/jsapi/config` 是最新一条), 下一个人照着补时很容易把「都是 /feishu/ 开头」
    当成放行理由 —— 而 routes 无鉴权且能 spawn Session, 浏览器一次都不打。

    顺带钉住 `jsapi/config` 放行 + `routes` 挡住**同时**成立: 只断言后者的话, 有人为了
    让这条绿而把整族撤回白名单也不会响。
    """
    rig = await _rig(monkeypatch)
    try:
        for method, path in (("GET", "/feishu/route"), ("GET", "/feishu/routes"), ("POST", "/feishu/routes")):
            resp = await rig.client.request(method, path)
            assert resp.status == 404, f"{method} {path} 是内部端点, 不许放行, 实得 {resp.status}"
        assert rig.upstream.hits == [], "未放行的路径一次都不该触达上游"
        resp = await rig.client.get("/feishu/jsapi/config")
        assert resp.status == 200, "前端签名接口该放行"
    finally:
        await rig.client.close()


async def test_unsupported_method_is_405_not_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """白名单内的路径 + 不支持的方法 → 405, 不是 404。

    405 与 404 必须能区分: 排查时「路径没放行」和「方法没放行」是两处不同的改法, 都
    回 404 就分不出来。
    """
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.put("/feishu/sessions")
        assert resp.status == 405
        assert rig.upstream.hits == []
    finally:
        await rig.client.close()


async def test_post_body_and_content_type_reach_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST 的 body 与 `Content-Type` 都要到上游。

    少了 `Content-Type`, gateway 侧 `await request.json()` 直接失败 —— 而 body 转发
    对了、头没转发时, 症状是登录接口 400, 看不出是代理丢的。
    """
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.post("/feishu/auth/login", json={"code": "abc123"})
        assert resp.status == 200
        assert len(rig.upstream.hits) == 1
        hit = rig.upstream.hits[0]
        assert hit.method == "POST"
        assert hit.path == "/feishu/auth/login"
        assert hit.body == b'{"code": "abc123"}'
        assert hit.headers["Content-Type"] == "application/json"
    finally:
        await rig.client.close()


async def test_delete_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE 在支持的方法列表里, 会被转发而不是被代理自己挡掉。

    打的是一条**已放行的**路径。今天 `/feishu/*` 里没有任何一条接 DELETE(前端删会话
    打的是核心的 `DELETE /sessions/{id}`, 那条刻意不放行), 所以这里量的是方法透传本身:
    代理不该成为 DELETE 到不了上游的原因。上游怎么回(很可能 405)不是本条的判据。
    """
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.delete("/feishu/sessions")
        assert resp.status == 200
        assert [(h.method, h.path) for h in rig.upstream.hits] == [("DELETE", "/feishu/sessions")]
    finally:
        await rig.client.close()


async def test_request_cookie_reaches_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求侧的 `Cookie` 头必须转发。

    网页应用的身份只从 `HttpOnly` cookie 里的 sid 取(`_routes.py:current_identity`),
    这一跳丢了 `Cookie`, 后续每条受保护路由都 401 —— 而路由本身是通的, 极难定位。
    """
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.get("/feishu/sessions", headers={"Cookie": "psi_feishu_sid=deadbeef"})
        assert resp.status == 200
        assert rig.upstream.hits[0].headers["Cookie"] == "psi_feishu_sid=deadbeef"
    finally:
        await rig.client.close()


async def test_all_set_cookie_headers_reach_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """上游下发几条 `Set-Cookie`, 客户端就要收到几条。

    **数量是这条的重点。** `resp.headers.get("Set-Cookie")` 只返回第一条, 用它转发时
    多出来的那些静默消失; 而「签发新 sid + 清掉旧 cookie」恰好就是两条的场合, 表现是
    登录看着成功、旧 cookie 还在。
    """
    rig = await _rig(monkeypatch)
    rig.upstream.set_cookies = [
        "psi_feishu_sid=new-sid; HttpOnly; Path=/",
        "psi_stale=; Max-Age=0; Path=/",
    ]
    try:
        resp = await rig.client.post("/feishu/auth/login", json={"code": "x"})
        assert resp.status == 200
        got = resp.headers.getall("Set-Cookie")
        assert len(got) == 2, f"Set-Cookie 应有 2 条, 实收 {len(got)}: {got}"
        assert got == rig.upstream.set_cookies
    finally:
        await rig.client.close()


async def test_path_traversal_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿 `..` 从放行前缀爬到核心路由必须被拒, 且要**编码过的也拒**。

    白名单的前缀匹配若做在未解码的路径上, `/feishu-web/..%2Fsessions` 会被认成
    `/feishu-web/` 底下的一个普通文件名而原样转发, 上游解码后就爬到了核心路由。

    **请求用裸 socket 发, 不走 aiohttp 客户端。** 客户端会在发出前自己把 `/a/../b`
    归一化成 `/b`, 于是「代理有没有挡住 `..`」这件事根本到不了代理 —— 那些请求以
    `/sessions` 的形状抵达, 被白名单挡掉, 用例照旧绿。实测: 去掉代理里的 `..` 检查后
    走客户端的版本仍然全绿, 判据完全没吃劲。裸 socket 才是这条唯一量得到的发法。

    多层编码那几条各守一层, 三条缺一不可 —— **这条链上一共有三处解码**, 判据必须逐层
    都有一条(实测确认过每一条的必要性):

    * `%2e%2e`(单层): aiohttp **服务端**已经替我们解过一层, 所以它到 handler 时就是
      `..`。代理里完全不解码时被它抓住。
    * `%252e%252e`(双层): 服务端解成 `%2e%2e`, handler 里那一次 `unquote` 解成 `..`。
      去掉 handler 那次解码时被它抓住。
    * `%25252e%25252e`(三层): 服务端 + handler 各解一层后还剩 `%2e%2e`, 只解一次的实现
      会把它当成 `/feishu-web/` 底下一个普通文件名**放行**, 而转发时 aiohttp **客户端**
      自己会再解一层并归一化 —— 上游最终收到 `/sessions`。只有反复解码到不动才挡得住,
      这条是 `_MAX_UNQUOTE_ROUNDS` 那个循环唯一的判据(实测: 少了它, 把实现改成解一次
      本条仍绿)。
    """
    rig = await _rig(monkeypatch)
    port = rig.client.server.port
    assert port is not None
    try:
        for raw in (
            "/feishu-web/../sessions",
            "/feishu/../sessions",
            "/feishu-web/..%2Fsessions",
            "/feishu-web/%2E%2E/sessions",
            "/feishu-web/..\\sessions",
            "/feishu-web/%252e%252e/sessions",
            "/feishu-web/%252E%252E%252Fsessions",
            "/feishu-web/%25252e%25252e%25252Fsessions",
            "/feishu-web/%25252e%25252e/sessions",
        ):
            status = await _raw_get(port, raw)
            assert status == 404, f"{raw} 竟然被放行了(得到 {status})"
        # 不止状态码: 上游一次都不许被打到。穿越的危害是**请求抵达了核心路由**, 而那种
        # 情况下客户端拿回的可能是上游的 200, 也可能是别的码 —— 只看码抓不住。
        assert rig.upstream.hits == [], f"穿越请求打到了上游: {rig.upstream.hits}"
    finally:
        await rig.client.close()


async def test_feishu_web_static_prefix_passes_any_subpath(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/feishu-web/` 下任意子路径都要过 —— 前端产物文件名带 hash, 不可能逐条列。"""
    rig = await _rig(monkeypatch)
    try:
        for path in (
            "/feishu-web/",
            "/feishu-web/index.html",
            "/feishu-web/assets/index-a1b2c3d4.js",
            "/feishu-web/assets/nested/deep/style.css",
        ):
            resp = await rig.client.get(path)
            assert resp.status == 200, f"{path} 应放行, 实得 {resp.status}"
        assert len(rig.upstream.hits) == 4
    finally:
        await rig.client.close()


async def test_oauth_paths_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """改造不许打断原有的两条 OAuth 路径 —— 那是这个代理原本唯一的用途。"""
    rig = await _rig(monkeypatch)
    try:
        for path in ("/oauth/callback", "/oauth/code"):
            resp = await rig.client.get(path)
            assert resp.status == 200, f"{path} 回归了: {resp.status}"
        assert len(rig.upstream.hits) == 2
    finally:
        await rig.client.close()


async def test_oauth_query_stays_whitelisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """OAuth 两条路径的 query 仍只放行那 4 个参数。"""
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.get("/oauth/callback?code=c1&state=s1&evil=x")
        assert resp.status == 200
        assert rig.upstream.hits[0].query == {"code": "c1", "state": "s1"}
    finally:
        await rig.client.close()


async def test_feishu_query_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """`/feishu/` 一族的 query 原样透传 —— 白名单只管 OAuth 那两条。"""
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.get("/feishu/sessions?limit=20&cursor=abc")
        assert resp.status == 200
        assert rig.upstream.hits[0].query == {"limit": "20", "cursor": "abc"}
    finally:
        await rig.client.close()


async def test_host_header_is_not_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Host` 不许照搬到上游 —— 转发白名单是「列出要带的」而不是「排除不要的」。"""
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.get("/feishu/app-id", headers={"Host": "evil.example.com", "X-Sneaky": "1"})
        assert resp.status == 200
        hit = rig.upstream.hits[0]
        assert hit.headers.get("Host") != "evil.example.com"
        assert "X-Sneaky" not in hit.headers
    finally:
        await rig.client.close()


async def test_every_frontend_feishu_path_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """前端会打的每条 `/feishu/*` 都在白名单里。

    清单来自 `src/psi_agent/gateway/feishu/feishu-web/api-paths.json`(由
    `scripts/feishu_web_paths.py` 从前端源码提取), 这里**只取 `/feishu/` 那一族** ——
    同清单里的 `/sessions`/`/titles`/`/workspace` 一族刻意不放行, 由
    `test_core_routes_stay_blocked` 守着。

    前端加一个 `/feishu/*` 端点而白名单没跟上 → 这条红, 挡住「云上那条静默 404」。
    """
    wanted = [e for e in _manifest_paths() if e["path"].startswith("/feishu/")]
    assert wanted, "清单里一条 /feishu/ 都没有, 提取器坏了"

    rig = await _rig(monkeypatch)
    try:
        for entry in wanted:
            # 参数化路径的 `{param}` 换成哨兵 —— 判据是路由可达性, 不碰真实会话。
            path = entry["path"].replace("{param}", "probe-id")
            resp = await rig.client.request(entry["method"], path)
            assert resp.status == 200, f"{entry['method']} {path} 未放行, 实得 {resp.status}"
    finally:
        await rig.client.close()


async def test_sse_chunks_arrive_before_upstream_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SSE 必须**边收边转**: 上游还没写完, 客户端就该收到前面的块。

    这一条是 `POST /feishu/sessions/{id}/chat`(带鉴权的聊天流)的判据。它被
    `/feishu/sessions/` 这个前缀覆盖 —— 那个前缀本是为 `/{id}/history` 加的,
    `startswith` 把 chat 一起放行了, 所以流式在这一跳是**已经在用**的能力, 不是预留。

    **判据不能只断言最终内容对**: 缓冲实现(`await resp.read()` 一次读完再回)最终吐出
    的字节与流式**一模一样**, 断言内容相等测不出任何东西。这里用一个闩把两者分开:
    上游写完第一块就停下等 `release`, 而判据在读到第一块之后才 set 它。于是

      * 流式: 第一块立刻到 → 判据放行 → 上游写完剩下的 → 通过。
      * 缓冲: 转发层要等上游 EOF 才回, 上游在等 `release`, 判据在等第一块 —— 三方
        死等, `asyncio.timeout` 到点变红。

    这是「断言真的吃劲」唯一的写法: 缓冲下**不可能**通过。
    """
    rig = await _rig(monkeypatch)
    rig.upstream.sse_chunks = [b"data: first\n\n", b"data: second\n\n", b"data: [DONE]\n\n"]
    try:
        resp = await rig.client.post("/feishu/sessions/s-1/chat", json={"chunks": "hi"})
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")

        # 上游此刻已写第一块、正卡在 release 上。转发层若缓冲, 这里读不到东西。
        async with asyncio.timeout(5):
            first = await resp.content.readuntil(b"\n\n")
        assert first == b"data: first\n\n"
        assert not rig.upstream.release.is_set(), "上游还没被放行, 却已经写完了 —— 闩没生效"

        # 确认到这一步上游确实还没写完 —— 「提前到达」才有意义。
        rig.upstream.release.set()
        async with asyncio.timeout(5):
            rest = await resp.content.read()
        assert b"data: second" in rest
        assert b"data: [DONE]" in rest
    finally:
        await rig.client.close()


async def test_sse_response_has_no_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式响应不许带 `Content-Length` —— 长度此时还不知道。

    自己算一个长度出来必然是错的(要么截断、要么客户端等一段永远补不齐的字节), 正确做法
    是交给 chunked 传输编码。这条与上面那条分开: 内容分块到达了、头却算错了, 浏览器一样
    显示不出来。
    """
    rig = await _rig(monkeypatch)
    rig.upstream.sse_chunks = [b"data: a\n\n", b"data: [DONE]\n\n"]
    rig.upstream.release.set()  # 本条不量时序, 直接放行
    try:
        resp = await rig.client.post("/feishu/sessions/s-1/chat", json={"chunks": "hi"})
        assert resp.status == 200
        assert "Content-Length" not in resp.headers
        assert resp.headers.get("Transfer-Encoding") == "chunked"
    finally:
        await rig.client.close()


def test_upstream_timeout_has_no_total_deadline() -> None:
    """上游超时不许设 `total` —— 那是「这条流总共能活多久」。

    `ClientTimeout(total=...)` 管的是从发出到响应体**读完**的整段时间, 对 SSE 就成了
    一条硬性寿命。原先是 `total=15`: 短回答一切正常, 而生成超过 15 秒的长回答会在中途
    断掉。已实测复现: 造一条每秒一个 event 的流配 `total=3`, 客户端收到 `data: 0/1/2`
    之后拿到 `ClientPayloadError`, 永远等不到 `[DONE]`。

    `sock_read` 必须仍在: 上游真卡死(一直不吐字节)时还要能断开, 否则连接泄漏。它量的是
    **两次数据之间**的间隔, 不随流的总时长增长, 所以不会掐断长回答。

    **这是一条配置断言, 不是端到端判据。** 端到端量它需要一条真的跑够 15 秒的流, 那会
    让用例集慢一个数量级; 把超时 patch 小再量则是在测 aiohttp 遵守 `total`、而不是测我们
    没有设它。所以这里直接钉住那个属性, 端到端的证据是上面那次手工复现。
    """
    assert _M._UPSTREAM_TIMEOUT.total is None, "设了 total 就是给 SSE 定了寿命"
    assert _M._UPSTREAM_TIMEOUT.sock_read is not None, "sock_read 不能一起丢, 否则上游卡死时连接泄漏"


async def test_set_cookie_survives_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    """流式路径上 `Set-Cookie` 也要回传。

    改成 `StreamResponse` 后响应头必须在 `prepare()` **之前**写好 —— prepare 之后再 add
    是静默无效的(头已经上路了)。缓冲实现那边这条本来是好的, 所以这是改造引入的新风险,
    不是原有判据的重复。
    """
    rig = await _rig(monkeypatch)
    rig.upstream.sse_chunks = [b"data: a\n\n", b"data: [DONE]\n\n"]
    rig.upstream.release.set()
    rig.upstream.set_cookies = ["psi_feishu_sid=stream-sid; HttpOnly; Path=/", "psi_x=1; Path=/"]
    try:
        resp = await rig.client.post("/feishu/sessions/s-1/chat", json={"chunks": "hi"})
        assert resp.status == 200
        got = resp.headers.getall("Set-Cookie")
        assert len(got) == 2, f"流式路径上 Set-Cookie 应有 2 条, 实收 {len(got)}: {got}"
    finally:
        await rig.client.close()


async def test_feishu_chat_path_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`POST /feishu/sessions/{id}/chat` 在放行范围内, 而裸的那条仍然不在。

    两条断言必须同时成立: 带鉴权的对等物放行、无鉴权的裸路由挡住。只断言前者的话, 哪天
    有人把裸的那条也加进白名单, 这里不会响。
    """
    rig = await _rig(monkeypatch)
    try:
        resp = await rig.client.post("/feishu/sessions/s-1/chat", json={"chunks": "hi"})
        assert resp.status == 200, "带鉴权的聊天流应放行"
        resp = await rig.client.post("/sessions/s-1/chat", json={"chunks": "hi"})
        assert resp.status == 404, "无鉴权的裸聊天流不许放行"
        assert [h.path for h in rig.upstream.hits] == ["/feishu/sessions/s-1/chat"]
    finally:
        await rig.client.close()
