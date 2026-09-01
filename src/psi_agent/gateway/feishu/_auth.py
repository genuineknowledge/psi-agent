"""飞书网页应用免登 —— 前端交来的 ``code`` 换成**后端认定**的身份。

安全前提只有一条: **open_id 由后端向飞书换回来, 绝不采信前端传的值。** 前端可以伪造
任何 body 字段, 唯一不能伪造的是「飞书认这个 code 属于谁」。因此本模块的输入只有
``code``, 输出是 ``Identity``, 中间不接受任何调用方给的身份提示。

``user_access_token`` 用完即弃
------------------------------
换到 token 后只用它调一次 ``user_info`` 取 ``open_id``/``name``, 随后丢掉, 既不存内存也
不落盘。本产品不需要以用户身份调 OpenAPI (要那个得走增量授权链路, 是另一件事)。这条
取舍顺带满足 ``desktop/_auth_store.py`` 模块头的第 1 条约定: 凭证不进
``state/latest.json`` —— 我们压根没有长期凭证可存。

登录态是一张内存表 ``sid -> (Identity, 过期时刻)``, 不落盘: Gateway 重启后大家重新免登
即可 (在飞书客户端里是无感的一次 JSAPI 调用), 用不着为此引入加密存储。

上游失败判据
------------
飞书这两个接口**失败时 HTTP 仍是 200**, 错误在 body 的 ``code`` 字段 (官方错误码表形如
``200 | 20005 | The user access token passed is invalid``)。只看 HTTP 状态会把伪造的 code
当成功、拿到空 ``open_id``, 于是错误在下游以 500 的形式冒出来。故本模块一律以
``code == 0`` 为成功判据, 其余抛 ``AuthError``, 由路由层映射成 4xx。

``PSI_FEISHU_DEV_OPEN_ID`` 旁路
-------------------------------
本机开发时飞书客户端外没有 JSAPI, 于是留一个环境变量旁路。**默认不设置即完全不可用**,
PR 755 的教训是一个写死的真实 open_id 上云后让所有访问者变成同一个人。旁路只能由部署者
显式打开, 代码里不留任何默认身份。

痕迹分两处, 都是 WARNING: ``warn_if_dev_bypass_enabled()`` 在 Gateway 装配飞书这条线时打
一次(**开发者启动时就能看见**), ``_routes._auth_feishu`` 在每次旁路登录时打一次(旁路**实际
被用了**的痕迹)。``dev_open_id()`` 本身纯读不打日志 —— 读值与留痕是两件事。
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, cast

from aiohttp import ClientError, ClientSession, ClientTimeout
from loguru import logger

TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
"""换 ``user_access_token``。官方已把 v2 标为历史版本, 推荐 v3
``https://accounts.feishu.cn/oauth/v3/token`` (请求/响应结构与 v2 一致, 差别在 PKCE 校验
更严)。这里用 v2 是因为本流程不启用 PKCE, 两者行为等价; 迁 v3 时只改这一个常量。
"""

USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
"""拿 ``open_id`` / ``name``。只需要这两个字段, 故不申请手机号/邮箱等敏感字段权限。"""

DEV_OPEN_ID_ENV = "PSI_FEISHU_DEV_OPEN_ID"

_HTTP_TIMEOUT = ClientTimeout(total=10)


class AuthError(Exception):
    """入参无效或上游拒绝 —— 路由层一律映射成 4xx, 不是 500。"""


@dataclass
class Identity:
    """后端认定的身份。前两个字段是业务需要的, 第三个只为了让前端能显示告警。

    ``via_dev_bypass`` 标记这个身份是不是 ``PSI_FEISHU_DEV_OPEN_ID`` 旁路发的。**它是后端
    的判断, 前端只读不写** —— 与本模块的安全前提一致: 前端伪造不出身份, 也伪造不出「这
    是不是真身份」。

    为什么保留: 它是**响应形状的一部分**(``login`` 与 ``me`` 共用, 见
    ``_routes._identity_payload``), 也是「这个登录态是不是真身份」在服务端的唯一记录 ——
    任何要区分两者的调用方都只能问它。前端曾用它渲染一条常驻告警条, 那条通栏已撤(提示
    改在启动期日志), 但字段与形状约定不动: 撤掉一个渲染用法不等于这个判断没人要了。

    **刷新页面后仍然准** 是它非得存在登录态里的原因 —— 刷新走 ``/feishu/auth/me`` 读
    cookie, 那一次不经过登录分支, 来路只有服务端记着才问得出来。
    """

    open_id: str
    name: str
    via_dev_bypass: bool = False


def dev_open_id() -> str:
    """读开发旁路的 open_id; 未设置或全空白 → 空串 (即旁路不可用)。

    每次调用都重读环境变量而不缓存: 与 ``external_sessions()`` 同一个理由 —— 换来
    「改了 env 重启进程即生效」这条最简单的运维语义。

    **纯读, 不打日志。** 原先它自己在非空时打 WARNING, 于是「读一下这个值」与「留一条
    告警」绑成了一件事: 调用方无从选择, 而 ``_auth_feishu`` 里得留一条注释提醒后人别再打
    第二遍。告警现在由两个调用点各自显式打 (见 ``warn_if_dev_bypass_enabled`` 与
    ``_routes._auth_feishu``), 谁在什么时机留痕因此看得见。
    """
    return (os.environ.get(DEV_OPEN_ID_ENV, "") or "").strip()


def warn_if_dev_bypass_enabled() -> str:
    """**启动期**告警: 旁路开着就打一条 WARNING 并回那个 open_id, 没开则一声不响回空串。

    由 ``register_feishu_routes`` 在装配飞书这条线时调一次。为什么需要它: 旁路此前唯一的
    痕迹是**每次登录**那条 WARNING —— 启动时什么都不打, 于是「这个进程开着旁路」这件事在
    没人登录之前完全不可见。页面上那条常驻告警条已经撤掉(开发者启动时看见就够了, 不必占
    着每个用户的一条通栏), 撤掉之后启动期这条就是开发者唯一的提示, 缺了它等于没提示。

    带上 open_id 本身: 旁路的危害正是「所有访问者都变成这一个人」(PR 755), 看不到是谁就
    判断不了严重程度。
    """
    value = dev_open_id()
    if value:
        logger.warning(
            "FeishuAuth dev bypass is ENABLED at startup via {}={} -- every visitor becomes "
            "this identity; do not use in production",
            DEV_OPEN_ID_ENV,
            value,
        )
    return value


@dataclass
class FeishuAuth:
    """免登的全部状态: 应用凭证 + 一张内存登录态表。

    ``_ttl`` 默认 8 小时 —— 一个工作日。到期后前端在飞书客户端内重新免登是无感的,
    所以不做 refresh_token 那一套 (那要求申请 ``offline_access`` 权限并加密存长期凭证)。
    """

    app_id: str = ""
    app_secret: str = ""
    _sessions: dict[str, tuple[Identity, float]] = field(default_factory=dict)
    _ttl: float = 8 * 3600

    @property
    def configured(self) -> bool:
        """两个凭证都齐才算配好 —— 缺一个就换不到 token。"""
        return bool(self.app_id and self.app_secret)

    async def identity_from_code(self, code: str) -> Identity:
        """``code`` → ``Identity``。任何失败都抛 ``AuthError``。"""
        if not self.configured:
            raise AuthError("Feishu app credentials are not configured on the Gateway")
        if not code or not code.strip():
            raise AuthError("missing code")
        async with ClientSession(timeout=_HTTP_TIMEOUT) as http:
            token = await self._exchange_token(http, code.strip())
            return await self._fetch_user_info(http, token)

    async def _exchange_token(self, http: ClientSession, code: str) -> str:
        body = {
            "grant_type": "authorization_code",
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "code": code,
        }
        data = await self._post_json(http, TOKEN_URL, body)
        token = str(data.get("access_token") or "")
        if not token:
            raise AuthError("Feishu returned no access_token")
        return token

    async def _fetch_user_info(self, http: ClientSession, token: str) -> Identity:
        try:
            async with http.get(USER_INFO_URL, headers={"Authorization": f"Bearer {token}"}) as resp:
                payload = await resp.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as e:
            raise AuthError(f"Feishu user_info request failed: {e}") from e
        data = self._unwrap(payload, what="user_info").get("data") or {}
        if not isinstance(data, dict):
            raise AuthError("Feishu user_info response data is not a JSON object")
        open_id = str(data.get("open_id") or "")
        if not open_id:
            # 上游说成功却没给 open_id: 宁可当失败, 也不让空身份流进归属判定。
            raise AuthError("Feishu user_info returned no open_id")
        return Identity(open_id=open_id, name=str(data.get("name") or ""))

    async def _post_json(self, http: ClientSession, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with http.post(
                url,
                json=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            ) as resp:
                payload = await resp.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as e:
            raise AuthError(f"Feishu token request failed: {e}") from e
        return self._unwrap(payload, what="token")

    @staticmethod
    def _unwrap(payload: object, *, what: str) -> dict[str, Any]:
        """校验 ``code == 0``。**失败时飞书的 HTTP 状态仍是 200**, 判据只能是 body。

        错误信息里不回显 ``app_secret`` / token, 只带上游的 code 与 msg: 这些响应会进
        日志与 4xx 响应体。
        """
        if not isinstance(payload, dict):
            raise AuthError(f"Feishu {what} response is not a JSON object")
        # isinstance 只窄化到 dict[Unknown, Unknown]; 键是 JSON 对象的键, 必为 str。
        body = cast(dict[str, Any], payload)
        code = body.get("code")
        if code not in (0, None):
            msg = body.get("msg") or body.get("error_description") or body.get("error") or ""
            raise AuthError(f"Feishu {what} rejected (code={code}): {msg}")
        # v2 token 接口失败时给 ``error``/``error_description`` 而不带 ``code``。
        if code is None and body.get("error"):
            raise AuthError(f"Feishu {what} rejected: {body.get('error_description') or body['error']}")
        return body

    def issue(self, identity: Identity) -> str:
        """签发登录态, 返回高熵 sid (放 HttpOnly cookie, 不可猜)。"""
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = (identity, time.time() + self._ttl)
        return sid

    def lookup(self, sid: str) -> Identity | None:
        """取身份; 过期即删并返回 None (顺手回收, 免得表只增不减)。"""
        if not sid:
            return None
        entry = self._sessions.get(sid)
        if entry is None:
            return None
        identity, expires_at = entry
        if expires_at <= time.time():
            del self._sessions[sid]
            logger.debug("FeishuAuth: login session expired, dropped")
            return None
        return identity

    def revoke(self, sid: str) -> None:
        self._sessions.pop(sid, None)
