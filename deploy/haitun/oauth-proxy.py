"""公网唯一入口: 白名单反代, 只把 OAuth 两条与飞书网页应用需要的那些路径放行到 Gateway。

Gateway 自身还挂着 /sessions、/titles、/workspace、/chat/completions 这些**一行鉴权都
没有**的路由, 其中 POST /sessions/{id}/chat 能直接驱动 agent 执行工具(含 bash), 所以
不能把 Gateway 端口整个暴露出去。本代理是唯一对外监听的入口, 白名单外的路径一律 404、
不做任何转发。

拓扑(生产): Caddy 占 80/443 → 反代 127.0.0.1:8090 的本进程 → Gateway 容器
(compose 里 network_mode: "service:gateway", 与 Gateway 共享 netns, 故上游是 127.0.0.1)。

承载两件事:

1. **OAuth 回调落地** —— 用户点完飞书授权, 回调自己落到 Gateway, 不必从地址栏手抄
   code。/oauth/code 也必须放行: 工具侧用同一个基址轮询取件(见
   agents/feishu/tools/_oauth_receiver.py 的 _wait_for_code), 少放一条等于没接通。
2. **飞书网页应用** —— /feishu-web/ 的静态产物 + /feishu/* 那一族 API, 其中
   POST /feishu/sessions/{id}/chat 是一条 **SSE**(带鉴权的聊天流)。所以转发层必须边收边
   转, 见 _relay。

## 头转发是这个代理最容易出错的地方

网页应用的鉴权全靠 HttpOnly cookie: 登录时 Gateway 用 set_cookie 下发 psi_feishu_sid
(gateway/feishu/_routes.py 的 _issue_login), 之后每条受保护路由都从 request.cookies
读它(同文件 current_identity)。**请求侧不带 Cookie 头、响应侧不回 Set-Cookie 头, 整套
鉴权就在这一跳断掉**, 而表现是「路由都通了但一直 401」—— 路径全对、状态码也不像代理的
错, 极难定位。所以两个方向都要转发, 且响应侧必须用 getall 取**全部** Set-Cookie
(见 _RESPONSE_PASSTHROUGH_HEADERS 处的注释)。

环境变量:
  OAUTH_PROXY_LISTEN     对外监听地址, 默认 0.0.0.0:8080
  OAUTH_PROXY_UPSTREAM   Gateway 基址, 默认 http://127.0.0.1:8080
"""

from __future__ import annotations

import os
from urllib.parse import unquote

import aiohttp
from aiohttp import web

# 精确匹配的放行路径。**只列前端真的会打的**, 多放一条就是白送一份公网暴露面。
#
# 刻意不含的:
#   * /sessions /titles /workspace/* /chat/completions —— 无鉴权, 见模块 docstring。
#     它们同在 api-paths.json 里(前端确实在打), 但那是**直连本地 gateway** 时的用法;
#     公网这一跳不放行, 由 tests/deploy/test_oauth_proxy.py 双向钉住。
#   * /feishu/route /feishu/routes —— channel 进程内部调用, 无鉴权且能 spawn Session,
#     浏览器一次都不打。
ALLOWED_PATHS = frozenset(
    {
        # OAuth: 本代理原本唯一的用途。
        "/oauth/callback",
        "/oauth/code",
        # 登录一族(register_auth_routes)。
        "/feishu/app-id",
        "/feishu/auth/login",
        "/feishu/auth/logout",
        "/feishu/auth/me",
        # 网页应用数据一族(register_feishu_routes), 全部按 sid 过滤身份。
        "/feishu/defaults",
        "/feishu/jsapi/config",
        "/feishu/sessions",
        "/feishu/summaries",
        "/feishu/titles",
    }
)

# 前缀匹配的放行路径 —— 只有**参数化路径**与**静态产物**才配得上前缀, 因为它们逐条列
# 不出来:
#   * /feishu-web/ 的产物文件名带内容 hash, 每次 build 都变。
#   * /feishu/sessions/ 下是 /{session_id}/history, session id 是运行期生成的。
#
# **这个前缀是会捎带的**: /feishu/sessions/ 下现在还住着 POST /{id}/chat(带鉴权的聊天流,
# 能驱动 agent 执行工具), 它不是被单独加进来的, 是 startswith 一起放行的。往这个前缀下加
# 路由**不动白名单就自动对公网可达** —— 加的时候要自己判断该不该暴露。chat 那条还带来一
# 个硬要求: 它是 SSE, 转发必须流式(见 _relay)。
#
# 前缀必须以 / 结尾: 否则 /feishu 会把 /feishudlfjk 一起吃掉。匹配前路径已被
# _normalized_path 归一化并挡掉 .., 所以 /feishu-web/../sessions 过不来。
ALLOWED_PREFIXES = ("/feishu-web/", "/feishu/sessions/")

