"""OAuthRelay —— OAuth 回调中继, 让「授权码」自己回到发起方, 免用户手工复制。

问题: 授权码流程里第三方(飞书等)只会把 ``code`` 拼在 ``redirect_uri`` 上跳一次浏览器。
若没人监听那个地址, 用户就只能自己看地址栏、把 code 粘回给 agent —— 每次授权都要手工搬一次。

本模块给 Gateway 加一个**极小的**回调中继: 浏览器跳到 ``/oauth/callback`` 时把 ``code``
按 ``state`` 暂存(带 TTL, 一次取走即删), 发起方(workspace 工具, 通常在另一个进程)用同一个
``state`` 从 ``/oauth/code`` 取回。Gateway 侧刻意**不碰 token 交换**: 不知道 app_secret,
不知道 PKCE verifier, 也不知道哪个飞书用户 —— 那些都留在发起方, 中继只搬运一次性 code。

``state`` 由发起方生成的高熵随机串充当取件码; 因此本模块**不做**跨用户鉴权, 也不落盘。

本模块的这条性质**只描述 OAuth 中继这条路径**。网页应用免登 (``_auth.py``) 是另一条路径,
那里 Gateway 确实持有 app_secret 并亲自换 token —— 因为免登的 code 只能由服务端换 (放前端
等于公开 secret), 而中继搬运的 code 属于发起方, Gateway 没有理由知道那边的 secret。

为什么住在 ``feishu/`` 而不是骨架层
----------------------------------
这 69 行里确实零飞书字样 —— 但**判据不能只看代码认识什么, 还要看存在性**: 取件方
(实测)全在 ToB 一侧 (``agents/feishu/tools/`` 下 ``_oauth_receiver`` / ``_oauth_setup`` /
``feishu_auth`` / ``_feishu/auth``), ToC 那两处 (``desktop/_auth_manager.py:7`` 与 ``:16``)
只是注释、零调用 —— ToC 的登录走手机号 + 验证码, 不经过 OAuth 跳转。

先前把它留在骨架层的理由是 ``_auth_manager.py:16`` 那句「跳转留给将来的 OAuth」, 那是拿
一句注释里的将来计划去支撑一个当下的位置决定, 违背「先问存在性、不为假想需求预留」。
等 ToC 真要用 OAuth 那天再往骨架层提 —— 那时是有真实需求驱动的移动。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import anyio
from loguru import logger

# code 在飞书侧的有效期是 5 分钟, 中继侧给 10 分钟上限即足够宽松。
_TTL_SECONDS = 600.0
# 防内存无界增长: 超过此数即先清理过期项, 仍超则拒收最旧的。
_MAX_PENDING = 256


@dataclass
class _Pending:
    code: str
    error: str
    created_at: float


@dataclass
class OAuthRelay:
    """按 ``state`` 暂存回调结果的一次性信箱 (进程内存, 不持久化)。"""

    _pending: dict[str, _Pending] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)

    def _sweep(self, now: float) -> None:
        """就地清理过期项 (调用方须持锁)。"""
        stale = [s for s, p in self._pending.items() if now - p.created_at > _TTL_SECONDS]
        for s in stale:
            del self._pending[s]

    async def deliver(self, state: str, *, code: str = "", error: str = "") -> None:
        """回调到达: 把 ``code`` (或错误) 挂到 ``state`` 名下等发起方来取。"""
        if not state:
            raise ValueError("state must not be empty")
        now = time.monotonic()
        async with self._lock:
            self._sweep(now)
            if len(self._pending) >= _MAX_PENDING:
                oldest = min(self._pending, key=lambda s: self._pending[s].created_at)
                del self._pending[oldest]
                logger.warning("OAuthRelay: pending 已满, 丢弃最旧的一条待取回调")
            self._pending[state] = _Pending(code=code, error=error, created_at=now)
        logger.info(f"OAuthRelay: 收到回调 state={state[:8]}… (error={error!r})")

    async def take(self, state: str) -> _Pending | None:
        """发起方取件: 命中即返回并**删除** (一次性), 未到达返回 ``None``。"""
        if not state:
            return None
        now = time.monotonic()
        async with self._lock:
            self._sweep(now)
            return self._pending.pop(state, None)
