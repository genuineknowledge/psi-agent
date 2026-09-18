"""签一份服务间请求 —— 用例共用的助手 (不是测试模块, pytest 不收集它)。

``POST /feishu/route`` 与 ``GET /feishu/routes`` 现在要求 channel 的 HMAC 签名 (见
``psi_agent._service_auth``)。用例要打这两条, 就必须像 channel 那样签; 把「怎么签」收在这里,
免得 4 个文件各写一遍、日后判据变了只改了一半。

**必须用 ``data=`` 而不是 ``json=`` 发**: 签名覆盖**发出去的字节**, 交给 aiohttp 再序列化
一遍就等于签一份、发另一份 —— 今天两者输出恰好相同, 这种巧合不该被用例依赖。
"""

from __future__ import annotations

import json

from psi_agent._service_auth import sign

#: 用例里给 Gateway 与「channel」共用的 app_secret。值与生产无关, 只要是两边同一个。
TEST_APP_SECRET = "test-app-secret-7f3a"


def signed_json_post(
    path: str,
    payload: dict[str, object],
    *,
    secret: str = TEST_APP_SECRET,
) -> tuple[bytes, dict[str, str]]:
    """``(body, headers)`` —— 直接喂给 ``session.post(url, data=body, headers=headers)``。"""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return raw, {
        "Content-Type": "application/json; charset=utf-8",
        **sign(secret, method="POST", path=path, body=raw),
    }


def signed_get(path: str, *, secret: str = TEST_APP_SECRET) -> dict[str, str]:
    """GET 只需签名头, 没有 body。"""
    return sign(secret, method="GET", path=path)
