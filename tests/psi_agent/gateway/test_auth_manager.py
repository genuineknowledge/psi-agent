"""AuthManager 的响应改形逻辑。

这里只测「网关如何改造云端响应」这一层, 不打真实云端: 用 ``monkeypatch`` 顶掉
``_call``, 断言交给页面的 body。两条回归都源自线上实测出来的空数据/弹回。

顶替一律走 ``monkeypatch.setattr``, 不直接赋值 ``m._call = fake``: 后者的签名与
方法不兼容, 类型检查会拦 (而 ``# type: ignore`` 是 mypy 语法, 本仓库用 ``ty``,
压不住)。走 fixture 还能在用例结束时自动还原。
"""

from __future__ import annotations

import socket
import ssl
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey

from psi_agent.gateway._auth_manager import AuthManager, classify_failure


async def _manager(tmp_path: Path) -> AuthManager:
    return await AuthManager.create("https://auth.invalid", appdata_root=str(tmp_path))


def _stub_call(
    monkeypatch: pytest.MonkeyPatch,
    manager: AuthManager,
    body: dict[str, Any],
    status: int = 200,
) -> None:
    """让 ``manager._call`` 恒回 *(status, body)*, 不发真实请求。"""

    async def fake(
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = False,
        retry: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        return status, body

    monkeypatch.setattr(manager, "_call", fake)


@pytest.mark.anyio
async def test_verify_new_user_swaps_temp_token_for_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """注册凭证不下发, 但必须留一个不含凭证的新用户信号。

    回归: 摘掉 ``tempToken`` 却没给替代信号时, 页面判不出「该进建号屏」,
    会把新用户当成登录失败弹回输入页。
    """
    m = await _manager(tmp_path)
    try:
        _stub_call(monkeypatch, m, {"tempToken": "tt-secret", "isNewUser": True})
        status, body = await m.verify(code="123456", phone="13900000000")

        assert status == 200
        assert body["registrationRequired"] is True
        assert "tempToken" not in body, "注册凭证不得下发到页面"
        assert m._pending_temp_token == "tt-secret", "凭证应留在本进程"
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_verify_existing_user_has_no_registration_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = await _manager(tmp_path)
    try:
        _stub_call(monkeypatch, m, {"token": "tok-1", "user": {"id": "u1"}})
        _, body = await m.verify(code="123456", phone="13900000000")

        assert "registrationRequired" not in body, "老用户不该被送进建号屏"
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_list_devices_survives_bare_array(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """云端 ``GET /sessions`` 回裸数组, 不能被当成坏响应丢掉。

    回归: ``_call`` 里「非 dict 即 bad_response」把整个设备列表吃掉,
    界面上「管理登录设备」恒为 (0)。
    """
    m = await _manager(tmp_path)
    try:
        # 模拟 _call 对裸数组的信封化
        _stub_call(monkeypatch, m, {"items": [{"id": "s1", "platform": "windows", "current": True}]})
        status, body = await m.list_devices()

        assert status == 200
        assert [d["id"] for d in body["devices"]] == ["s1"]
    finally:
        await m.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw",
    [
        {"devices": [{"id": "s1"}]},
        {"sessions": [{"id": "s1"}]},
        {"items": [{"id": "s1"}]},
    ],
)
async def test_list_devices_normalizes_three_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
) -> None:
    m = await _manager(tmp_path)
    try:
        _stub_call(monkeypatch, m, raw)
        _, body = await m.list_devices()
        assert [d["id"] for d in body["devices"]] == ["s1"]
    finally:
        await m.aclose()


def _connection_key() -> Any:
    """造一个 ``ConnectionKey`` —— aiohttp 的连接异常都要求带它。"""
    return ConnectionKey(
        host="auth.invalid",
        port=443,
        is_ssl=True,
        ssl=None,
        proxy=None,
        proxy_auth=None,
        proxy_headers_hash=None,
    )


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (
            lambda ck: aiohttp.ClientConnectorCertificateError(
                ck, ssl.SSLCertVerificationError("unable to get local issuer certificate")
            ),
            "tls-certificate",
        ),
        (lambda ck: aiohttp.ClientConnectorSSLError(ck, OSError(1, "handshake")), "tls-handshake"),
        (lambda ck: aiohttp.ClientProxyConnectionError(ck, OSError(1, "refused")), "proxy"),
        (lambda ck: aiohttp.ClientConnectorDNSError(ck, socket.gaierror("nodename")), "dns"),
        (lambda ck: aiohttp.ClientConnectorError(ck, OSError(1, "refused")), "connect"),
        (lambda _ck: aiohttp.ServerTimeoutError("slow"), "timeout"),
        (lambda _ck: TimeoutError(), "timeout"),
        (lambda _ck: ValueError("something else"), "unknown"),
    ],
)
def test_classify_failure_separates_causes(build: Any, expected: str) -> None:
    """四种「连不上」必须落到不同的分类词。

    回归动机: 证书错、代理错都是 ``ClientConnectorError`` 的子类, 泛类若排在具体类
    之前, 全部会被吞成 ``connect``, 于是日志里再也分不出该改 CA bundle 还是改代理。
    这个用例把顺序钉住。

    造真实的 aiohttp 异常实例而不是打桩: 要验的正是「本机装的这个 aiohttp 的继承
    关系下分类仍然正确」, 用假对象测就把被验对象换掉了。
    """
    assert classify_failure(build(_connection_key())) == expected


def test_classify_failure_handles_none() -> None:
    assert classify_failure(None) == "unknown"


@pytest.mark.anyio
async def test_session_honours_proxy_env(tmp_path: Path) -> None:
    """会话必须读 ``HTTPS_PROXY`` —— 否则代理后的机器一律登录不了。

    回归: ``aiohttp.ClientSession`` 的 ``trust_env`` 默认 ``False``, 即默认**无视**
    代理环境变量; 而同一产品里 AI 层走 httpx, 它默认 ``True``。于是同一台机器上
    「对话能通、登录不通」, 而前端只显示「无法连接认证服务」, 看不出是代理没走。

    断言 ``trust_env`` 而不是去发请求: 要固定的是「读不读环境」这个契约, 起一个
    真代理来测等于把一条配置断言换成一个会 flaky 的网络用例。
    """
    m = await _manager(tmp_path)
    try:
        session = m._ensure_session()
        assert session.trust_env is True, "trust_env 为假时 HTTPS_PROXY 被忽略"
    finally:
        await m.aclose()
