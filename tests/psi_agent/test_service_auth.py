"""``psi_agent._service_auth`` —— channel → Gateway 的签名判据。

判据本身很短, 但它是 ``/feishu/route`` 一族**唯一**的准入闸门 (那条按需 spawn 会话并回内部
管道路径), 所以每种「不算数」的形态都要有一条: 空 secret / 缺头 / 时间戳非数字 / 时间戳过期 /
签名不符 / body 被改 / path 被改。时钟与随机数都由参数注入, 用例不依赖真实时间。
"""

from __future__ import annotations

import time

from psi_agent._service_auth import (
    MAX_SKEW_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    canonical_string,
    sign,
    signature,
    verify,
)

SECRET = "app-secret-under-test"
BODY = b'{"open_id":"ou_alice","ai_id":"ai1"}'
PATH = "/feishu/route"


def _signed(*, secret: str = SECRET, body: bytes = BODY, path: str = PATH) -> dict[str, str]:
    return sign(secret, method="POST", path=path, body=body)


def test_sign_then_verify_round_trips() -> None:
    headers = _signed()
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers=headers) is True


def test_timestamp_header_is_generated_when_absent() -> None:
    """调用方不必自己管时钟 —— 也不会有「忘了带时间戳」这种失败态。"""
    headers = _signed()
    assert headers[TIMESTAMP_HEADER].isdigit()
    assert abs(int(headers[TIMESTAMP_HEADER]) - int(time.time())) <= 5
    assert len(headers[SIGNATURE_HEADER]) == 64  # sha256 hex


def test_method_is_case_insensitive_and_part_of_the_signature() -> None:
    headers = _signed()
    assert verify(SECRET, method="post", path=PATH, body=BODY, headers=headers) is True
    assert verify(SECRET, method="GET", path=PATH, body=BODY, headers=headers) is False


def test_verify_rejects_wrong_secret() -> None:
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers=_signed(secret="other")) is False


def test_verify_rejects_empty_secret() -> None:
    """**没配凭证必须是拒绝, 不是放行** —— 空密钥下人人算得出同一个签名。"""
    assert verify("", method="POST", path=PATH, body=BODY, headers=_signed(secret="")) is False


def test_verify_rejects_missing_headers() -> None:
    headers = _signed()
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers={}) is False
    assert (
        verify(SECRET, method="POST", path=PATH, body=BODY, headers={SIGNATURE_HEADER: headers[SIGNATURE_HEADER]})
        is False
    )
    assert (
        verify(SECRET, method="POST", path=PATH, body=BODY, headers={TIMESTAMP_HEADER: headers[TIMESTAMP_HEADER]})
        is False
    )


def test_verify_rejects_non_numeric_timestamp() -> None:
    """时间戳不是数字时**不能**退化成「没有时间戳也算」——那正是重放窗口无限长。"""
    headers = {**_signed(), TIMESTAMP_HEADER: "not-a-number"}
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers=headers) is False


def test_verify_rejects_timestamp_outside_the_skew_window() -> None:
    now = time.time()
    fresh = str(int(now))
    headers = sign(SECRET, method="POST", path=PATH, body=BODY, timestamp=fresh)
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers=headers, now=now) is True
    # 窗口内 (边界两侧) 照收; 超出即拒 —— 这正是「抓到旧请求重放」的上界。
    inside = now + MAX_SKEW_SECONDS - 1
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers=headers, now=inside) is True
    outside = now + MAX_SKEW_SECONDS + 1
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers=headers, now=outside) is False
    assert verify(SECRET, method="POST", path=PATH, body=BODY, headers=headers, now=now - MAX_SKEW_SECONDS - 1) is False


def test_verify_rejects_tampered_body_or_path() -> None:
    headers = _signed()
    tampered = BODY.replace(b"ou_alice", b"ou_mallory")
    assert verify(SECRET, method="POST", path=PATH, body=tampered, headers=headers) is False
    assert verify(SECRET, method="POST", path="/feishu/routes", body=BODY, headers=headers) is False


def test_empty_body_has_a_defined_digest() -> None:
    """空体也要签得住 (GET 没有 body): 摘要不是空串, 否则空体与「没签」长得一样。"""
    headers = sign(SECRET, method="GET", path="/feishu/routes")
    assert verify(SECRET, method="GET", path="/feishu/routes", headers=headers) is True
    assert verify(SECRET, method="GET", path="/feishu/routes", body=b"x", headers=headers) is False


def test_canonical_string_pins_field_order() -> None:
    """规范串的顺序与分隔符是契约: 顺序一改, 两侧各自「自洽」却互相验不过。"""
    assert canonical_string(method="post", path=PATH, body=BODY, timestamp="1700000000").split("\n")[:3] == [
        "1700000000",
        "POST",
        PATH,
    ]
    assert signature(SECRET, method="POST", path=PATH, body=BODY, timestamp="1") == signature(
        SECRET, method="post", path=PATH, body=BODY, timestamp="1"
    )