# OAuth 流程真正用到的查询参数。**只管 ALLOWED_QUERY_PATHS 那两条**: 那两条的 query
# 直接进 OAuth 换取链, 收窄它有意义。/feishu/* 一族的 query 是普通业务参数(分页游标
# 之类, 前端加一个就多一个), 逐个列会让「前端加个参数 → 云上静默丢参数」变成常态,
# 所以那边原样透传 —— 收益在路径白名单上, 不在这里。
ALLOWED_QUERY = frozenset({"code", "state", "error", "error_description"})
ALLOWED_QUERY_PATHS = frozenset({"/oauth/callback", "/oauth/code"})

# 支持转发的方法。前端只用这三种(api-paths.json 里就这三种), 其余一律 405。
ALLOWED_METHODS = frozenset({"GET", "POST", "DELETE"})

# 往上游带的请求头 —— **白名单式**, 不是「排除几个」。无脑转发全部会把 Host(上游按它做
# vhost/重定向)、Content-Length(body 重新编码后长度可能不符)、Accept-Encoding(上游压缩
# 后本进程按 raw body 回传会坏)一起带过去。
#   * Cookie          —— 登录态唯一来源, 见模块 docstring。
#   * Content-Type    —— 少了它 Gateway 侧 await request.json() 直接失败。
#   * Accept          —— 让上游能区分要 JSON 还是 HTML(OAuth 回调回的是 HTML)。
_REQUEST_PASSTHROUGH_HEADERS = frozenset({"Cookie", "Content-Type", "Accept"})

# 回客户端的响应头。Set-Cookie 用 getall 逐条取: 一个响应可能有多条(签发新 sid + 清掉
# 旧 cookie 就是两条), 而 headers.get() **只返回第一条**、其余静默消失 —— 表现是登录看
# 着成功但旧 cookie 还在。Content-Type 单独走 web.Response 的参数。
_RESPONSE_PASSTHROUGH_HEADERS = frozenset({"Set-Cookie", "Location", "Cache-Control"})

# 上游超时。**total 必须是 None**: 放行范围里有 SSE(见 _relay), 而 total 管的是「从发出
# 到响应体读完」的整段时间 —— 原先那个 total=15 会把任何超过 15 秒的聊天流从中间掐断,
# 表现是长回答生成到一半连接断掉, 而短回答一切正常。
#
# 换成两个分别的闸, 它们都不随响应时长增长:
#   * sock_connect —— 连不上上游要快速失败, 不是干等。
#   * sock_read    —— 两次收到数据之间的最长间隔。上游卡死时仍然能断开, 但只要它还在
#                     吐 event(gateway 的 SSE 有 keepalive)就一直续下去。
_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=300)


def _upstream() -> str:
    return os.environ.get("OAUTH_PROXY_UPSTREAM", "http://127.0.0.1:8080").rstrip("/")


#: 反复解码的次数上限 —— 防「解到不动为止」被一个深层嵌套的路径拖住。到了上限还在变就
#: 当不安全拒掉: 合法路径解一次就该稳定, 解 4 层还在变的只能是刻意构造的。
_MAX_UNQUOTE_ROUNDS = 4


def _normalized_path(raw: str) -> str | None:
    """归一化请求路径, 判不安全就回 None(调用方 404)。

    **反复解码到不动为止**, 而不是解一次。aiohttp 服务端已经替我们解过一层, 所以单层
    编码的 %2e%2e 到这里本就是 ..; 真正的口子是**双重编码**: %252e%252e 解一层还是
    %2e%2e, 前缀匹配把它当成 /feishu-web/ 底下一个普通文件名放行, 而转发时 aiohttp
    客户端自己会再解一层并归一化 —— 上游最终收到的是 /sessions。实测过。

    解完只要出现 .. 段就整条拒掉, 不做「解析掉 ..」那种归一化: 合法请求里根本不会有
    .., 拒绝比化解更难出错。

    转发用的是**解码后**的 path(且已确认无 ..), 于是「本进程判的字符串」与「上游看到
    的字符串」一致 —— 判一个形状、转发另一个形状正是穿越能钻进来的缝。
    """
    path = raw
    for _ in range(_MAX_UNQUOTE_ROUNDS):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    else:
        # 解到上限仍在变化 —— 不判它到底是什么, 直接拒。
        if unquote(path) != path:
            return None
    # 反斜杠也算分隔符: 上游若在某个平台上把 \ 当分隔符, 只挡 / 会留下一条口子。
    if ".." in path.replace("\\", "/").split("/"):
        return None
    return path


