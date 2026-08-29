"""免费模型的哨兵值与 token 解析。

C 端默认的免费模型走云端转发 (``<账号服务>/llm/v1``), 上游供应商 key 只在云端
持有, 客户端凭**登录态**换算力。但 SPA 拿不到 token 也不该拿 —— token 全程由
Gateway 持有并加密落盘, 登录组件源码里连字面量都不许出现 (防 XSS)。

所以 SPA 填一个哨兵值, Gateway 在拉起 AI 子进程时把它换成真 token。
换出来的 token **只活在 ``Ai`` 实例里**: 不进 ``state/latest.json`` (那里的
api_key 是明文), 不经 ``/ais`` 下发给 SPA。
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from loguru import logger

PLACEHOLDER_API_KEY = "haitun-default"
"""与 SPA 的 ``PLACEHOLDER_API_KEY`` 是同一个契约 (见 ``spa-v2/src/services/
bootstrapAi.ts`` 与 ``spa/src/bootstrapAi.js``)。两边任改一边就会静默失效 ——
免费模型会带着哨兵值原样去请求, 云端回 401。改动时三处一起改。"""


def _origin(url: str) -> str:
    """取 scheme://host:port。取不到就返回空串, 让调用方判定为不匹配。"""
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def is_cloud_free_model(api_key: str, base_url: str, auth_endpoint: str) -> bool:
    """这份配置是否要用登录态换算力。

    两个条件都必须成立:

    1. ``api_key`` 是哨兵值 —— 用户自填了真 key 就走他自己的, 不替换。
    2. ``base_url`` 与认证服务**同源** —— ** token 只能发给签发它的那台主机 **。
       否则用户 (或一份被改过的快照) 只要把 base_url 指向任意域名并填上哨兵,
       Gateway 就会把登录凭证送出去。同源判定让这条路走不通。
    """
    if api_key.strip() != PLACEHOLDER_API_KEY:
        return False
    endpoint_origin = _origin(auth_endpoint)
    if not endpoint_origin:
        return False
    return _origin(base_url) == endpoint_origin


def make_key_resolver(token_of: Callable[[], str], auth_endpoint: str) -> Callable[[str, str], str]:
    """造一个 ``(api_key, base_url) -> key`` 函数, 供 ``AIManager`` 注入。

    ``token_of`` 每次调用都重新读一次登录态, 不缓存 —— socket 重建时 (登录/登出
    之后) 要能拿到当时的新值。
    """

    def resolve(api_key: str, base_url: str) -> str:
        if not is_cloud_free_model(api_key, base_url, auth_endpoint):
            return api_key
        token = token_of()
        if not token:
            # ** 不阻止 socket 起来 **: 免费模型是默认配置, 未登录时也要能起,
            # 否则用户看到的是「模型列表空了」而不是「请先登录」。
            #
            # 但这条路上的报错**很难看**: 空 key 根本走不到云端 —— any-llm 的
            # openai provider 在发请求之前就本地抛
            # ``No openai API key provided. Please provide it in the config or
            # set the OPENAI_API_KEY environment variable``, 一句与本产品毫无
            # 关系的话。(先前这里写着「会拿到云端的 401」, 实测不成立。)
            #
            # 所以真正的兜底在前端: SPA v2 启动即硬门禁, 未登录进不来 (见
            # ``spa-v2/src/haitun-agent/HaiTunAgentWorkspace.tsx`` 的 authGate)。
            # 这一支只在门禁被绕过或认证服务关闭时才会走到。
            logger.info("免费模型: 尚未登录, 交给 AI 层的 key 为空 (请求会在 AI 层本地失败)")
            return ""
        logger.info("免费模型: 已用登录态替换哨兵值")
        return token

    return resolve
