"""连接与传输层的行为。

与 ``test_auth_manager.py`` 分开: 那边测「云端响应怎么被改造成前端契约」,
这边测「连接怎么建、怎么复用、什么时候重试」。两个关注点, 两个文件。

替换一律走 ``monkeypatch.setattr`` —— 直接赋值 ``m._call = fake`` 的签名不兼容,
``ty`` 会拒, 而本仓库的类型检查器是 ``ty`` 不是 mypy, ``# type: ignore`` 无效。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey

from psi_agent.gateway.desktop._auth_manager import (
    _DNS_CACHE_SECONDS,
    _KEEPALIVE_SECONDS,
    AuthManager,
)


@pytest.mark.anyio
async def test_session_connector_keeps_connection_across_sms_wait(tmp_path: Path) -> None:
    """连接池的 keepalive 必须撑过等短信的间隔, 否则每步都要重新握手。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    try:
        session = m._ensure_session()
        connector = session.connector
        # 收窄到 TCPConnector: session.connector 的静态类型是 BaseConnector | None,
        # 下面要读的字段只在 TCPConnector 上。isinstance 断言让 ty 自己认出来,
        # 不必写 type-ignore 注释 (本仓库用 ty, 那是 mypy 语法, 压不住; 而且写出那个
        # 字面量本身就会被 ty 当成一条真指令, 反倒多报一条 unused 警告)。
        assert isinstance(connector, aiohttp.TCPConnector)
        # 等短信最长约 90s; keepalive 必须比它长, 否则连接在等待期间就被回收了。
        assert _KEEPALIVE_SECONDS > 90.0
        assert connector._keepalive_timeout == _KEEPALIVE_SECONDS
        # 云端地址不变, 没必要每 10s 重新解析一次 DNS。
        assert _DNS_CACHE_SECONDS >= 600
        assert connector._use_dns_cache is True
        # ttl 不在 connector 上, 落在它内部的缓存对象里 (aiohttp 3.14 是
        # _cached_hosts._ttl)。私有属性, 换 aiohttp 版本时这条会先炸 —— 那正是
        # 我们想要的信号: 取值没生效比测试失败更难发现。
        assert connector._cached_hosts._ttl == _DNS_CACHE_SECONDS
        # 同一个 session 复用同一个 connector, 不能每次调用都新建。
        assert m._ensure_session().connector is connector
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_idempotent_get_retries_once_on_stale_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """池里的连接被对端关掉时, GET 重试一次就能成功, 用户看不到失败。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    m._token = "tok"
    calls: list[str] = []

    async def fake_attempt(
        method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        calls.append(path)
        if len(calls) == 1:
            raise aiohttp.ServerDisconnectedError
        return 200, {"ok": True}

    monkeypatch.setattr(m, "_attempt", fake_attempt)
    try:
        status, body = await m.me()
        assert status == 200
        assert body == {"ok": True}
        assert len(calls) == 2  # 第一次撞死连接, 第二次拿新连接成功
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_unreachable_server_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真正连不通时不重试 —— 重试只会白等一个超时周期。

    这是「只捕 ServerDisconnectedError」而不是捕 ClientOSError /
    ClientConnectionError 的理由: ClientConnectorError (DNS 失败、连接被拒)
    是它们的子类, 罩上去连这种情况一起重试。
    """
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    m._token = "tok"
    calls: list[str] = []

    async def fake_attempt(
        method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        calls.append(path)
        # 造个真的 ConnectionKey: 传 None 运行时能过, 但 ty 会拦 (它要 ConnectionKey),
        # 而本仓库不许用 type-ignore 注释压。
        key = ConnectionKey(
            host="example.invalid",
            port=443,
            is_ssl=True,
            ssl=True,
            proxy=None,
            proxy_auth=None,
            proxy_headers_hash=None,
            server_hostname=None,
        )
        raise aiohttp.ClientConnectorError(connection_key=key, os_error=OSError("refused"))

    monkeypatch.setattr(m, "_attempt", fake_attempt)
    try:
        status, body = await m.me()
        assert status == 0
        assert body["error"] == "upstream_unreachable"
        assert len(calls) == 1
    finally:
        await m.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("op", ["send_code", "verify", "complete", "bind"])
async def test_business_post_never_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str) -> None:
    """业务 POST 永不重试。

    验证码被消耗两次后, 前端 D1 兜底屏会说「验证码不正确」—— 而码是对的。
    用户只会一遍遍重输, 真正的原因被那句文案盖住。性能优化就此变成正确性缺陷。
    """
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    m._token = "tok"
    m._pending_temp_token = "tmp"  # complete() 的前置, 否则它在 _call 之前就返回 400
    calls: list[str] = []

    async def fake_attempt(
        method: str, path: str, payload: dict[str, Any] | None = None, *, auth: bool = False
    ) -> tuple[int, dict[str, Any]]:
        calls.append(path)
        raise aiohttp.ServerDisconnectedError

    monkeypatch.setattr(m, "_attempt", fake_attempt)
    ops: dict[str, Callable[[], Awaitable[tuple[int, dict[str, Any]]]]] = {
        "send_code": lambda: m.send_code(phone="13800000000"),
        "verify": lambda: m.verify(code="123456", phone="13800000000"),
        "complete": lambda: m.complete(display_name="x"),
        "bind": lambda: m.bind(code="123456", phone="13800000000"),
    }
    try:
        status, body = await ops[op]()
        assert status == 0
        assert body["error"] == "upstream_unreachable"
        assert len(calls) == 1  # 只发一次, 绝不重试
    finally:
        await m.aclose()