def _is_allowed(path: str) -> bool:
    return path in ALLOWED_PATHS or path.startswith(ALLOWED_PREFIXES)


def _forward_params(request: web.Request, path: str) -> dict[str, str]:
    if path in ALLOWED_QUERY_PATHS:
        return {k: v for k, v in request.query.items() if k in ALLOWED_QUERY}
    return dict(request.query)


async def _forward(request: web.Request) -> web.StreamResponse:
    path = _normalized_path(request.path)
    if path is None or not _is_allowed(path):
        # 白名单判断在方法判断**之前**: 未放行的路径不该因为方法不对而漏出 405 —— 那
        # 等于告诉外面「这条路径存在, 只是方法不符」。
        return web.Response(status=404, text="Not Found\n")
    if request.method not in ALLOWED_METHODS:
        # 405 而非 404: 排查时「路径没放行」与「方法没放行」是两处不同的改法。
        return web.Response(status=405, text="Method Not Allowed\n")

    headers = {k: v for k, v in request.headers.items() if k in _REQUEST_PASSTHROUGH_HEADERS}
    body = await request.read()
    url = f"{_upstream()}{path}"
    session: aiohttp.ClientSession = request.app["client"]
    try:
        async with session.request(
            request.method,
            url,
            params=_forward_params(request, path),
            headers=headers,
            data=body or None,
            allow_redirects=False,
        ) as resp:
            return await _relay(request, resp)
    except aiohttp.ClientError:
        return web.Response(status=502, text="Bad Gateway\n")


async def _relay(request: web.Request, resp: aiohttp.ClientResponse) -> web.StreamResponse:
    """把上游响应**边收边转**给客户端。

    用 StreamResponse 而非把 body 读完再回, 因为放行范围里已经有 SSE:
    POST /feishu/sessions/{id}/chat(带鉴权的聊天流)被 ALLOWED_PREFIXES 里的
    /feishu/sessions/ 覆盖。一次读完的写法会把 SSE 憋成「等全部生成完才一次吐出」——
    打字机效果消失, 长回答表现为疑似卡死。

    三个要点:

    * **不自己设 Content-Length。** 转发的时候根本不知道总长度。让 aiohttp 按 chunked
      发(不给 content_length 即是), 自己算一个必然错: 短了截断, 长了客户端等一段永远
      补不齐的字节。上游若给了 Content-Length 也不照搬 —— 我们逐块写, 由本进程的传输
      编码说话。
    * **响应头必须在 prepare() 之前写完。** prepare 之后再 add 是**静默无效**的(头已经
      上路了), 表现是 Set-Cookie 丢失、登录不上。
    * **不自己加缓冲。** 逐块 write 之后不 drain 成大块; iter_any() 给多少写多少。
    """
    out = web.StreamResponse(status=resp.status)
    content_type = resp.headers.get("Content-Type", "text/plain")
    out.content_type = content_type.split(";")[0]
    for name in _RESPONSE_PASSTHROUGH_HEADERS:
        for value in resp.headers.getall(name, ()):
            out.headers.add(name, value)
    await out.prepare(request)
    # iter_any(): 上游给多少就转多少, 不按固定块大小攒 —— 攒够才发就等于又加了一层
    # 缓冲, SSE 的每个 event 会卡在里面。
    async for chunk in resp.content.iter_any():
        await out.write(chunk)
    await out.write_eof()
    return out


async def _on_startup(app: web.Application) -> None:
    app["client"] = aiohttp.ClientSession(timeout=_UPSTREAM_TIMEOUT)


async def _on_cleanup(app: web.Application) -> None:
    await app["client"].close()


def build_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    # 通配 *: 白名单与方法判断全在 handler 里, 好让「路径未放行」(404) 与「方法未放行」
    # (405) 由本代理自己区分开。交给 router 按方法注册的话, 未注册的方法会被 aiohttp
    # 回 405, 于是未放行的路径也可能拿到 405 而不是 404。
    app.router.add_route("*", "/{tail:.*}", _forward)
    return app


def main() -> None:
    listen = os.environ.get("OAUTH_PROXY_LISTEN", "0.0.0.0:8080")
    host, _, port = listen.rpartition(":")
    web.run_app(build_app(), host=host or "0.0.0.0", port=int(port), access_log=None)


if __name__ == "__main__":
    main()
