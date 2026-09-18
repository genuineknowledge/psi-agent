"""Feishu H5 JSAPI config signing (``tt.config``).

Official flow (web app step 2):

1. ``POST /open-apis/auth/v3/tenant_access_token/internal`` -> tenant token
2. ``POST /open-apis/jssdk/ticket/get`` with ``Authorization: Bearer <token>``
   -> jsapi_ticket
3. sha1(``jsapi_ticket&noncestr&timestamp&url``) -> signature

The ticket endpoint is rate limited, so this module caches both the tenant token
and the ticket globally per Gateway process and only refreshes them near expiry.

``app_secret`` never leaves this module: the HTTP route returns the signed
parameters, not the ticket or credentials.

**刻意为之 (2026-09-18 由部署方确认): 不校验来源 host, 也不做来源白名单。**
``config_for_url`` 只为**前端交来的那个 url** 签名, 只校验它是绝对 http(s) 地址 —— 于是
``GET /feishu/jsapi/config?url=https://任意域名/x`` 也会拿到一份为该 URL 算出的签名。

理由与边界, 写给将来想"顺手加个白名单"的人:

* **飞书 JSAPI 的签名对象就是页面 URL 本身**。前端传的是 ``location.href``, 而同一个部署
  完全可能经多个地址被打开(公网域名 / 内网域名 / 带端口 / 飞书客户端内嵌浏览器), 一份
  硬编码的 host 白名单会在**合法**场景下把免登打断, 而它挡不住真正的攻击者 —— 签名只在
  **那个 URL** 的页面上有效, 攻击者拿自己的域名来要签名, 得到的签名也只对自己的页面有效;
* **这一层不下发任何凭据**: 响应里没有 jsapi_ticket, 没有 ``app_secret``, 只有 appId
  (本来就是公开值)、时间戳、随机串与签名 —— 见下面的 :meth:`FeishuJsapiSigner.config_for_url`;
* 因此这条路由与 ``/feishu/route`` 一族**不是一类**: 后者能 spawn 会话、能回内部管道路径,
  所以那边加了服务间签名(见 ``psi_agent._service_auth``); 这条只做一次纯计算。

要收紧的话, 该收紧的地方是**频控与来源**, 而那是反代 (``deploy/haitun/oauth-proxy.py``)
或产品层的决定, 不是这个函数的判据 —— 这个函数只认识 URL, 不认识"谁在问"。
行为由 ``tests/psi_agent/gateway/test_feishu_jsapi.py`` 钉住, 别当缺陷"修"掉。
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, cast

import anyio
from aiohttp import ClientError, ClientSession, ClientTimeout

TENANT_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
"""官方「自建应用获取 tenant_access_token」接口。"""

JSSDK_TICKET_URL = "https://open.feishu.cn/open-apis/jssdk/ticket/get"
"""官方「获取 JSAPI 临时授权凭证」接口。"""

_HTTP_TIMEOUT = ClientTimeout(total=10)

#: 过期前提前刷新, 避免边界时刻恰好用过期的 token/ticket 被飞书拒绝。
_CACHE_MARGIN_S = 300.0


class JsapiError(Exception):
    """凭证获取、上游调用或 URL 参数失败。路由层映射成 4xx。"""


def signature_for(ticket: str, nonce: str, timestamp: str, url: str) -> str:
    """按官方文档拼接并生成 SHA1 签名。

    拼接顺序是 ``jsapi_ticket&noncestr&timestamp&url``, 顺序错了签名必失败。
    """
    verify_str = "&".join((ticket, nonce, timestamp, url))
    return hashlib.sha1(verify_str.encode("utf-8")).hexdigest()


@dataclass
class FeishuJsapiSigner:
    """为当前页面 URL 生成 ``window.tt.config`` 所需参数。"""

    app_id: str = ""
    app_secret: str = ""

    _tenant_token: str = ""
    _tenant_token_expires_at: float = 0.0
    _ticket: str = ""
    _ticket_expires_at: float = 0.0
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    async def config_for_url(self, raw_url: str) -> dict[str, str]:
        """签名 ``url`` (不含 fragment), 返回前端 ``tt.config`` 需要的 camelCase 参数。"""
        url = (raw_url or "").strip().split("#", 1)[0]
        if not url.startswith("http://") and not url.startswith("https://"):
            raise JsapiError("url query parameter must be an absolute http(s) URL")
        if not self.configured:
            raise JsapiError("Feishu app credentials are not configured on the Gateway")

        async with self._lock, ClientSession(timeout=_HTTP_TIMEOUT) as http:
            tenant_token = await self._ensure_tenant_token(http)
            ticket = await self._ensure_ticket(http, tenant_token)

        nonce = secrets.token_urlsafe(16)
        timestamp = str(int(time.time()))
        return {
            "appId": self.app_id,
            "timestamp": timestamp,
            "nonceStr": nonce,
            "signature": signature_for(ticket, nonce, timestamp, url),
            "url": url,
        }

    async def _ensure_tenant_token(self, http: ClientSession) -> str:
        if self._tenant_token and time.monotonic() < self._tenant_token_expires_at:
            return self._tenant_token
        data = await self._post_json(
            http,
            TENANT_TOKEN_URL,
            body={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise JsapiError("Feishu returned no tenant_access_token")
        expires_in = _positive_int(data.get("expire"))
        self._tenant_token = token
        self._tenant_token_expires_at = time.monotonic() + max(expires_in - _CACHE_MARGIN_S, 60)
        return token

    async def _ensure_ticket(self, http: ClientSession, tenant_token: str) -> str:
        if self._ticket and time.monotonic() < self._ticket_expires_at:
            return self._ticket
        data = await self._post_json(
            http,
            JSSDK_TICKET_URL,
            headers={"Authorization": f"Bearer {tenant_token}"},
        )
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise JsapiError("Feishu ticket response data is not a JSON object")
        ticket = str(payload.get("ticket") or "")
        if not ticket:
            raise JsapiError("Feishu returned no jsapi_ticket")
        expires_in = _positive_int(payload.get("expire_in"))
        self._ticket = ticket
        self._ticket_expires_at = time.monotonic() + max(expires_in - _CACHE_MARGIN_S, 60)
        return ticket

    async def _post_json(
        self,
        http: ClientSession,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {"Content-Type": "application/json; charset=utf-8"}
        request_headers.update(headers or {})
        try:
            async with http.post(url, json=body or {}, headers=request_headers) as resp:
                payload = await resp.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as e:
            raise JsapiError(f"Feishu JSAPI request failed: {e}") from e
        return _unwrap(payload)


def _unwrap(payload: object) -> dict[str, Any]:
    """飞书失败时 HTTP 仍是 200, 成功判据只能是 body 的 ``code == 0``。"""
    if not isinstance(payload, dict):
        raise JsapiError("Feishu JSAPI response is not a JSON object")
    body = cast(dict[str, Any], payload)
    code = body.get("code")
    if code not in (0, None):
        msg = body.get("msg") or body.get("error_description") or body.get("error") or ""
        raise JsapiError(f"Feishu JSAPI rejected (code={code}): {msg}")
    if code is None and body.get("error"):
        raise JsapiError(f"Feishu JSAPI rejected: {body.get('error_description') or body['error']}")
    return body


def _positive_int(value: object) -> int:
    if isinstance(value, bool):
        return 7200
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value)
        except ValueError:
            number = 0
    else:
        number = 0
    return number if number > 0 else 7200
