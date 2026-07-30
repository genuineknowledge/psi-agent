"""飞书授权环境自检: 形态判定 + 逐条病因 + 该登记的 URL。"""

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

_setup: Any = importlib.import_module("_oauth_setup")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "PSI_OAUTH_CALLBACK_BASE",
        "PSI_OAUTH_LOOPBACK_PORT",
        "PSI_FEISHU_REDIRECT_URI",
        "PSI_FEISHU_APP_ID",
        "PSI_FEISHU_APP_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "secret_test")
    # 形态判定会看是否在容器里; 固定成「不在」才能稳定断言本机开发分支。
    monkeypatch.setattr(_setup, "_in_container", lambda: False)


def _busy_port() -> tuple[int, socket.socket]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return int(s.getsockname()[1]), s


def test_local_shape_needs_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    result = anyio.run(lambda: _setup.env_check_impl(False))
    assert result["deployment"]["kind"] == "local"
    assert result["mode"] == "loopback"
    assert result["auto_receive"] is True
    assert result["blockers"] == []
    assert result["register_in_console"].startswith("http://127.0.0.1:")


def test_container_counts_as_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """容器内的 127.0.0.1 不是用户浏览器的回环, 必须判成服务器部署。"""
    monkeypatch.setattr(_setup, "_in_container", lambda: True)
    result = anyio.run(lambda: _setup.env_check_impl(False))
    assert result["deployment"]["kind"] == "server"
    assert any("PSI_OAUTH_CALLBACK_BASE" in b["issue"] for b in result["blockers"])


def test_gateway_base_makes_it_automatic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "http://192.168.60.214:8090")
    result = anyio.run(lambda: _setup.env_check_impl(False))
    assert result["mode"] == "gateway"
    assert result["auto_receive"] is True
    assert result["blockers"] == []
    assert result["register_in_console"] == "http://192.168.60.214:8090/oauth/callback"
    assert result["deployment"]["exposure"] == "intranet"


def test_explicit_non_loopback_redirect_is_named_as_the_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    """214 上的真实病根: 显式 redirect 指向失效隧道, 静默把所有人打回手抄。"""
    monkeypatch.setenv("PSI_FEISHU_REDIRECT_URI", "https://dead-tunnel.example.com")
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "http://192.168.60.214:8090")
    result = anyio.run(lambda: _setup.env_check_impl(False))
    assert result["mode"] == "manual"
    assert result["auto_receive"] is False
    joined = " ".join(b["issue"] + b["fix"] for b in result["blockers"])
    assert "PSI_FEISHU_REDIRECT_URI" in joined
    # 必须指出修法是改用 CALLBACK_BASE, 而不是只说一句 manual
    assert "PSI_OAUTH_CALLBACK_BASE" in joined


def test_busy_loopback_port_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    port, sock = _busy_port()
    try:
        monkeypatch.setenv("PSI_OAUTH_LOOPBACK_PORT", str(port))
        result = anyio.run(lambda: _setup.env_check_impl(False))
        assert result["mode"] == "manual"
        assert any(str(port) in b["issue"] for b in result["blockers"])
    finally:
        sock.close()


def test_missing_credentials_are_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSI_FEISHU_APP_SECRET", raising=False)
    result = anyio.run(lambda: _setup.env_check_impl(False))
    assert any("PSI_FEISHU_APP_SECRET" in b["issue"] for b in result["blockers"])
    assert result["config"]["app_secret_set"] is False


def test_secrets_are_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    """体检结果会被原样发进聊天窗口, 密钥只能报存在与否。"""
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "super_secret_value")
    result = anyio.run(lambda: _setup.env_check_impl(False))
    assert "super_secret_value" not in repr(result)
    assert result["config"]["app_secret_set"] is True


def test_unreachable_base_is_caught_by_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """配着但连不上的基址: 只读环境变量看不出来, 探测才抓得到。"""
    port, sock = _busy_port()
    sock.close()  # 立刻释放 -> 该端口上没人监听, 探测必然连不上
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", f"http://127.0.0.1:{port}")
    result = anyio.run(lambda: _setup.env_check_impl(True))
    assert result["callback_probe"]["probed"] is True
    assert result["callback_probe"]["reachable"] is False
    assert any("探测不可达" in b["issue"] for b in result["blockers"])


def test_probe_can_be_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "http://192.168.60.214:8090")
    result = anyio.run(lambda: _setup.env_check_impl(False))
    assert result["callback_probe"] == {"probed": False}


def test_redirect_url_offers_both_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "http://192.168.60.214:8090")
    result = anyio.run(lambda: _setup.redirect_url_impl(False))
    channels = {c["channel"] for c in result["candidates"]}
    assert channels == {"gateway", "loopback"}
    assert result["register_this"] == "http://192.168.60.214:8090/oauth/callback"
    assert any("安全设置" in s for s in result["steps"])


def test_setup_guide_autoselects_by_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    result = anyio.run(lambda: _setup.setup_guide_impl(""))
    assert result["target"] == "local"
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "http://192.168.60.214:8090")
    result = anyio.run(lambda: _setup.setup_guide_impl(""))
    assert result["target"] == "intranet"
    monkeypatch.setenv("PSI_OAUTH_CALLBACK_BASE", "https://haitun.example.com")
    result = anyio.run(lambda: _setup.setup_guide_impl(""))
    assert result["target"] == "public"


def test_setup_guide_target_can_be_forced() -> None:
    """在笔记本上问服务器怎么配, 得给服务器的步骤。"""
    result = anyio.run(lambda: _setup.setup_guide_impl("intranet"))
    assert result["target"] == "intranet"
    joined = " ".join(result["steps"])
    # 服务器指导必须包含「别整个暴露 Gateway」这条安全要求
    assert "/oauth/code" in joined
    assert "sessions" in joined


def test_setup_guide_rejects_unknown_target_by_falling_back() -> None:
    result = anyio.run(lambda: _setup.setup_guide_impl("nonsense"))
    assert result["target"] in ("local", "intranet", "public")
