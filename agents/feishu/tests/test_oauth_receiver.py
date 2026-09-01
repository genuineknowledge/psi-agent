"""OAuth 回调自动接收: 通道选择 + 回环监听真收一次回调。"""

from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_rx: Any = importlib.import_module("_oauth_receiver")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSI_OAUTH_CALLBACK_BASE", raising=False)
    monkeypatch.delenv("PSI_OAUTH_LOOPBACK_PORT", raising=False)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_gateway_channel_wins_when_base_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "https://gw.example.com/")
    plan = _rx.plan_receiver("")
    assert plan.mode == "gateway"
    # 尾斜杠不能带出双斜杠路径
    assert plan.redirect_uri == "https://gw.example.com/oauth/callback"
    assert plan.automatic is True


def test_loopback_channel_when_no_gateway_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_OAUTH_LOOPBACK_PORT", str(_free_port()))
    plan = _rx.plan_receiver("")
    assert plan.mode == "loopback"
    assert plan.redirect_uri.startswith("http://127.0.0.1:")
    assert plan.redirect_uri.endswith("/oauth/callback")


def test_manual_when_loopback_port_taken(monkeypatch: pytest.MonkeyPatch) -> None:
    """端口被占就别抢 —— 退回手工贴码, 而不是绑失败炸掉。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        monkeypatch.setenv("PSI_OAUTH_LOOPBACK_PORT", str(held.getsockname()[1]))
        plan = _rx.plan_receiver("")
    assert plan.mode == "manual"
    assert plan.automatic is False


def test_explicit_loopback_redirect_is_still_automatic() -> None:
    port = _free_port()
    plan = _rx.plan_receiver(f"http://127.0.0.1:{port}/oauth/callback")
    assert plan.mode == "loopback"
    assert plan.redirect_uri == f"http://127.0.0.1:{port}/oauth/callback"


def test_explicit_public_redirect_falls_back_to_manual(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式登记的公网地址我们无从监听 —— 尊重它, 但只能手工贴码。"""
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "https://gw.example.com")
    plan = _rx.plan_receiver("https://other.example.com/cb")
    assert plan.mode == "manual"
    assert plan.redirect_uri == "https://other.example.com/cb"


def test_bad_loopback_port_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_OAUTH_LOOPBACK_PORT", "not-a-port")
    assert _rx.loopback_port() == 17860
    monkeypatch.setenv("PSI_OAUTH_LOOPBACK_PORT", "99999")
    assert _rx.loopback_port() == 17860


async def _get(port: int, target: str) -> str:
    """最小 HTTP GET, 返回响应首行 (不引入额外依赖)。"""
    stream = await anyio.connect_tcp("127.0.0.1", port)
    async with stream:
        await stream.send(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
        raw = b""
        while True:
            try:
                chunk = await stream.receive(4096)
            except anyio.EndOfStream:
                break
            if not chunk:
                break
            raw += chunk
    return raw.decode("utf-8", "replace")


@pytest.mark.asyncio
async def test_loopback_receives_code_end_to_end() -> None:
    port = _free_port()
    result: dict[str, str] = {}

    async with anyio.create_task_group() as tg:

        async def _wait() -> None:
            result.update(await _rx.wait_loopback(port, "st-1", 10.0))

        tg.start_soon(_wait)
        await anyio.sleep(0.3)
        resp = await _get(port, "/oauth/callback?code=C0DE&state=st-1")

    assert "200 OK" in resp
    assert "授权成功" in resp
    assert result == {"code": "C0DE"}


@pytest.mark.asyncio
async def test_loopback_rejects_state_mismatch() -> None:
    """state 不匹配的回调不能顶替真结果 —— 回 400 且继续等真回调。"""
    port = _free_port()
    result: dict[str, str] = {}

    async with anyio.create_task_group() as tg:

        async def _wait() -> None:
            result.update(await _rx.wait_loopback(port, "st-real", 10.0))

        tg.start_soon(_wait)
        await anyio.sleep(0.3)
        bad = await _get(port, "/oauth/callback?code=EVIL&state=st-forged")
        assert "400" in bad
        assert not result
        good = await _get(port, "/oauth/callback?code=GOOD&state=st-real")
        assert "200 OK" in good

    assert result == {"code": "GOOD"}


@pytest.mark.asyncio
async def test_loopback_records_error_callback() -> None:
    port = _free_port()
    result: dict[str, str] = {}

    async with anyio.create_task_group() as tg:

        async def _wait() -> None:
            result.update(await _rx.wait_loopback(port, "st-1", 10.0))

        tg.start_soon(_wait)
        await anyio.sleep(0.3)
        resp = await _get(port, "/oauth/callback?error=access_denied&state=st-1")

    assert "400" in resp
    assert result.get("error") == "access_denied"


@pytest.mark.asyncio
async def test_loopback_times_out_without_callback() -> None:
    assert await _rx.wait_loopback(_free_port(), "st-1", 0.2) == {}


@pytest.mark.asyncio
async def test_poll_gateway_without_base_is_noop() -> None:
    assert await _rx.poll_gateway("st-1", 1.0) == {}
