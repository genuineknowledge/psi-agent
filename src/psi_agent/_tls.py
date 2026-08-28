"""出站 HTTPS 的 TLS 上下文。**唯一存在理由是绕开一条真实的连不上。**

2026-08-15 负责人机器上, 所有发往云端的请求 (认证 ``/auth/*`` 与免费模型
``/llm/v1``) 一律超时, 而同一时刻 ``curl`` 同一域名 0.7s 就回。

逐层排掉了: aiohttp connector 配置、地址族、请求顺序、事件循环 (Proactor 与
Selector 都 0/5)、asyncio 本身 (同步 ``http.client`` 一样失败)、Python 整体
(换 baidu 4/4 通)、TLS 版本 (1.2 与 1.3 都 0/6)。背靠背对比才定位到::

    ecdh=prime256v1  8/8 通        默认组列表  0/8 全超时

**成因**: OpenSSL 3.5 的默认组列表带上了后量子混合密钥交换 (X25519MLKEM768),
ClientHello 因此撑过 ~1400 字节被分片, 路径上有设备把分片的握手包丢了。
``curl`` 走的是 Schannel、不发这个密钥份额, 所以它通 —— 这就是「curl 行、
Python 不行」的由来, 与本产品的代码无关。

**为什么放在顶层而不是各用各的**: 出站 HTTPS 有两条独立的路, 分属两个进程 ——
Gateway 的 ``AuthManager`` (aiohttp) 与 AI 层转发 (any-llm 内部的 httpx)。两条都
中招, 修一条另一条照样在登录成功后第一次对话时超时。同一个成因不该有两份注释。

量的时候必须每次新连接: keepalive 会掩盖握手失败率 (一次成功后连接复用, 后续
看不见), 当时默认组列表看着像 7/8, 实际 0/8。

---

2026-08-27 第二条真实的连不上, 与上面那条无关, 只是恰好共用这个上下文。冻结成
macOS 包发出去后, 认证请求全数 ``CERTIFICATE_VERIFY_FAILED: unable to get local
issuer certificate``, 前端只显示「无法连接认证服务」。

**成因**: ``create_default_context()`` 在 macOS 上不读钥匙串, 只读 OpenSSL 编译期
写死的那几个路径; 冻结包里没有那些路径, 于是信任库是空的。Windows 有系统证书库
兜底 (实测 cafile 路径不存在仍加载到 56 张), macOS 没有 —— 这就是同一份代码
「Windows 能登录、macOS 不能」的由来。

修法见 ``_load_ca_bundle``: 必须是**并集**。
"""

from __future__ import annotations

import ssl
from pathlib import Path

# 只能填单条经典曲线: ``SSLContext.set_groups`` 在 Python 3.14.7 上不存在,
# ``"X25519:prime256v1"`` 与 ``"x25519"`` 都被 ``set_ecdh_curve`` 判为未知曲线。
# 取 P-256: 各端普遍支持, 且它把 ClientHello 压回一个包。
_CURVE = "prime256v1"


def _load_ca_bundle(ctx: ssl.SSLContext) -> None:
    """把 certifi 的根证书**补进** ``ctx``, 系统那份原样保留。

    只能用 ``load_verify_locations`` (并集), 不能用
    ``create_default_context(cafile=certifi.where())`` (替换)。实测本机: 系统默认
    56 张, 并集后 154 张, 而 ``cafile=`` 只剩 118 张 —— 少掉的是企业 MDM 下发的根和
    TLS 审查设备的根。把它们换掉, 就是把「macOS 登录不了」修成「装了 MDM 的机器
    什么都连不上」, 而后者更难查。

    系统证书库缺失不是错误: macOS 冻结包里本来就没有, 这正是要补的场景。

    **取不到就静默退回系统信任库**, 不往外抛。取不到只发生在冻结时漏收集
    ``cacert.pem`` 的情况下, 而那时 Windows 靠系统证书库照样能连 —— 在这里抛异常
    会把「一个平台连不上」升级成「所有平台起不来」。失败路径有三条, 都得堵:
    ``import`` 失败、``where()`` 自己抛 (它走 ``importlib.resources``, 资源缺失时抛
    而不是回坏路径)、以及路径在但不是文件。
    """
    try:
        import certifi  # noqa: PLC0415  # 只在建上下文时用到, 不值得拖慢 import psi_agent

        where = certifi.where()
    # 宽捕获是刻意的: 三条失败路径的异常类型都不由我们决定, 而它们的处置完全相同。
    except Exception:
        return
    if not Path(where).is_file():
        return
    ctx.load_verify_locations(cafile=where)


def client_ssl_context() -> ssl.SSLContext:
    """出站 HTTPS 用的 TLS 上下文: 默认校验全保留, 只收窄密钥交换曲线。

    从 ``create_default_context()`` 起手, **证书校验与主机名核对一个都不动** ——
    这里要解决的是握手包过大被丢, 不是校验太严。
    """
    ctx = ssl.create_default_context()
    ctx.set_ecdh_curve(_CURVE)
    _load_ca_bundle(ctx)
    return ctx
