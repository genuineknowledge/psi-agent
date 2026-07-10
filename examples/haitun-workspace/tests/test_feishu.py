from __future__ import annotations

import importlib
import inspect
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


class _CapturedInvoke:
    """Replace _invoke; record the BaseRequest, return a canned success dict."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.request: Any = None
        self._data = data or {}

    async def __call__(self, request: Any) -> dict[str, Any]:
        self.request = request
        return {"ok": True, "code": 0, "msg": "", "data": self._data}


def _qdict(req: Any) -> dict[str, str]:
    """SDK stores queries as list[tuple[str, str]] with str-coerced values."""
    return dict(req.queries)


@pytest.mark.asyncio
async def test_add_comment_builds_create_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"comment_id": "c1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.add_comment_impl("tok", "docx", "hello")
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.paths["file_token"] == "tok"
    assert _qdict(req).get("file_type") == "docx"


@pytest.mark.asyncio
async def test_list_comments_passes_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.list_comments_impl("tok", "docx", 25, "pt1")
    q = _qdict(cap.request)
    assert q.get("page_size") == "25"  # add_query coerces to str
    assert q.get("page_token") == "pt1"
    assert q.get("is_whole") == "true"


@pytest.mark.asyncio
async def test_reply_replies_list_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.list_comment_replies_impl("tok", "docx", "cid", 50, "")
    req = cap.request
    assert req.paths["comment_id"] == "cid"
    assert "replies" in req.uri


@pytest.mark.asyncio
async def test_reply_comment_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"reply_id": "r1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.reply_comment_impl("tok", "docx", "cid", "hi", "")
    req = cap.request
    assert req.http_method.name == "POST"
    assert "replies" in req.uri
    els = req.body["content"]["elements"]
    assert els[0]["text_run"]["text"] == "hi"
    assert all(e["type"] != "person" for e in els)


@pytest.mark.asyncio
async def test_reply_comment_with_mention(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"reply_id": "r2"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.reply_comment_impl("tok", "docx", "cid", "hi", "ou_abc")
    els = cap.request.body["content"]["elements"]
    assert any(e["type"] == "person" and e["person"]["user_id"] == "ou_abc" for e in els)


def test_drive_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_drive")
    for name in (
        "feishu_drive_add_comment",
        "feishu_drive_list_comments",
        "feishu_drive_list_comment_replies",
        "feishu_drive_reply_comment",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


@pytest.mark.asyncio
async def test_drive_add_comment_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_drive")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "code": 0, "msg": "", "data": {"comment_id": "c9"}}

    monkeypatch.setattr(_impl, "add_comment_impl", _fake)
    out = await mod.feishu_drive_add_comment(file_token="t", file_type="docx", content="hi")
    assert json.loads(out)["data"]["comment_id"] == "c9"