class _FakeTaskGroup:
    """只记下被 ``start_soon`` 的次数, 够测预热的调度与节流。"""

    def __init__(self) -> None:
        self.started = 0

    def start_soon(self, func: object, *args: object) -> None:  # 签名照 anyio.TaskGroup
        # 只计数, **不调用 func** —— 调了会造出一个没人 await 的协程,
        # 冒出 RuntimeWarning: coroutine was never awaited。
        self.started += 1


@pytest.mark.anyio
async def test_warm_401_does_not_clear_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """预热必须绕开 _on_response, 否则每次预热都把已登录用户踢下线。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    m._token = "real-user-token"

    async def fake_call(
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = False,
        retry: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        assert auth is False  # 预热不带 token
        return 401, {"error": "unauthorized"}

    monkeypatch.setattr(m, "_call", fake_call)
    try:
        await m._warm()
        assert m._token == "real-user-token"  # 没被清掉
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_warm_swallows_errors_and_resets_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """预热失败必须自己吞掉。

    异常逃出 start_soon 会拆掉整个 task group, 连带杀死 Gateway —— 为了省一个
    RTT 把进程搞没了。且失败后要复位 _warming, 否则一次失败永久堵死后续预热。
    """
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))

    async def boom(*args: object, **kwargs: object) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("network down")

    monkeypatch.setattr(m, "_call", boom)
    try:
        await m._warm()  # 不抛
        assert m._warming is False
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_nudge_warm_throttles_consecutive_calls(tmp_path: Path) -> None:
    """SPA 挂载登录面板时可能连发几次 /auth/status, 节流保证只热一次。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    tg = _FakeTaskGroup()
    m._tg = tg
    try:
        await m.nudge_warm()
        # 手工复位 _warming: 假 task group 不真跑 _warm, 标志不会自己落下。
        # 不复位的话这个用例考的是 _warming 那道闸, 而不是节流那道 —— 两道闸都要考。
        m._warming = False
        await m.nudge_warm()
        m._warming = False
        await m.nudge_warm()
        assert tg.started == 1  # 节流窗口内的连发只热一次
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_nudge_warm_without_task_group_is_silent(tmp_path: Path) -> None:
    """没注入 task group 时静默跳过 —— 预热是优化, 缺了不算故障。"""
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    try:
        await m.nudge_warm()  # 不抛
        assert m._warming is False
    finally:
        await m.aclose()


@pytest.mark.anyio
async def test_create_accepts_task_group(tmp_path: Path) -> None:
    """create(tg=...) 把 task group 存下来; 不传时行为不变 (仍能建、仍不预热)。"""
    tg = _FakeTaskGroup()
    m = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path), tg=tg)
    try:
        assert m._tg is tg
    finally:
        await m.aclose()

    plain = await AuthManager.create("https://example.invalid", appdata_root=str(tmp_path))
    try:
        assert plain._tg is None
    finally:
        await plain.aclose()
