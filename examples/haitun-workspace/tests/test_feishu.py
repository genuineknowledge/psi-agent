from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSI_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("PSI_FEISHU_APP_SECRET", raising=False)
    _impl._reset_client()


def test_config_missing_returns_none() -> None:
    assert _impl._config() is None


def test_config_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec_y")
    assert _impl._config() == ("cli_x", "sec_y")


@pytest.mark.asyncio
async def test_invoke_without_auth_returns_error() -> None:
    class _Req:
        pass

    result = await _impl._invoke(_Req())
    assert result["ok"] is False
    assert "PSI_FEISHU_APP_ID" in result["message"]


def test_dumps_result_roundtrip() -> None:
    s = _impl.dumps_result({"ok": True, "data": {"名": "值"}})
    assert json.loads(s)["data"]["名"] == "值"
    assert "\\u" not in s  # ensure_ascii=False


class _FakeRaw:
    def __init__(self, body: bytes) -> None:
        self.content = body
        self.status_code = 200
        self.headers = {}


class _FakeResp:
    def __init__(self, code, msg, body: bytes) -> None:
        self.code = code
        self.msg = msg
        self.raw = _FakeRaw(body)
        self.success = code == 0


class _FakeClient:
    def __init__(self, resp) -> None:
        self._resp = resp

    async def arequest(self, request: Any) -> Any:
        return self._resp


@pytest.mark.asyncio
async def test_invoke_success_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"code": 0, "msg": "ok", "data": {"x": 1}}).encode()
    monkeypatch.setattr(_impl, "_get_client", lambda: _FakeClient(_FakeResp(0, "ok", body)))
    result = await _impl._invoke(object())
    assert result == {"ok": True, "code": 0, "msg": "ok", "data": {"x": 1}}


@pytest.mark.asyncio
async def test_invoke_error_passes_through_code_msg(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"code": 99991672, "msg": "permission denied", "data": {}}).encode()
    monkeypatch.setattr(_impl, "_get_client", lambda: _FakeClient(_FakeResp(99991672, "permission denied", body)))
    result = await _impl._invoke(object())
    assert result["ok"] is False
    assert result["code"] == 99991672
    assert result["msg"] == "permission denied"
    assert "permission denied" in result["message"]
