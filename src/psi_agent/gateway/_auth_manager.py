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

from psi_agent._logging import trace_id_var
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

    @classmethod
    async def create(cls, endpoint: str, appdata_root: str = "", platform: str = "") -> AuthManager:
        """建一个 manager 并从磁盘恢复登录态 (满足 R3: 跨重启保持)。"""
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
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS))
        return self._session

    async def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        """发一次云端请求, 返回 ``(状态码, 响应体)``。

        网络异常收敛成 ``(0, {"error": ...})`` —— 调用方 (HTTP 路由) 据此回 502,
        而不是让异常冒到 aiohttp 中间件变成 500。
        """
        if not self.endpoint:
            return 0, {"error": "auth_endpoint_not_configured"}
        headers: dict[str, str] = {}
        headers["X-Trace-ID"] = trace_id_var.get()
        if auth:
            if not self._token:
                return _UNAUTHORIZED, {"error": "unauthorized"}
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self.endpoint}{self.prefix}{path}"
        try:
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
        except Exception as e:
            logger.warning(f"认证服务请求失败 {method} {path}: {e!r}")
            return 0, {"error": "upstream_unreachable", "detail": repr(e)[:200]}

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
        status, body = await self._call("GET", "/me", auth=True)
        await self._on_response(status)
        return status, body

    async def list_devices(self) -> tuple[int, dict[str, Any]]:
        """列出已登录设备。统一成 ``{"devices": [...]}`` 下发。

        云端回裸数组, ``_call`` 会装进 ``items`` 信封; 在此拆回并落到页面契约的
        ``devices`` 键, 页面不必再猜三种形状。
        """
        status, body = await self._call("GET", "/sessions", auth=True)
        await self._on_response(status)
        if status == 200:
            items = body.get("items")
            if items is None:
                items = body.get("devices") or body.get("sessions") or []
            body = {"devices": items if isinstance(items, list) else []}
        return status, body

    async def revoke_device(self, device_id: str) -> tuple[int, dict[str, Any]]:
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
