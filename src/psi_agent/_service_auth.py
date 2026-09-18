"""飞书 channel ↔ Gateway 的服务间鉴权 —— ``/feishu/route`` 一族的调用凭据。

## 为什么需要它

``POST /feishu/route`` 与 ``GET /feishu/routes`` 是**进程间**接口: 前者按需 spawn 会话并
回内部管道路径, 后者列出全部「飞书会话 → Session」映射。此前两条都一行鉴权都没有 ——
浏览器那套 cookie 鉴权 (``_require_identity``) 在这里用不上, 因为调用方是 channel 进程,
它没有、也不该有一份用户登录态。

代价是: 任何能打到 gateway 端口的东西 (同容器里的其它进程, 开发机上任何本地进程) 都能
凭空 spawn 会话并读到内部管道路径。云端还有一层反代白名单挡着
(``deploy/haitun/oauth-proxy.py`` 刻意不含这两条), 但那是**部署形态**的缓解, 不是这条
路由自身的判据 —— 判据必须在 handler 里。

## 判据: 拿两边本来就有的 app_secret 做 HMAC

channel 与 gateway 本就共享飞书应用的 ``app_secret`` (前者跑机器人、后者做免登, 都从同一
份 ``.env`` 取), 于是不必新造凭据、也不必让部署方多配一项: 请求方按
``HMAC-SHA256(app_secret, timestamp\\nMETHOD\\npath_qs\\nsha256(body))`` 签名, 服务端用同一份
secret 重算。**没有 secret 的调用方签不出来** —— 它不是用户身份, 也不是可猜的常量。

刻意不做的事:

* **不新开环境变量**。多一个「忘了配就静默失效」的开关, 而这里已经有一份高熵共享密钥。
* **不接受 cookie 身份作为替代**。这两条路由跨用户 (给出的是**所有人**的路由表与 spawn
  能力), 用户身份不是这套接口的合法凭据。
* **secret 为空即拒绝**。空密钥下 HMAC 退化成人人可算的常量, 「没配好」必须表现成 401,
  不能表现成放行。

## 重放

时间戳窗口 ``MAX_SKEW_SECONDS`` 把重放限制在 5 分钟内, 且签名覆盖 body 摘要 —— 改一个
字节就验不过。窗口内重放要能读到 loopback 流量 (需要同机特权), 这一层不再加 nonce 缓存:
那会引入一份**进程内状态**, 而本模块刻意是纯函数。

放在 ``psi_agent`` 顶层 (与 ``_feishu_routing`` / ``_send_markers`` 同级) 而非任一组件内,
避免在 Gateway 与 Channel 之间新造一条跨组件依赖。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

#: 签名头。名字带 ``PSI-`` 前缀, 与 ``X-`` 那一族第三方头区分开。
SIGNATURE_HEADER = "X-PSI-Service-Signature"

#: Unix 秒 (UTC) 的十进制字符串。**参与签名**, 故改它等于改签名。
TIMESTAMP_HEADER = "X-PSI-Service-Timestamp"

#: 允许的时钟偏差 (秒)。取 5 分钟: 足够吸收两台机器的时钟漂移 (生产 gateway 与 channel
#: 同容器, 但开发机上 channel 可能在别的 shell 里), 又短到「抓到一次旧请求还能重放」的
#: 窗口很小。
MAX_SKEW_SECONDS = 300


def body_digest(body: bytes) -> str:
    """请求体的 SHA-256 十六进制摘要。空体也有确定值 (不是空串)。"""
    return hashlib.sha256(body).hexdigest()


def canonical_string(*, method: str, path: str, body: bytes, timestamp: str) -> str:
    """待签名的规范串。**顺序与分隔符是契约**, 两边必须逐字一致。

    ``path`` 是 **path + query** (aiohttp 的 ``request.path_qs``): 只签 path 的话, 将来某条
    路由加了 query 就能被随意改写而签名照样通过。当前这两条路由没有 query, 故两种写法
    在今天的请求上等价 —— 选前者是为了不给后来人留坑。
    """
    return "\n".join((timestamp, method.upper(), path, body_digest(body)))


def signature(secret: str, *, method: str, path: str, body: bytes = b"", timestamp: str) -> str:
    """按 *secret* 算出十六进制签名。"""
    return hmac.new(
        secret.encode("utf-8"),
        canonical_string(method=method, path=path, body=body, timestamp=timestamp).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sign(
    secret: str,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    timestamp: str = "",
) -> dict[str, str]:
    """请求方用: 产出该带的两条头。

    ``timestamp`` 省略即取当前时刻 —— 调用方不必自己管时钟, 也不会有「忘了带时间戳」
    这种失败态 (服务端拿到空时间戳一律拒, 而这里永远给得出)。
    """
    stamp = timestamp or str(int(time.time()))
    return {
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: signature(secret, method=method, path=path, body=body, timestamp=stamp),
    }


def verify(
    secret: str,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    headers: Mapping[str, str],
    now: float | None = None,
    max_skew_seconds: int = MAX_SKEW_SECONDS,
) -> bool:
    """服务端用: 这份请求是不是持有 *secret* 的一方发的。

    任何一条不成立都返回 ``False`` (不抛异常): 调用方只需把它映射成 401, 而「为什么不算
    数」不该通过异常类型泄漏给请求方 —— 日志里记原因, 响应里只有一句 not authorized。
    """
    if not secret:
        return False
    provided = (headers.get(SIGNATURE_HEADER) or "").strip()
    stamp = (headers.get(TIMESTAMP_HEADER) or "").strip()
    if not provided or not stamp:
        return False
    try:
        sent_at = float(stamp)
    except ValueError:
        return False
    moment = time.time() if now is None else now
    if abs(moment - sent_at) > max_skew_seconds:
        return False
    expected = signature(secret, method=method, path=path, body=body, timestamp=stamp)
    return hmac.compare_digest(expected, provided)
