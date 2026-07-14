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


# ── IM (messaging) impl tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_chat_builds_search_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"chat_id": "oc_1", "name": "主群", "description": "d"}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.find_chat_impl("主群", False, 50, "")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/chats/search")
    assert _qdict(req).get("query") == "主群"
    assert result["matches"][0]["chat_id"] == "oc_1"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_find_chat_exact_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"items": [{"chat_id": "oc_1", "name": "主群"}, {"chat_id": "oc_2", "name": "主群通知"}], "has_more": False}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.find_chat_impl("主群", True)
    assert result["count"] == 1
    assert result["matches"][0]["chat_id"] == "oc_1"


@pytest.mark.asyncio
async def test_send_message_builds_create_and_returns_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"message_id": "om_1", "thread_id": "omt_1", "chat_id": "oc_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.send_message_impl("oc_1", "hello 待办", "chat_id")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/im/v1/messages"
    assert _qdict(req).get("receive_id_type") == "chat_id"
    assert req.body["receive_id"] == "oc_1"
    assert req.body["msg_type"] == "text"
    assert json.loads(req.body["content"])["text"] == "hello 待办"
    assert result["message_id"] == "om_1"
    assert result["thread_id"] == "omt_1"


@pytest.mark.asyncio
async def test_reply_message_sets_reply_in_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"message_id": "om_2", "thread_id": "omt_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.reply_message_impl("om_1", "评价内容", True)
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.paths["message_id"] == "om_1"
    assert req.uri.endswith("/reply")
    assert req.body["reply_in_thread"] is True
    assert json.loads(req.body["content"])["text"] == "评价内容"
    assert result["thread_id"] == "omt_1"


@pytest.mark.asyncio
async def test_list_messages_thread_container(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"message_id": "om_x"}], "has_more": True, "page_token": "pt2"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_messages_impl("omt_1", "thread", "ByCreateTimeAsc", 50, "")
    q = _qdict(cap.request)
    assert cap.request.http_method.name == "GET"
    assert q.get("container_id_type") == "thread"
    assert q.get("container_id") == "omt_1"
    assert q.get("sort_type") == "ByCreateTimeAsc"
    assert result["has_more"] is True
    assert result["page_token"] == "pt2"
    assert result["items"][0]["message_id"] == "om_x"


def test_im_tools_are_async_with_docstrings() -> None:
    chat_mod = importlib.import_module("feishu_chat")
    msg_mod = importlib.import_module("feishu_message")
    fns = [
        chat_mod.feishu_chat_find,
        msg_mod.feishu_message_send,
        msg_mod.feishu_message_reply,
        msg_mod.feishu_message_list,
    ]
    for fn in fns:
        assert inspect.iscoroutinefunction(fn), fn.__name__
        assert (inspect.getdoc(fn) or "").strip(), f"{fn.__name__} needs a docstring"


@pytest.mark.asyncio
async def test_message_send_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "message_id": "om_9", "thread_id": "omt_9", "chat_id": "oc_9"}

    monkeypatch.setattr(_impl, "send_message_impl", _fake)
    out = await mod.feishu_message_send(receive_id="oc_9", text="hi")
    assert json.loads(out)["thread_id"] == "omt_9"


@pytest.mark.asyncio
async def test_doc_read_rejects_bad_file_type() -> None:
    result = await _impl.read_doc_impl("pdf", "tok", 20000)
    assert result["ok"] is False
    assert "docx" in result["message"]


@pytest.mark.asyncio
async def test_doc_read_docx_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"content": "hello world"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_doc_impl("docx", "doc123", 20000)
    assert result["ok"] is True
    assert result["content"] == "hello world"
    assert cap.request.paths["document_id"] == "doc123"
    assert "docx/v1/documents" in cap.request.uri


@pytest.mark.asyncio
async def test_doc_read_doc_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"content": "old doc body"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_doc_impl("doc", "dtok", 20000)
    assert result["content"] == "old doc body"
    assert "doc/v2" in cap.request.uri


@pytest.mark.asyncio
async def test_doc_read_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"content": "x" * 100})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_doc_impl("docx", "t", 10)
    assert result["truncated"] is True
    assert len(result["content"]) == 10


def test_doc_tool_is_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_doc")
    fn = mod.feishu_doc_read
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Contact — find member id by name ──────────────────────────────────────────


class _PagedInvoke:
    """Replace _invoke; return a queued sequence of canned success dicts (one per call)."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.requests: list[Any] = []
        self._pages = list(pages)

    async def __call__(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        page = self._pages.pop(0) if self._pages else {}
        return {"ok": True, "code": 0, "msg": "", "data": page}


@pytest.mark.asyncio
async def test_find_member_builds_members_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"items": [{"name": "张三", "member_id": "ou_1", "member_id_type": "open_id"}], "has_more": False}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.find_member_id_impl("oc_x", "张三", False, "open_id")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/im/v1/chats/:chat_id/members"
    assert req.paths["chat_id"] == "oc_x"
    assert _qdict(req).get("member_id_type") == "open_id"
    assert result["matches"] == [{"name": "张三", "id": "ou_1", "member_id_type": "open_id"}]
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_find_member_paginates_full_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"name": "张三", "member_id": "ou_1"}], "has_more": True, "page_token": "pt2"},
            {"items": [{"name": "张三丰", "member_id": "ou_2"}], "has_more": False, "page_token": ""},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.find_member_id_impl("oc_x", "张三", False, "open_id")
    assert len(paged.requests) == 2  # walked both pages
    assert _qdict(paged.requests[1]).get("page_token") == "pt2"
    assert result["member_total"] == 2
    assert result["count"] == 2  # substring: both contain 张三


@pytest.mark.asyncio
async def test_find_member_exact_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [
                {"name": "张三", "member_id": "ou_1"},
                {"name": "张三丰", "member_id": "ou_2"},
            ],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.find_member_id_impl("oc_x", "张三", True, "open_id")
    assert result["count"] == 1
    assert result["matches"][0]["id"] == "ou_1"


@pytest.mark.asyncio
async def test_find_member_empty_name_returns_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"name": "A", "member_id": "ou_a"}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.find_member_id_impl("oc_x", "", False, "open_id")
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_chat_find_member_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_chat")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "matches": [{"name": "张三", "id": "ou_9", "member_id_type": "open_id"}], "count": 1}

    monkeypatch.setattr(_impl, "find_member_id_impl", _fake)
    out = await mod.feishu_chat_find_member(chat_id="oc_x", name="张三")
    assert inspect.iscoroutinefunction(mod.feishu_chat_find_member)
    assert json.loads(out)["matches"][0]["id"] == "ou_9"
