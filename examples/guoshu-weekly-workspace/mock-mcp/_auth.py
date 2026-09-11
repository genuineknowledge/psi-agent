"""端点级鉴权:两级共享 token(读用 / 敏感字段可读)。

## 为什么要有这一层

交付形态是"一个容器嵌进国数网站"。在此之前,服务**没有任何请求级鉴权**:
``server.py`` 里定义了 ``BEARER_TOKEN`` 却从未使用,唯一与权限有关的是"审批意见
要不要打码"那一个开关 —— 而它的默认值是 ``demo-admin-token``。也就是说:

* 任何能访问 18900 的人都能调全部 31 个工具(任务清单、进展、附件元数据);
* 那个默认值一旦被猜中,敏感字段(审批意见)也一并拿到。

对一个要挂到内网站点上的服务,这是**交付阻断级**的缺口,不是"部署侧注意一下"能兜住的:
部署侧的鉴权只能拦住"谁进得来",拦不住"进来之后能拿多少"。

## 两级:这是"领导 / 员工"那类区分的最小可用形态

两个**共享** token,权限从高到低:

| 环境变量 | 称呼 | 权限 |
|---|---|---|
| ``GUOSHU_WEEKLY_MOCK_ADMIN_TOKEN`` | 管理 / 领导通道 | 全部工具 + 敏感字段(审批意见等)明文 |
| ``GUOSHU_WEEKLY_MOCK_TOKEN`` | 常规 / 员工通道 | 全部工具,但敏感字段打码 ``[按权限不展示]`` |

**这不是"按登录身份出数"**。真正的按人授权要身份传递 + 行级可见域 + 每个出口的域过滤,
本模块只做**第一层**:让服务至少能分辨"两类调用方",并且**默认拒绝**。没有它,
后面那层身份体系无处可挂。

## 为什么不用 SDK 的 ``token_verifier``

``mcp`` 1.30 的 ``FastMCP(token_verifier=...)`` 走的是完整 OAuth 流程:要 ``AuthSettings``
(issuer / resource_server_url / required_scopes),客户端还要能取 auth server 的元数据。
对"共享 token"这种基线,那套东西会带进一个并不存在的授权服务器。所以这里自己缠一层
ASGI 中间件 —— **行为完全可控、可单测**,且不依赖 SDK 的内部结构。

## 三条硬纪律

1. **正式源模式下两个 token 都必须显式注入**(无默认值)。缺一个就拒绝启动 ——
   继承了那个 ``demo-admin-token`` 默认值的容器,等于没鉴权。
2. **两个 token 必须不同**。若允许相同,常规通道的调用方就能解锁敏感字段,
   分级形同虚设。相同也拒绝启动。
3. **token 不回显**:401 响应里只说"缺少 / 无效",回显会让它出现在访问日志里。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

REQUEST_TOKEN_ENV = "GUOSHU_WEEKLY_MOCK_TOKEN"
"""常规通道 token(环境变量名)。"""

SENSITIVE_TOKEN_ENV = "GUOSHU_WEEKLY_MOCK_ADMIN_TOKEN"
"""敏感字段通道 token(环境变量名)。"""

SCOPE_GRANT_KEY = "guoshu_weekly_grant"
"""中间件把鉴权结果放进 ASGI ``scope`` 的这个键,工具侧从中读取。"""

READER_SCOPE = "weekly:read"
SENSITIVE_SCOPE = "weekly:sensitive"

OPEN_PATHS = ("/healthz",)
"""不需要鉴权的路径(只用来探活,不暴露任何数据)。"""


@dataclass(frozen=True)
class TokenGrant:
    """一次成功鉴权得到的权限。"""

    name: str
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def may_read_sensitive(self) -> bool:
        return SENSITIVE_SCOPE in self.scopes


@dataclass(frozen=True)
class AuthPolicy:
    """鉴权策略:由环境变量决定,启动时解析一次。"""

    enabled: bool
    reader_token: str | None = None
    sensitive_token: str | None = None

    def grant_for(self, token: str) -> TokenGrant | None:
        """校验一个 bearer token;通过则给出它的权限,否则 None。

        **先比敏感 token**:它与常规 token 必须不同(见模块 docstring),但即使将来
        有人把两者配成一样,这里也不至于把管理通道降级成常规通道。
        """
        if not self.enabled or not token:
            return None
        if self.sensitive_token and token == self.sensitive_token:
            return TokenGrant("sensitive", frozenset({READER_SCOPE, SENSITIVE_SCOPE}))
        if self.reader_token and token == self.reader_token:
            return TokenGrant("reader", frozenset({READER_SCOPE}))
        return None


class AuthConfigError(RuntimeError):
    """鉴权配置不合法 —— 启动期就报出来,不带病运行。"""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def formal_source_selected(env: dict[str, str] | None = None) -> bool:
    """正式源是否被选中(只看环境变量,不看驱动)。

    与 ``_formal.enabled()`` 相比少了"驱动可用"那一半 —— 这里刻意如此:鉴权是
    **HTTP 层**的事,与 psycopg 装没装无关,而且本模块要能被单测独立导入
    (不拉 ``_formal``)。两处的取值域必须一致(``o2oa`` / ``pg``),改一处要改两处。
    """
    resolved: dict[str, str] = dict(os.environ) if env is None else dict(env)
    return (resolved.get("TASK_BOARD_DATA_SOURCE") or "").strip().lower() in ("o2oa", "pg")


def load_policy(env: dict[str, str] | None = None) -> AuthPolicy:
    """从环境变量解析策略。正式源模式下缺配置 / 配置相同 -> 抛 ``AuthConfigError``。

    演示模式(未选正式源)保持**向后兼容**:两个 token 都没配时不启用鉴权,
    这样 README 里那套"起 mock 服务 + 跑契约测试"的流程一字不改。
    演示模式下**配了** token 就按同一套规则启用(便于本地验证鉴权本身)。
    """
    resolved: dict[str, str] = dict(os.environ) if env is None else env
    reader = _clean(resolved.get(REQUEST_TOKEN_ENV))
    sensitive = _clean(resolved.get(SENSITIVE_TOKEN_ENV))
    formal = formal_source_selected(resolved)

    if not formal and not reader and not sensitive:
        return AuthPolicy(enabled=False)

    missing = [
        name
        for name, value in ((REQUEST_TOKEN_ENV, reader), (SENSITIVE_TOKEN_ENV, sensitive))
        if not value
    ]
    if missing:
        raise AuthConfigError(
            "鉴权已启用(正式源模式要求必配),但缺少环境变量:"
            + ", ".join(missing)
            + "。服务拒绝以「部分鉴权」的状态启动:缺常规 token 等于没有鉴权,"
            "缺敏感 token 则敏感字段无人可读。"
            "两个 token 都要通过 Secret / 环境注入,不要写进镜像或代码。"
        )
    if reader == sensitive:
        raise AuthConfigError(
            f"{REQUEST_TOKEN_ENV} 与 {SENSITIVE_TOKEN_ENV} 不能相同:"
            "相同则常规通道也能解锁敏感字段(审批意见等),两级分级形同虚设。"
        )
    return AuthPolicy(enabled=True, reader_token=reader, sensitive_token=sensitive)


def bearer_token(headers: Any) -> str:
    """从请求头里取 bearer token(大小写不敏感,兼容 ``Bearer`` / ``bearer``)。

    两种输入都要认:真实 ASGI scope 里 header 值是 ``bytes``,而直接构造的假 scope
    与部分中间件给 ``str``。少认一种就会**静默判成"没有 token"**,即所有人都 401 ——
    这种故障看上去像"凭据发错了",很难往编码上想。

    不用 ``removeprefix("Bearer")``:它会把 ``BearerFoo`` 也算通过。
    """
    try:
        raw = headers.get("authorization") or headers.get("Authorization") or ""
    except AttributeError:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1", "replace")
    parts = str(raw).split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def grant_from_scope(scope: dict[str, Any]) -> TokenGrant | None:
    """工具侧读取本次调用的权限(由中间件写入;没有就是未经鉴权)。"""
    value = scope.get(SCOPE_GRANT_KEY)
    return value if isinstance(value, TokenGrant) else None


class BearerAuthMiddleware:
    """纯 ASGI 中间件:校验 ``Authorization: Bearer <token>``,不通过就 401。

    刻意写成纯 ASGI 而不是 ``BaseHTTPMiddleware``:后者会把请求体读进内存再转发,
    而 MCP 的 streamable-http 有长连接与流式响应,多一层缓冲只带来新的故障面。

    这里**不 import starlette**:``anyio`` 是 mcp 的依赖,一定在。
    """

    def __init__(self, app: Any, policy: AuthPolicy, paths: tuple[str, ...] = ("/mcp",)) -> None:
        self.app = app
        self.policy = policy
        self.paths = paths

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not self.policy.enabled:
            await self.app(scope, receive, send)
            return
        # 归一化尾斜杠:客户端写 /mcp/ 也要受保护
        path = str(scope.get("path") or "").rstrip("/") or "/"
        guarded = any(path == p.rstrip("/") for p in self.paths)
        if not guarded:
            await self.app(scope, receive, send)
            return

        # ASGI 规范里 header 名与值都是 bytes,但**不能假设** —— 测试用的假 scope
        # 与某些中间件会传 str。``bytes.decode`` / ``str.encode`` 各试一次,两种都收。
        headers: dict[str, Any] = {}
        for key, value in scope.get("headers") or []:
            name = key.decode("latin-1") if isinstance(key, bytes) else str(key)
            headers[name.lower()] = value
        token = bearer_token(headers)
        grant = self.policy.grant_for(token)
        if grant is None:
            await self._unauthorized(send, has_token=bool(token))
            return
        # 权限随 scope 往下传:工具侧只读,不参与判定
        scope = {**scope, SCOPE_GRANT_KEY: grant}
        await self.app(scope, receive, send)

    async def _unauthorized(self, send: Any, has_token: bool) -> None:
        reason = "token 无效" if has_token else "缺少 bearer token"
        body = (
            '{"ok":false,"error":{"code":"unauthorized","message":"'
            f"{reason}:请带上 Authorization: Bearer <token>"
            '"}}'
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"www-authenticate", b'Bearer realm="guoshu-weekly"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
