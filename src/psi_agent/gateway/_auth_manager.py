"""AuthManager —— 登录态持有者 + 云端认证服务客户端。

**它不是微内核组件。** ``psi_agent/`` 下的顶层包 (``ai`` / ``channel`` /
``gateway`` / ``router`` / ``session``) 各有自己的 ``run()``、自己的 socket、
独立进程; 认证没有这些 —— 没 socket、没 ``run()``、不独立部署, 生命周期完全跟随
``Gateway.run()``。所以它是个 **Gateway manager**, 与 ``TitleManager`` /
``OAuthRelay`` 同级, 沿用 ``_xxx_manager.py`` 命名与平铺结构。

职责边界 (刻意窄):

- 只跟云端认证服务通 HTTP, **不持任何供应商密钥** —— 安装包里放阿里云 AK/SK 或
  Resend key 等于公开发布, 发码必须由云端代理。
- 只管「谁登录了」, 不碰 Session 层。用户数据 (会话历史/todos/workspace) 全部留在
  本机, 本期不做云端同步, ``Session`` 不加 owner 字段。
- 手机号与邮箱验证码**全程在应用内完成, 不开浏览器跳转**: OTP 不是第三方授权,
  号码和验证码本来就输在自己的界面里。跳转留给将来的 OAuth (那时复用 ``OAuthRelay``)。

``endpoint`` 为空时 ``Gateway`` 根本不创建本 manager、也不注册 ``/auth/*``, 现有
本地单用户流程零回归。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import anyio
from loguru import logger

from psi_agent._tls import client_ssl_context
from psi_agent.gateway._auth_store import AuthStore

# 客户端拿到 401 即视为登录态失效: 清本地凭证、回登录界面。没有静默续期逻辑 ——
# 云端是滑动过期 (每次请求刷 last_used_at), 60 天内正常使用不会掉线。
_UNAUTHORIZED = 401

# 云端认证服务的路由前缀。
#
# 取 ``/auth`` —— 这是**实际在跑的那套** (psi-cloud 挂在 ``/auth/otp``
# ``/auth/verify/email``)。契约文档里另有 ``/api/auth`` 形态, 但没有部署对应它;
# 默认值必须指向真实部署, 否则开箱即用的结果是全部 404。
#
# 空串表示无前缀。以 ``PSI_AUTH_PREFIX`` 覆盖 (``PSI_AUTH_PREFIX=""`` 即无前缀)。
_DEFAULT_PREFIX = "/auth"

# 云端认证服务地址的默认值。
#
# 写成默认值而不是必填参数: 装了包的用户直接 ``psi-agent gateway`` 就该能登录,
# 让他先知道并手填一个域名, 等于把部署细节转嫁给使用者。以 ``PSI_AUTH_ENDPOINT``
# 或 ``--auth-endpoint`` 覆盖 (自建云端时用)。
_DEFAULT_ENDPOINT = "https://account.genuineknowledge.cn"


def resolve_endpoint(raw: str = "") -> str:
    """定出云端地址。显式参数 > 环境变量 > 内置默认。

    ``PSI_AUTH_ENDPOINT=""`` (显式设成空串) 表示**关掉认证**, 与"没设过"区分开:
    没设过要拿默认值, 显式设空是用户明确不要 —— 二者混同会让人无法关掉。
    """
    if raw.strip():
        return raw.strip().rstrip("/")
    env = os.environ.get("PSI_AUTH_ENDPOINT")
    if env is not None:
        return env.strip().rstrip("/")
    return _DEFAULT_ENDPOINT


# 云端把 platform 收成闭集 windows|macos|linux 并拒绝集合外的值 (契约 TODO-3)。
# 直接送 ``sys.platform`` 会得到 win32 / darwin, 被服务端 400 挡死 —— 登录直接
# 不可用。故在客户端就映射成闭集值。
_PLATFORM_MAP = {
    "win32": "windows",
    "cygwin": "windows",
    "darwin": "macos",
    "linux": "linux",
}


def _resolve_platform(raw: str = "") -> str:
    """把 sys.platform 映射成云端接受的闭集值。未知平台回落 linux。"""
    key = (raw or sys.platform).strip().lower()
    if key in ("windows", "macos", "linux"):
        return key
    return _PLATFORM_MAP.get(key, "linux")


def _resolve_prefix() -> str:
    raw = os.environ.get("PSI_AUTH_PREFIX")
    if raw is None:
        return _DEFAULT_PREFIX
    return raw.rstrip("/")


# 单次请求上限。发码要过云端再到供应商, 给宽松些; 但必须有界, 否则网络黑洞会挂住
# 整个 Gateway 请求。
_TIMEOUT_SECONDS = 30.0

# 连接保活时长。aiohttp 默认 15s, 撑不过登录任一步的间隔: 输手机号 5-20s、
# 等短信 30-90s。默认值下每一步都是冷连接 (TCP 1 RTT + TLS 1 RTT + 请求 1 RTT),
# 境外云 RTT 约 210ms, 每步白付约 420ms —— 代码里复用 self._session 成立,
# 网络层一次也没复用上。
# 取值须比服务端空闲超时短, 否则池里会攒着对端已关的连接。2026-08-14 实测:
# 空闲 10/30/60/90/120/180s 全部复用, 服务端超时比 180s 还长 —— 于是 120s 稳在
# 安全侧, 客户端先于服务端回收。不再往上加: 登录全程最大间隔约 90s (等短信),
# 120s 已完整覆盖, 再加只是让空闲连接多占资源。
_KEEPALIVE_SECONDS = 120.0

# DNS 缓存。默认 10s, 云端地址不变, 没必要反复解析。
_DNS_CACHE_SECONDS = 600

# 预热节流。SPA 挂载登录面板时可能连发几次 /auth/status, 没必要每次都热一遍。
_WARM_THROTTLE_SECONDS = 5.0


# 连不通的成因分类。前端一律显示「无法连接认证服务」——那是给用户看的, 但排查
# 需要知道是四种成因里的哪一种, 而它们要改的地方完全不同:
#   证书   -> 冻结包里的 CA bundle 没打进去/找不到 (PyInstaller 环境特有)
#   代理   -> 代理配置或代理本身不可达
#   DNS    -> 域名解析不了
#   超时   -> 握手包被路径上的设备丢了 (见 psi_agent._tls 那条真实案例)
# 只写 repr(异常) 也能看出来, 但要求读日志的人认得 aiohttp 的异常层级; 直接落一个
# 分类词, 定性就不依赖那份知识。
#
# 顺序即优先级: ClientConnectorCertificateError 和 ClientProxyConnectionError 都是
# ClientConnectorError 的子类 (实测 3.14.1 的 mro), 泛类必须排在最后, 否则具体成因
# 全被它吞成 "connect"。
_FAILURE_KINDS: tuple[tuple[type[BaseException], str], ...] = (
    (aiohttp.ClientConnectorCertificateError, "tls-certificate"),
    (aiohttp.ClientConnectorSSLError, "tls-handshake"),
    (aiohttp.ClientProxyConnectionError, "proxy"),
    (aiohttp.ClientConnectorDNSError, "dns"),
    (aiohttp.ServerTimeoutError, "timeout"),
    (TimeoutError, "timeout"),
    (aiohttp.ClientConnectorError, "connect"),
)


def classify_failure(exc: BaseException | None) -> str:
    """把连接异常归成一个可读的成因词。认不出的回 ``unknown``。"""
    if exc is None:
        return "unknown"
    for exc_type, kind in _FAILURE_KINDS:
        if isinstance(exc, exc_type):
            return kind
    return "unknown"


@dataclass
class AuthManager:
    """持有登录态, 代理云端认证 API。"""

    endpoint: str = ""
    prefix: str = _DEFAULT_PREFIX
    """云端路由前缀。默认 ``/api/auth``; psi-cloud 那种根路径形态传 ``""``。"""
    _store: AuthStore | None = None
    _token: str = ""
    _pending_temp_token: str = ""
    """两段式注册中间态。只在内存里活, **不落盘** —— 它几分钟就过期, 存下来没有
    意义, 却多一处凭证在磁盘上。"""
    _device_key: str = ""
    _platform: str = ""
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _session: aiohttp.ClientSession | None = None
    _tg: Any = None  # anyio.TaskGroup (ty不识别的第三方类型)
    """Gateway 的 task group, 只用来调度连接预热。没注入就不预热, 功能不受影响。"""
    _warming: bool = False
    _last_warm: float = 0.0

    @classmethod
    async def create(cls, endpoint: str, appdata_root: str = "", platform: str = "", *, tg: Any = None) -> AuthManager:
        """建一个 manager 并从磁盘恢复登录态 (满足 R3: 跨重启保持)。

        ``tg`` 是 Gateway 的 anyio task group, 只用于连接预热; 不传则不预热。
        """
        store = await AuthStore.from_appdata(appdata_root)
        token = await store.load_token()
        device_key = await store.device_key()
        mgr = cls(
            endpoint=resolve_endpoint(endpoint),
            prefix=_resolve_prefix(),
            _store=store,
            _token=token,
            _device_key=device_key,
            _platform=_resolve_platform(platform),
            _tg=tg,
        )
        if token:
            logger.info("已从本机凭证恢复登录态 (未回验, 首次请求 401 时再清)")
        return mgr

    async def aclose(self) -> None:
        """释放 HTTP 会话。``Gateway.run`` 的 finally 里调用。"""
        if self._session is not None and not self._session.closed:
            with anyio.CancelScope(shield=True):
                await self._session.close()
        self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # 必须显式传 connector: 默认值的 keepalive 15s 撑不过登录步距,
            # 见 _KEEPALIVE_SECONDS。不加 enable_cleanup_closed —— Python 3.14.7
            # 下它已是 no-op 且抛 DeprecationWarning, 而本仓库禁止 noqa 抑制。
            # 必须显式传 ssl 上下文: 默认组列表下部分网络会丢握手包, 表现为
            # 「所有 /auth/* 全超时而 curl 秒回」。见 psi_agent._tls。
            connector = aiohttp.TCPConnector(
                keepalive_timeout=_KEEPALIVE_SECONDS,
                ttl_dns_cache=_DNS_CACHE_SECONDS,
                ssl=client_ssl_context(),
            )
            # 必须显式 trust_env: aiohttp 的默认值是 False, 即**无视** HTTPS_PROXY
            # 一类代理环境变量。而同一产品的另一条出站路 (AI 层的 httpx, 见
            # psi_agent.ai._build_http_client) 默认 True。于是代理后的机器上
            # 「对话能通、登录不通」, 前端只显示「无法连接认证服务」, 看不出是
            # 代理没走 —— 两条出站路的默认值不该相反。
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS),
                trust_env=True,
            )
        return self._session

    async def _attempt(
        self, method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        """发一次请求并把响应改造成前端契约。

        连接异常**不在这里吞**, 往上抛给 ``_call`` 决定要不要重试。
        """
        headers: dict[str, str] = {}
        if auth:
            if not self._token:
                return _UNAUTHORIZED, {"error": "unauthorized"}
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self.endpoint}{self.prefix}{path}"
        session = self._ensure_session()
        async with session.request(method, url, json=payload, headers=headers) as resp:
            body: dict[str, Any]
            try:
                body = await resp.json()
            except Exception:
                text = await resp.text()
                body = {"error": "bad_response", "detail": text[:200]}
            if isinstance(body, list):
                # 云端 ``GET /sessions`` 回**裸数组**。这里的返回类型契约是 dict,
                # 但不能因此把数据丢掉 —— 装进信封转交, 由路由层原样下发。
                # (曾经这一支落到下面的 bad_response, 设备列表恒为空。)
                body = {"items": body}
            elif not isinstance(body, dict):
                body = {"error": "bad_response"}
            # 云端把重试间隔放在 ``Retry-After`` **响应头**里, 响应体里没有。
            # 而 SPA 的 fetch 封装只读 body —— 于是「请 60 秒后再试」的倒计时
            # 永远拿不到秒数, 只能不显示或瞎猜。在此抄进 body, 前端契约保持
            # 「所有信息都在 body」一条, 不必让每个调用点都去摸 headers。
            if "retryAfter" not in body:
                raw_retry = resp.headers.get("Retry-After", "")
                if raw_retry.strip().isdigit():
                    body["retryAfter"] = int(raw_retry.strip())
            return resp.status, body

    async def _call(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = False,
        retry: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        """发一次云端请求, 返回 ``(状态码, 响应体)``。

        网络异常收敛成 ``(0, {"error": ...})`` —— 调用方 (HTTP 路由) 据此回 502,
        而不是让异常冒到 aiohttp 中间件变成 500。

        ``retry`` 只对幂等 GET 生效, 且必须在**调用点显式开启** —— 这样将来给新
        端点加方法时, 默认落在「不重试」那一侧。业务 POST 永不重试: 验证码被消耗
        两次后, 前端 D1 兜底屏会说「验证码不正确」, 而码是对的。
        """
        if not self.endpoint:
            return 0, {"error": "auth_endpoint_not_configured"}
        attempts = 2 if (retry and method == "GET") else 1
        last: Exception | None = None
        for i in range(attempts):
            try:
                return await self._attempt(method, path, payload, auth=auth)
            except aiohttp.ServerDisconnectedError as e:
                # 只认这一种: keepalive 拉长后, 池里的连接可能在「取出」与「发出」
                # 之间被对端关掉。不能捕 ClientOSError 或 ClientConnectionError ——
                # ServerDisconnectedError 与 ClientConnectorError (DNS 失败、连接
                # 被拒) 都是它们的子类, 罩上去会把真正连不通的情况也重试一遍,
                # 白等一个超时周期。
                last = e
                if i + 1 < attempts:
                    logger.info(f"连接已被对端关闭, 重试一次 {method} {path}")
                    continue
            except Exception as e:
                last = e
                break
        kind = classify_failure(last)
        logger.warning(f"认证服务请求失败 [{kind}] {method} {path}: {last!r}")
        # ``kind`` 也进 body: 前端的文案不变 (仍按 upstream_unreachable 走 D3 屏),
        # 但用户报障时截个图就带上了成因, 不必再去翻日志文件。
        return 0, {"error": "upstream_unreachable", "kind": kind, "detail": repr(last)[:200]}

    async def nudge_warm(self) -> None:
        """请求把连接焐热。不阻塞调用方 —— 只往 task group 里塞个任务就返回。

        连接池只在**发过一次请求之后**才有连接可复用, 而用户点「获取验证码」是
        这个进程里的第一次请求, 必然是冷的。趁用户还在看界面时先建好连接, 那一
        次点击就能落在热连接上。
        """
        if self._tg is None or self._warming:
            return
        now = anyio.current_time()
        if now - self._last_warm < _WARM_THROTTLE_SECONDS:
            return
        # 从「检查」到「置位」之间没有 await, 协作式调度下不会被抢占, 不需要锁。
        self._warming = True
        self._last_warm = now
        self._tg.start_soon(self._warm)

    async def _warm(self) -> None:
        """发一次无副作用的 ``GET /me`` 把 TCP+TLS 建好。

        **不带 token** (``auth=False``): 云端回 401, 而这里不调 ``_on_response``,
        因此不会把已登录用户踢下线。

        异常必须在这里吞掉 —— 逃出 ``start_soon`` 会拆掉整个 task group, 连带杀死
        Gateway。``_call`` 目前自己收敛异常, 但预热的代价太高, 不赌它将来不变。
        """
        try:
            await self._call("GET", "/me", retry=True)
        except Exception as e:
            logger.debug(f"连接预热失败, 忽略: {e!r}")
        finally:
            # 必须复位, 否则一次失败就永久堵死后续预热。
            self._warming = False

    async def _on_response(self, status: int) -> None:
        """401 即清本地凭证 —— 云端撤销设备后, 本机不该继续显示已登录。"""
        if status == _UNAUTHORIZED and self._token:
            logger.info("云端返回 401, 清除本机登录态")
            await self.logout_local()

    # ---- 发码 / 校验 ----
    async def send_code(self, *, phone: str = "", email: str = "") -> tuple[int, dict[str, Any]]:
        """请云端发验证码。手机号与邮箱二选一。

        没有邀请码参数: 云端已移除邀请码机制, 传了也只是被忽略的多余字段。
        """
        if phone:
            payload: dict[str, Any] = {"phone": phone}
            path = "/sms/send"
        elif email:
            payload = {"email": email}
            path = "/otp"
        else:
            return 400, {"error": "phone_or_email_required"}
        return await self._call("POST", path, payload)

    async def verify(self, *, code: str, phone: str = "", email: str = "") -> tuple[int, dict[str, Any]]:
        """校验验证码。

        老用户直接拿到 token; 新用户在本进程留下 ``tempToken``, 只对页面回
        ``registrationRequired: true``, 由页面走 ``/auth/complete`` 建号。
        """
        if not code:
            return 400, {"error": "code_required"}
        if phone:
            payload: dict[str, Any] = {"phone": phone}
            path = "/verify/phone"
        elif email:
            payload = {"email": email}
            path = "/verify/email"
        else:
            return 400, {"error": "phone_or_email_required"}
        payload.update({"code": code, "deviceKey": self._device_key, "platform": self._platform})
        status, body = await self._call("POST", path, payload)
        if status == 200 and body.get("token"):
            await self._adopt_token(str(body["token"]))
        # tempToken 留在本进程, **不下发给页面**: 它是能换正式 token 的凭证, 交给
        # 页面脚本就等于把 XSS 升格成凭证泄露。同理不能让前端拿模块级变量存它 ——
        # 那既违反「组件模块不留可变全局」, 也没解决凭证进浏览器这个根问题。
        if status == 200 and body.get("tempToken"):
            async with self._lock:
                self._pending_temp_token = str(body["tempToken"])
            body = {k: v for k, v in body.items() if k != "tempToken"}
            # 摘掉凭证后必须补一个不含凭证的信号, 否则页面无从判断"这是新用户,
            # 该进建号屏", 会当成登录失败弹回输入页。
            body["registrationRequired"] = True
        return status, body

    async def complete(self, *, display_name: str = "") -> tuple[int, dict[str, Any]]:
        """两段式注册的第二段: 建号并换正式 token。

        ``tempToken`` 取自上一步 ``verify`` 暂存的值, 不由调用方传入。
        """
        async with self._lock:
            temp_token = self._pending_temp_token
        if not temp_token:
            return 400, {"error": "temp_token_required"}
        payload: dict[str, Any] = {
            "tempToken": temp_token,
            "deviceKey": self._device_key,
            "platform": self._platform,
        }
        if display_name:
            payload["displayName"] = display_name
        status, body = await self._call("POST", "/complete", payload)
        # 用过即弃: 成功换到 token 自然不再需要; 失败(过期/被占)也不该留着重放。
        async with self._lock:
            self._pending_temp_token = ""
        if status == 200 and body.get("token"):
            await self._adopt_token(str(body["token"]))
        return status, body

    async def bind(self, *, code: str, phone: str = "", email: str = "") -> tuple[int, dict[str, Any]]:
        """已登录态下把手机号/邮箱绑到当前账号。复用同一条发码, 校验走
        ``/identities/*``, 带 Bearer token, 不签发新会话。返回更新后的 UserOut。"""
        if not code:
            return 400, {"error": "code_required"}
        if phone:
            payload: dict[str, Any] = {"phone": phone}
            path = "/identities/phone"
        elif email:
            payload = {"email": email}
            path = "/identities/email"
        else:
            return 400, {"error": "phone_or_email_required"}
        payload["code"] = code
        status, body = await self._call("POST", path, payload, auth=True)
        # 与其它已登录接口一致: 401 即清本机凭证。漏掉这一步的话, 云端撤销本设备后
        # 用户在绑定界面会一直收到"登录态失效", 但界面仍显示已登录, 只能手动登出。
        await self._on_response(status)
        return status, body

    async def _adopt_token(self, token: str) -> None:
        async with self._lock:
            self._token = token
        if self._store is not None:
            await self._store.save_token(token)
        logger.info("登录成功, 凭证已落盘")

    # ---- 已登录接口 ----
    async def me(self) -> tuple[int, dict[str, Any]]:
        status, body = await self._call("GET", "/me", auth=True, retry=True)
        await self._on_response(status)
        return status, body

    async def list_devices(self) -> tuple[int, dict[str, Any]]:
        """列出已登录设备。统一成 ``{"devices": [...]}`` 下发。

        云端回裸数组, ``_call`` 会装进 ``items`` 信封; 在此拆回并落到页面契约的
        ``devices`` 键, 页面不必再猜三种形状。
        """
        status, body = await self._call("GET", "/sessions", auth=True, retry=True)
        await self._on_response(status)
        if status == 200:
            items = body.get("items")
            if items is None:
                items = body.get("devices") or body.get("sessions") or []
            body = {"devices": items if isinstance(items, list) else []}
        return status, body

    async def revoke_device(self, device_id: str) -> tuple[int, dict[str, Any]]:
        # 不开 retry。DELETE 按 HTTP 语义算幂等, 但重试拿到的 404 会让界面说
        # 「设备不存在」—— 而第一次其实已经删成功了。误导比省一个 RTT 重要。
        if not device_id:
            return 400, {"error": "device_id_required"}
        status, body = await self._call("DELETE", f"/sessions/{device_id}", auth=True)
        await self._on_response(status)
        return status, body

    async def unbind(self, provider: str) -> tuple[int, dict[str, Any]]:
        """解绑一种登录方式(手机/邮箱)。云端会拦截「解绑最后一个身份」。"""
        if provider not in ("phone", "email"):
            return 400, {"error": "invalid_provider"}
        status, body = await self._call("DELETE", f"/identities/{provider}", auth=True)
        await self._on_response(status)
        return status, body

    async def logout(self) -> tuple[int, dict[str, Any]]:
        """通知云端撤销本会话, 然后清本机凭证。

        云端不可达时也要清本机 —— 否则用户点了登出却仍显示已登录, 比多留一条
        云端会话更糟 (那条会话 60 天后自然过期)。
        """
        status, body = await self._call("POST", "/logout", auth=True)
        await self.logout_local()
        if status == 0:
            logger.warning("云端不可达, 已仅清除本机登录态")
        return (200 if status == 0 else status), (body if status else {"ok": True})

    async def logout_local(self) -> None:
        async with self._lock:
            self._token = ""
            # 半途放弃注册后残留的 tempToken 一并清掉, 不留可换 token 的凭证。
            self._pending_temp_token = ""
        if self._store is not None:
            await self._store.clear_token()

    def bearer_token(self) -> str:
        """当前 token, 未登录时为空串。

        ** 唯一的进程内取值口 **, 只给免费模型换算力用 (见 ``_free_model.py``)。
        不加锁: 读一个 str 是原子的, 而这里要的就是「此刻的值」—— 拿到旧值的
        后果是一次 401, 拿锁的代价是每次建 AI socket 都要等一次认证请求。

        ** 不要把它接到任何下行响应上 **: token 不进快照、不进 ``/ais``、不进
        ``status()``。要判断有没有登录用 ``status()["loggedIn"]``。
        """
        return self._token

    # ---- 状态 ----
    def status(self) -> dict[str, Any]:
        """给 SPA 判断该显示登录引导还是身份信息。不含 token 本身。"""
        return {
            "endpoint": self.endpoint,
            # 暴露前缀: 对不上时全部 404, 这是第一个该看的地方
            "prefix": self.prefix,
            "loggedIn": bool(self._token),
            "deviceKey": self._device_key,
            "platform": self._platform,
            # 钥匙串不可用时如实上报, 让界面能提示「凭证未加密」而非假装安全
            "credentialEncrypted": bool(self._store.encrypted) if self._store else False,
        }
