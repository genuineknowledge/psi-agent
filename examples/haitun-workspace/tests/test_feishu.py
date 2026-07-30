from __future__ import annotations

import importlib
import inspect
import io
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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
    def __init__(self, body: bytes, status_code: int = 200) -> None:
        self.content = body
        self.status_code = status_code
        self.headers = {}


class _FakeResp:
    def __init__(self, code, msg, body: bytes, status_code: int = 200) -> None:
        self.code = code
        self.msg = msg
        self.raw = _FakeRaw(body, status_code)
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

    async def __call__(
        self,
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        # Retryable call sites pass a request factory (see _feishu_impl._fresh); resolve
        # it like the real _invoke so assertions see the request that would be sent.
        self.request = request() if callable(request) else request
        self.user_key = user_key
        self.prefer = prefer
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
async def test_create_chat_builds_request_and_returns_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"chat_id": "oc_new", "name": "项目群", "invalid_user_id_list": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_chat_impl("项目群", ["ou_a", "ou_b"], description="d")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/im/v1/chats")
    assert _qdict(req).get("user_id_type") == "open_id"
    assert req.body["name"] == "项目群"
    assert req.body["description"] == "d"
    assert req.body["user_id_list"] == ["ou_a", "ou_b"]
    assert "set_bot_manager" not in _qdict(req)  # no owner → bot stays owner
    assert result["ok"] is True
    assert result["chat_id"] == "oc_new"
    assert result["invited_count"] == 2


@pytest.mark.asyncio
async def test_create_chat_owner_is_requester_bot_stays_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    # owner_id is the requester's sender_open_id: the group is handed to them, and
    # set_bot_manager keeps the bot on as admin so it can still post afterwards.
    cap = _CapturedInvoke({"chat_id": "oc_o", "owner_id": "ou_requester"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_chat_impl("群", ["ou_a"], owner_id="ou_requester")
    assert cap.request.body["owner_id"] == "ou_requester"
    assert _qdict(cap.request).get("set_bot_manager") == "true"
    assert result["owner_id"] == "ou_requester"


@pytest.mark.asyncio
async def test_create_chat_no_owner_leaves_bot_as_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    # No requester → bot owns the group; set_bot_manager is not sent.
    cap = _CapturedInvoke({"chat_id": "oc_b"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.create_chat_impl("群", ["ou_a"])
    assert "owner_id" not in cap.request.body
    assert "set_bot_manager" not in _qdict(cap.request)


@pytest.mark.asyncio
async def test_create_chat_requires_name() -> None:
    result = await _impl.create_chat_impl("   ", ["ou_a"])
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_create_chat_rejects_over_50_members(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"chat_id": "oc_x"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_chat_impl("群", [f"ou_{i}" for i in range(51)])
    assert result["ok"] is False
    assert cap.request is None  # never sent


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
async def test_send_message_no_on_behalf_keeps_text_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    # 回归保护: 不传 on_behalf_of 时正文原样发出, 不加任何前缀 (机器人自己发内容的路径)。
    cap = _CapturedInvoke({"message_id": "om_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.send_message_impl("oc_1", "看板已更新", "chat_id")
    assert json.loads(cap.request.body["content"])["text"] == "看板已更新"


@pytest.mark.asyncio
async def test_send_message_on_behalf_wraps_with_resolved_name(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"message_id": "om_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)

    async def _fake_batch(user_ids: str, user_id_type: str = "open_id", **_: Any) -> dict[str, Any]:
        assert user_ids == "ou_zhangsan"
        return {"ok": True, "users": [{"open_id": "ou_zhangsan", "name": "张三"}]}

    monkeypatch.setattr(_impl, "get_users_batch_impl", _fake_batch)
    await _impl.send_message_impl("ou_lisi", "记得交周报", "open_id", on_behalf_of="ou_zhangsan")
    assert json.loads(cap.request.body["content"])["text"] == "张三给你发了一条消息：「记得交周报」"  # noqa: RUF001


@pytest.mark.asyncio
async def test_send_message_on_behalf_falls_back_to_open_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # 查名失败也要把消息发出去, 前缀回退成 open_id 本身 (转达失败比署名不全更糟)。
    cap = _CapturedInvoke({"message_id": "om_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)

    async def _fail_batch(*_: Any, **__: Any) -> dict[str, Any]:
        return {"ok": False, "code": 99991672, "msg": "permission denied"}

    monkeypatch.setattr(_impl, "get_users_batch_impl", _fail_batch)
    await _impl.send_message_impl("ou_lisi", "记得交周报", "open_id", on_behalf_of="ou_zhangsan")
    assert json.loads(cap.request.body["content"])["text"] == "ou_zhangsan给你发了一条消息：「记得交周报」"  # noqa: RUF001


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


@pytest.mark.asyncio
async def test_recall_message_builds_delete_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.recall_message_impl("om_abc", user_key="ou_owner")
    req = cap.request
    assert req.http_method.name == "DELETE"
    assert req.uri == "/open-apis/im/v1/messages/:message_id"
    assert req.paths["message_id"] == "om_abc"
    # tenant first: the bot's own messages need no user authorization at all
    assert cap.prefer == "tenant"
    assert cap.user_key == "ou_owner"
    # Feishu returns an empty data object on success, so success must be explicit
    assert result == {"ok": True, "message_id": "om_abc", "recalled": True}


@pytest.mark.asyncio
async def test_recall_message_trims_and_requires_message_id() -> None:
    for bad in ("", "   "):
        result = await _impl.recall_message_impl(bad)
        assert result["ok"] is False
        assert "message_id is required" in result["message"]


@pytest.mark.asyncio
async def test_recall_message_rejects_chat_or_open_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    for bad in ("oc_group", "ou_person"):
        result = await _impl.recall_message_impl(bad)
        assert result["ok"] is False
        assert "must be a message id" in result["message"]
    assert cap.request is None  # rejected before spending a request


@pytest.mark.asyncio
async def test_recall_message_adds_hint_for_known_error_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": False, "code": 230026, "msg": "No permission to recall this message.", "message": "err"}

    monkeypatch.setattr(_impl, "_invoke", _fake)
    result = await _impl.recall_message_impl("om_other")
    assert result["ok"] is False
    assert result["code"] == 230026
    assert "群主" in result["hint"]  # names the real blocker, not a bare "error 230026"


@pytest.mark.asyncio
async def test_recall_message_keeps_unknown_error_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": False, "code": 99999, "msg": "boom", "message": "err"}

    monkeypatch.setattr(_impl, "_invoke", _fake)
    result = await _impl.recall_message_impl("om_x")
    assert "hint" not in result


@pytest.mark.asyncio
async def test_recall_tool_returns_json_and_passes_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")
    captured: dict[str, Any] = {}

    async def _fake(message_id: str, user_key: str = "") -> dict[str, Any]:
        captured.update(message_id=message_id, user_key=user_key)
        return {"ok": True, "message_id": message_id, "recalled": True}

    monkeypatch.setattr(_impl, "recall_message_impl", _fake)
    out = await mod.feishu_message_recall(message_id="om_9", user_key="ou_a")
    assert json.loads(out)["recalled"] is True
    assert captured == {"message_id": "om_9", "user_key": "ou_a"}


def test_im_tools_are_async_with_docstrings() -> None:
    chat_mod = importlib.import_module("feishu_chat")
    msg_mod = importlib.import_module("feishu_message")
    fns = [
        chat_mod.feishu_chat_find,
        chat_mod.feishu_chat_create,
        msg_mod.feishu_message_send,
        msg_mod.feishu_message_send_card,
        msg_mod.feishu_message_reply,
        msg_mod.feishu_message_recall,
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
async def test_send_card_builds_interactive_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"message_id": "om_c", "thread_id": "omt_c", "chat_id": "oc_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    card = {
        "schema": "2.0",
        "body": {"elements": [{"tag": "action", "actions": [{"tag": "button", "value": {"a": "ok"}}]}]},
    }
    result = await _impl.send_card_impl("oc_1", json.dumps(card), "chat_id")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/im/v1/messages"
    assert _qdict(req).get("receive_id_type") == "chat_id"
    assert req.body["msg_type"] == "interactive"
    # card content is posted verbatim as a JSON string
    assert json.loads(req.body["content"]) == card
    assert result["message_id"] == "om_c"
    assert result["thread_id"] == "omt_c"


@pytest.mark.asyncio
async def test_send_card_infers_receive_id_type_from_open_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"message_id": "om_c"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    # default receive_id_type=chat_id but an ou_ id must be corrected to open_id
    await _impl.send_card_impl("ou_zhang", json.dumps({"config": {}, "elements": []}), "chat_id")
    assert _qdict(cap.request).get("receive_id_type") == "open_id"


@pytest.mark.asyncio
async def test_send_card_rejects_invalid_json() -> None:
    result = await _impl.send_card_impl("oc_1", "not json{", "chat_id")
    assert result["ok"] is False
    assert "valid JSON" in result["message"]


@pytest.mark.asyncio
async def test_send_card_rejects_non_object_json() -> None:
    result = await _impl.send_card_impl("oc_1", "[1, 2, 3]", "chat_id")
    assert result["ok"] is False
    assert "JSON object" in result["message"]


@pytest.mark.asyncio
async def test_send_card_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "message_id": "om_c9", "thread_id": "omt_c9", "chat_id": "oc_9"}

    monkeypatch.setattr(_impl, "send_card_impl", _fake)
    out = await mod.feishu_message_send_card(receive_id="oc_9", card_json='{"schema": "2.0"}')
    assert json.loads(out)["message_id"] == "om_c9"


@pytest.mark.asyncio
async def test_send_card_tool_passes_user_key_as_none_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")
    captured: dict[str, Any] = {}

    async def _fake(receive_id: str, card_json: str, receive_id_type: str, user_key: Any = None) -> dict[str, Any]:
        captured["user_key"] = user_key
        return {"ok": True, "message_id": "om_c"}

    monkeypatch.setattr(_impl, "send_card_impl", _fake)
    await mod.feishu_message_send_card(receive_id="oc_9", card_json='{"schema": "2.0"}')
    assert captured["user_key"] is None


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


# ── Sheet tabs — list worksheets to get a SHEET_ID ────────────────────────────


@pytest.mark.asyncio
async def test_sheet_tabs_lists_worksheets(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {
        "sheets": [
            {
                "sheet_id": "46a582",
                "title": "Sheet1",
                "index": 0,
                "grid_properties": {"row_count": 201, "column_count": 20},
            },
            {"sheetId": "zz999", "title": "备份"},
        ]
    }
    cap = _CapturedInvoke(data)
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_sheet_tabs_impl("sht1")
    assert result["ok"] is True
    assert result["count"] == 2
    first = result["sheets"][0]
    assert first["sheet_id"] == "46a582"
    assert first["title"] == "Sheet1"
    assert first["row_count"] == 201
    assert first["column_count"] == 20
    # camelCase sheetId is accepted too, and a tab with no grid_properties still lists
    assert result["sheets"][1]["sheet_id"] == "zz999"
    assert result["sheets"][1]["row_count"] is None
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.paths["spreadsheet_token"] == "sht1"
    assert "sheets/v3/spreadsheets/:spreadsheet_token/sheets/query" in req.uri


@pytest.mark.asyncio
async def test_sheet_tabs_requires_token() -> None:
    assert (await _impl.list_sheet_tabs_impl("  "))["ok"] is False


@pytest.mark.asyncio
async def test_sheet_reads_forward_user_key_as_tenant_first_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads stay tenant-first but carry the user identity as a permission fallback."""
    cap = _CapturedInvoke({"sheets": [{"sheet_id": "s1", "title": "T"}]})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.list_sheet_tabs_impl("sht1", user_key="ou_1")
    assert cap.user_key == "ou_1"
    assert cap.prefer == "tenant"

    cap = _CapturedInvoke({"valueRange": {"range": "S1!A1", "values": [["x"]]}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.read_sheet_range_impl("sht1", "S1!A1", user_key="ou_1")
    assert cap.user_key == "ou_1"
    assert cap.prefer == "tenant"


# ── Sheet range read — plain-text rows, mentions flattened ────────────────────


@pytest.mark.asyncio
async def test_sheet_read_builds_get_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"valueRange": {"range": "S1!A1:B2", "values": [["a", 1], [True, None]]}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_sheet_range_impl("sht1", "S1!A1:B2")
    assert result["ok"] is True
    assert result["range"] == "S1!A1:B2"
    assert result["rows"] == [["a", "1"], ["TRUE", ""]]
    assert result["row_count"] == 2
    assert result["truncated"] is False
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.paths["spreadsheet_token"] == "sht1"
    assert req.paths["range"] == "S1!A1:B2"
    assert "sheets/v2/spreadsheets/:spreadsheet_token/values/:range" in req.uri


@pytest.mark.asyncio
async def test_sheet_read_flattens_mentions_and_rich_text(monkeypatch: pytest.MonkeyPatch) -> None:
    grid = [
        [
            {"type": "mention", "name": "牛志宇", "text": "@牛志宇", "token": "7662722911182015754"},
            [
                {"type": "text", "text": "大目标：", "segmentStyle": {"bold": True}},  # noqa: RUF001
                {"type": "text", "text": "做最牛的 Agent", "segmentStyle": {"bold": False}},
            ],
        ]
    ]
    cap = _CapturedInvoke({"valueRange": {"range": "S1!B7:C7", "values": grid}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_sheet_range_impl("sht1", "S1!B7:C7")
    # a mention cell reads as its visible text, not raw JSON
    assert result["rows"] == [["@牛志宇", "大目标：做最牛的 Agent"]]  # noqa: RUF001


@pytest.mark.asyncio
async def test_sheet_read_truncates_on_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    grid = [["x" * 40], ["y" * 40], ["z" * 40]]
    cap = _CapturedInvoke({"valueRange": {"range": "S1", "values": grid}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_sheet_range_impl("sht1", "S1", max_chars=60)
    assert result["truncated"] is True
    assert result["row_count"] == 1


@pytest.mark.asyncio
async def test_sheet_read_no_limit_keeps_all_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    grid = [["x" * 40], ["y" * 40]]
    cap = _CapturedInvoke({"valueRange": {"range": "S1", "values": grid}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_sheet_range_impl("sht1", "S1", max_chars=0)
    assert result["truncated"] is False
    assert result["row_count"] == 2


@pytest.mark.asyncio
async def test_sheet_read_requires_token_and_range() -> None:
    assert (await _impl.read_sheet_range_impl("", "S1"))["ok"] is False
    assert (await _impl.read_sheet_range_impl("sht1", ""))["ok"] is False


# ── Sheet writes — put values/formulas, append rows, set cell style ────────────


@pytest.mark.asyncio
async def test_sheet_write_builds_put_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"updatedRange": "S1!A1:B2", "updatedCells": 4, "spreadsheetToken": "sht1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.write_sheet_impl("sht1", "S1!A1:B2", '[["a",1],["=SUM(B1:B1)",2]]', user_key="ou_1")
    assert result["ok"] is True
    assert result["updated_range"] == "S1!A1:B2"
    assert result["updated_cells"] == 4
    req = cap.request
    assert req.http_method.name == "PUT"
    assert req.paths["spreadsheet_token"] == "sht1"
    assert "sheets/v2/spreadsheets/:spreadsheet_token/values" in req.uri
    assert req.body["valueRange"]["range"] == "S1!A1:B2"
    assert req.body["valueRange"]["values"] == [["a", 1], ["=SUM(B1:B1)", 2]]
    # writes act as the user so the content is owned by them
    assert cap.prefer == "user"
    assert cap.user_key == "ou_1"


@pytest.mark.asyncio
async def test_sheet_write_rejects_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.write_sheet_impl("sht1", "S1!A1", "{not json")
    assert result["ok"] is False
    assert "JSON" in result["message"]
    assert cap.request is None  # never hit the API


@pytest.mark.asyncio
async def test_sheet_write_rejects_non_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.write_sheet_impl("sht1", "S1", '["not","a","grid"]')
    assert result["ok"] is False
    assert "list of lists" in result["message"]


@pytest.mark.asyncio
async def test_sheet_write_requires_token_and_range() -> None:
    assert (await _impl.write_sheet_impl("", "S1!A1", "[[1]]"))["ok"] is False
    assert (await _impl.write_sheet_impl("sht1", "", "[[1]]"))["ok"] is False


@pytest.mark.asyncio
async def test_sheet_write_rejects_too_many_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    big = json.dumps([[1] for _ in range(_impl._SHEET_MAX_ROWS + 1)])
    result = await _impl.write_sheet_impl("sht1", "S1", big)
    assert result["ok"] is False
    assert "too many rows" in result["message"]


@pytest.mark.asyncio
async def test_sheet_append_builds_post_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"tableRange": "S1!A1:B3"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_sheet_impl("sht1", "S1", '[["x",1]]', insert_data_option="insert_rows")
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "POST"
    assert "values_append" in req.uri
    assert _qdict(req).get("insertDataOption") == "INSERT_ROWS"
    assert req.body["valueRange"]["values"] == [["x", 1]]


@pytest.mark.asyncio
async def test_sheet_append_rejects_bad_option() -> None:
    result = await _impl.append_sheet_impl("sht1", "S1", "[[1]]", insert_data_option="NOPE")
    assert result["ok"] is False
    assert "insert_data_option" in result["message"]


@pytest.mark.asyncio
async def test_sheet_format_builds_style_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"spreadsheetToken": "sht1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.format_sheet_impl("sht1", "S1!A1:B2", '{"font":{"bold":true},"backColor":"#21d11f"}')
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "PUT"
    assert req.uri.endswith("/style")
    assert req.body["appendStyle"]["range"] == "S1!A1:B2"
    assert req.body["appendStyle"]["style"]["font"]["bold"] is True


@pytest.mark.asyncio
async def test_sheet_format_rejects_non_object(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.format_sheet_impl("sht1", "S1!A1", "[1,2]")
    assert result["ok"] is False
    assert cap.request is None


def test_sheet_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_sheet")
    for name in (
        "feishu_sheet_tabs",
        "feishu_sheet_read",
        "feishu_sheet_write",
        "feishu_sheet_append",
        "feishu_sheet_format",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn)
        assert (inspect.getdoc(fn) or "").strip()


# ── Contact — find member id by name ──────────────────────────────────────────


class _PagedInvoke:
    """Replace _invoke; return a queued sequence of canned success dicts (one per call)."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.requests: list[Any] = []
        self._pages = list(pages)

    async def __call__(
        self,
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
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


@pytest.mark.asyncio
async def test_list_chat_members_returns_full_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [
                {"name": "张三", "member_id": "ou_1"},
                {"name": "李四", "member_id": "ou_2"},
            ],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_chat_members_impl("oc_x", "open_id")
    req = cap.request
    assert req.uri == "/open-apis/im/v1/chats/:chat_id/members"
    assert req.paths["chat_id"] == "oc_x"
    assert result["count"] == 2
    assert result["members"] == [
        {"name": "张三", "id": "ou_1", "member_id_type": "open_id"},
        {"name": "李四", "id": "ou_2", "member_id_type": "open_id"},
    ]


@pytest.mark.asyncio
async def test_list_chat_members_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"name": "张三", "member_id": "ou_1"}], "has_more": True, "page_token": "pt2"},
            {"items": [{"name": "李四", "member_id": "ou_2"}], "has_more": False, "page_token": ""},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.list_chat_members_impl("oc_x", "open_id")
    assert len(paged.requests) == 2
    assert _qdict(paged.requests[1]).get("page_token") == "pt2"
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_chat_list_members_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_chat")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "members": [{"name": "张三", "id": "ou_9", "member_id_type": "open_id"}], "count": 1}

    monkeypatch.setattr(_impl, "list_chat_members_impl", _fake)
    out = await mod.feishu_chat_list_members(chat_id="oc_x")
    assert inspect.iscoroutinefunction(mod.feishu_chat_list_members)
    assert json.loads(out)["members"][0]["id"] == "ou_9"


@pytest.mark.asyncio
async def test_chat_create_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_chat")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "chat_id": "oc_new", "invited": ["ou_a"], "invited_count": 1}

    monkeypatch.setattr(_impl, "create_chat_impl", _fake)
    out = await mod.feishu_chat_create(name="项目群", user_ids=["ou_a"])
    assert inspect.iscoroutinefunction(mod.feishu_chat_create)
    assert json.loads(out)["chat_id"] == "oc_new"


# ── Approval — list tasks, read instance, approve/reject ──────────────────────


@pytest.mark.asyncio
async def test_list_approval_tasks_builds_query(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "process_id": "inst1",
                    "definition_code": "appr1",
                    "title": "请假申请",
                    "status": 1,
                    "process_status": 1,
                    "initiator_names": ["张三"],
                }
            ],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_approval_tasks_impl("ou_a", "1", "open_id")
    q = _qdict(cap.request)
    assert cap.request.http_method.name == "GET"
    assert cap.request.uri.endswith("/approval/v4/tasks/query")
    assert q.get("user_id") == "ou_a"
    assert q.get("topic") == "1"
    t = result["tasks"][0]
    assert t["task_id"] == "t1"
    assert t["instance_code"] == "inst1"
    assert t["approval_code"] == "appr1"
    assert t["status"] == "待办"


@pytest.mark.asyncio
async def test_get_approval_instance_reads_form(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"approval_code": "appr1", "status": "PENDING", "user_id": "ou_app", "form": '[{"id":"w1"}]', "task_list": []}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_approval_instance_impl("inst1")
    assert cap.request.paths["instance_id"] == "inst1"
    assert "approval/v4/instances" in cap.request.uri
    assert result["applicant"] == "ou_app"
    assert result["form"] == '[{"id":"w1"}]'


@pytest.mark.asyncio
async def test_decide_approve_builds_post(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.decide_approval_task_impl(True, "appr1", "inst1", "ou_boss", "t1", "同意")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/tasks/approve")
    assert req.body["approval_code"] == "appr1"
    assert req.body["instance_code"] == "inst1"
    assert req.body["user_id"] == "ou_boss"
    assert req.body["task_id"] == "t1"
    assert req.body["comment"] == "同意"
    assert result["action"] == "approve"


@pytest.mark.asyncio
async def test_decide_reject_uses_reject_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.decide_approval_task_impl(False, "appr1", "inst1", "ou_boss", "t1")
    assert cap.request.uri.endswith("/tasks/reject")
    assert "comment" not in cap.request.body  # empty comment omitted
    assert result["action"] == "reject"


@pytest.mark.asyncio
async def test_get_approval_definition_parses_form_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "approval_name": "请假",
            "status": "ACTIVE",
            "form": '[{"id":"w1","custom_id":"leave_type","name":"假别","type":"radioV2","required":true},'
            '{"id":"w2","name":"事由","type":"textarea"}]',
            "node_list": [{"name": "直属主管", "node_id": "n1", "node_type": "AND"}],
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_approval_definition_impl("appr1")
    q = _qdict(cap.request)
    assert cap.request.http_method.name == "GET"
    assert cap.request.paths["approval_code"] == "appr1"
    assert cap.request.uri.endswith("/approval/v4/approvals/:approval_code")
    assert q.get("user_id_type") == "open_id"
    assert result["approval_name"] == "请假"
    fields = result["form"]
    assert fields[0] == {
        "id": "w1",
        "custom_id": "leave_type",
        "name": "假别",
        "type": "radioV2",
        "required": True,
    }
    assert fields[1]["required"] is False
    assert result["node_list"][0]["node_id"] == "n1"


@pytest.mark.asyncio
async def test_get_approval_definition_requires_code(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_approval_definition_impl("")
    assert result["ok"] is False
    assert cap.request is None  # never called Feishu


@pytest.mark.asyncio
async def test_create_instance_builds_body(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"instance_code": "inst_new"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_approval_instance_impl(
        "appr1",
        '[{"id":"w1","type":"input","value":"年假"}]',
        applicant_open_id="ou_emp",
        title="张三的请假",
    )
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/approval/v4/instances")
    assert req.body["approval_code"] == "appr1"
    assert req.body["open_id"] == "ou_emp"
    assert "user_id" not in req.body
    assert req.body["title"] == "张三的请假"
    assert json.loads(req.body["form"]) == [{"id": "w1", "type": "input", "value": "年假"}]
    assert result["instance_code"] == "inst_new"


@pytest.mark.asyncio
async def test_create_instance_passes_node_approvers_and_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"instance_code": "inst_new"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.create_approval_instance_impl(
        "appr1",
        "[]",
        applicant_open_id="ou_emp",
        node_approver_open_id_list_json='[{"key":"n1","value":["ou_boss"]}]',
        user_key="ou_emp",
    )
    assert cap.request.body["node_approver_open_id_list"] == [{"key": "n1", "value": ["ou_boss"]}]
    assert cap.user_key == "ou_emp"


@pytest.mark.asyncio
async def test_create_instance_requires_applicant(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_approval_instance_impl("appr1", "[]")
    assert result["ok"] is False
    assert cap.request is None


@pytest.mark.asyncio
async def test_create_instance_rejects_bad_form_json(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    bad = await _impl.create_approval_instance_impl("appr1", "{not json", applicant_open_id="ou_emp")
    assert bad["ok"] is False
    not_list = await _impl.create_approval_instance_impl("appr1", '{"id":"w1"}', applicant_open_id="ou_emp")
    assert not_list["ok"] is False
    assert cap.request is None


def test_approval_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_approval")
    for name in (
        "feishu_approval_list_tasks",
        "feishu_approval_get",
        "feishu_approval_decide",
        "feishu_approval_get_definition",
        "feishu_approval_create",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


@pytest.mark.asyncio
async def test_approval_decide_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_approval")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "action": "approve", "instance_code": "inst1", "task_id": "t1"}

    monkeypatch.setattr(_impl, "decide_approval_task_impl", _fake)
    out = await mod.feishu_approval_decide(
        approve=True, approval_code="a", instance_code="inst1", approver_user_id="ou_b", task_id="t1"
    )
    assert json.loads(out)["action"] == "approve"


# ── Wiki — resolve node token to underlying document ──────────────────────────


@pytest.mark.asyncio
async def test_get_wiki_node_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"node": {"node_token": "NFOnw", "obj_token": "doccnX", "obj_type": "docx", "title": "SOP", "space_id": "s1"}}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_wiki_node_impl("NFOnw")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/wiki/v2/spaces/get_node")
    assert _qdict(req).get("token") == "NFOnw"
    assert result["obj_token"] == "doccnX"
    assert result["obj_type"] == "docx"
    assert result["title"] == "SOP"


@pytest.mark.asyncio
async def test_get_wiki_node_error_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(_req: Any, user_key: str | None = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        return {"ok": False, "code": 131006, "msg": "node not found", "message": "Feishu API error 131006"}

    monkeypatch.setattr(_impl, "_invoke", _fake)
    result = await _impl.get_wiki_node_impl("bad")
    assert result["ok"] is False
    assert result["code"] == 131006


def test_wiki_tool_is_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_wiki")
    fn = mod.feishu_wiki_get_node
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Start topic with @-mentions ───────────────────────────────────────────────


def test_build_post_at_content_has_at_elements() -> None:
    content = json.loads(_impl._build_post_at_content("今天的待办", ["ou_a", "ou_b"], False))
    line = content["zh_cn"]["content"][0]
    assert line[0] == {"tag": "at", "user_id": "ou_a"}
    assert line[1] == {"tag": "at", "user_id": "ou_b"}
    assert line[2] == {"tag": "text", "text": " 今天的待办"}  # space separates mentions from text


def test_build_post_at_content_at_all_and_skip_empty() -> None:
    content = json.loads(_impl._build_post_at_content("hi", ["ou_a", ""], True))
    line = content["zh_cn"]["content"][0]
    assert line[0] == {"tag": "at", "user_id": "all"}  # @everyone first
    assert line[1] == {"tag": "at", "user_id": "ou_a"}
    assert all(e.get("user_id") != "" for e in line if e["tag"] == "at")  # empties skipped
    assert line[-1] == {"tag": "text", "text": " hi"}


@pytest.mark.asyncio
async def test_start_topic_uses_post_when_mentions(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"message_id": "om_1", "thread_id": "omt_1", "chat_id": "oc_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.start_topic_impl("oc_1", "今天的待办", ["ou_a", "ou_b"], False)
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/im/v1/messages"
    assert req.body["receive_id"] == "oc_1"
    assert req.body["msg_type"] == "post"  # mentions -> post rich text
    line = json.loads(req.body["content"])["zh_cn"]["content"][0]
    assert {"tag": "at", "user_id": "ou_a"} in line
    assert result["thread_id"] == "omt_1"


@pytest.mark.asyncio
async def test_start_topic_no_mentions_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"message_id": "om_1", "thread_id": "omt_1", "chat_id": "oc_1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.start_topic_impl("oc_1", "hello", None, False)
    assert cap.request.body["msg_type"] == "text"  # no mentions -> plain text
    assert json.loads(cap.request.body["content"])["text"] == "hello"


@pytest.mark.asyncio
async def test_topic_start_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "message_id": "om_9", "thread_id": "omt_9", "chat_id": "oc_9"}

    monkeypatch.setattr(_impl, "start_topic_impl", _fake)
    out = await mod.feishu_topic_start(chat_id="oc_9", text="hi", at_open_ids=["ou_x"])
    assert inspect.iscoroutinefunction(mod.feishu_topic_start)
    assert json.loads(out)["thread_id"] == "omt_9"


# ── Document search (user_access_token) ───────────────────────────────────────


class _FakeUAT:
    def __init__(self, access_token: str = "uat_tok") -> None:
        self.access_token = access_token
        self.refresh_token = "rt"
        self.expires_at = None
        self.open_id = "ou_me"
        self.scopes = ["docs:doc:readonly"]


class _CapturingUatClient:
    """Fake UAT client: record the (request, option) passed to arequest, return a canned body."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.request: Any = None
        self.option: Any = None
        self._raw = _FakeRaw(json.dumps(body).encode())

    async def arequest(self, request: Any, option: Any = None) -> Any:
        self.request = request
        self.option = option
        return type("R", (), {"raw": self._raw, "code": 0, "msg": ""})()


@pytest.mark.asyncio
async def test_search_docs_not_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    result = await _impl.search_docs_impl("周报", 20, 0, "")
    assert result["ok"] is False
    assert result.get("need_auth") is True


@pytest.mark.asyncio
async def test_search_docs_builds_request_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "code": 0,
        "data": {
            "docs_entities": [{"title": "周报", "docs_token": "doccnX", "docs_type": "docx", "owner_id": "ou_o"}],
            "has_more": False,
            "total": 1,
        },
    }
    client = _CapturingUatClient(body)
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.search_docs_impl("周报", 10, 5, "docx,sheet")
    req = client.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/suite/docs-api/search/object"
    assert _impl.AccessTokenType.USER in req.token_types
    assert req.body["search_key"] == "周报"
    assert req.body["count"] == 10
    assert req.body["offset"] == 5
    assert req.body["docs_types"] == ["docx", "sheet"]
    assert client.option.user_access_token == "uat_tok"
    assert result["docs"][0] == {"title": "周报", "token": "doccnX", "obj_type": "docx", "owner_id": "ou_o"}
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_search_docs_api_error_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CapturingUatClient({"code": 99991663, "msg": "permission denied", "data": {}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.search_docs_impl("x", 20, 0, "")
    assert result["ok"] is False
    assert result["code"] == 99991663


@pytest.mark.asyncio
async def test_auth_start_builds_authorize_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(
        _impl._oauth_rx,
        "plan_receiver",
        lambda explicit="": _impl._oauth_rx.ReceiverPlan(mode="manual", redirect_uri="http://localhost/"),
    )
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    result = await _impl.auth_start_impl("")
    assert result["ok"] is True
    parsed = urlparse(result["authorize_url"])
    assert parsed.hostname == "accounts.feishu.cn"
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["cli_x"]
    assert q["response_type"] == ["code"]
    assert "offline_access" in q["scope"][0]
    # No capabilities named -> the documented default set, and only real scopes: a
    # fabricated one (e.g. "drive:drive:drive:readonly") fails the page with 20043.
    # Order is normalized to the catalog's, so compare as a set.
    assert set(q["scope"][0].split()) == set(_impl._scope_string(list(_impl._DEFAULT_CAPABILITIES)).split())
    assert "drive:drive:drive" not in q["scope"][0]
    # PKCE: challenge goes on the authorize URL, verifier stays with us
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"][0]
    pending = json.loads((tmp_path / "pending.json").read_text())
    # state persisted for CSRF check
    assert pending["state"] == q["state"][0]
    assert 43 <= len(pending["code_verifier"]) <= 128
    assert pending["redirect_uri"] == q["redirect_uri"][0]
    # manual fallback keeps the old address-bar instructions
    assert result["auto_receive"] is False
    # state persisted for CSRF check, capabilities parked for auth_complete
    assert set(pending["capabilities"]) == set(_impl._DEFAULT_CAPABILITIES)
    # the prompt must be explicit about copying the code from the browser ADDRESS BAR
    msg = result["message"]
    assert "地址栏" in msg
    assert "code=" in msg
    assert "feishu_auth_complete" in msg
    # reassure the user they won't be asked again after authorizing once
    assert "不会再" in msg or "自动续期" in msg


@pytest.mark.asyncio
async def test_auth_start_prefers_automatic_receive(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """有自动通道时不再让用户复制 code, 而是引导到 feishu_auth_wait。"""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(
        _impl._oauth_rx,
        "plan_receiver",
        lambda explicit="": _impl._oauth_rx.ReceiverPlan(
            mode="gateway", redirect_uri="https://gw.example.com/oauth/callback"
        ),
    )
    result = await _impl.auth_start_impl("")
    assert result["auto_receive"] is True
    assert result["mode"] == "gateway"
    assert result["next_step"] == "feishu_auth_wait"
    q = parse_qs(urlparse(result["authorize_url"]).query)
    assert q["redirect_uri"] == ["https://gw.example.com/oauth/callback"]
    # 自动路径的提示里不能再出现「从地址栏复制」的指令
    assert "地址栏" not in result["message"]
    assert "不用复制" in result["message"]
    assert json.loads((tmp_path / "pending.json").read_text())["mode"] == "gateway"


@pytest.mark.asyncio
async def test_auth_start_requests_only_named_capabilities(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Asking for one capability must not drag in the rest of the catalog."""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    result = await _impl.auth_start_impl("bitable_write", "ou_a")
    scope = parse_qs(urlparse(result["authorize_url"]).query)["scope"][0]
    assert "bitable:app" in scope
    assert "docx:document" not in scope
    assert "wiki:wiki" not in scope
    assert result["capabilities"] == ["bitable_write"]


@pytest.mark.asyncio
async def test_auth_start_unions_with_already_granted(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A second authorization must not revoke what the first one granted.

    Feishu issues a token carrying exactly the latest grant's scopes, so asking for
    only the new capability would silently drop the working ones.
    """
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    _impl._record_granted_capabilities("ou_a", ["docx_write"])
    result = await _impl.auth_start_impl("bitable_write", "ou_a")
    scope = parse_qs(urlparse(result["authorize_url"]).query)["scope"][0]
    assert "docx:document" in scope  # kept
    assert "bitable:app" in scope  # added
    assert result["newly_requested"] == ["bitable_write"]
    assert result["already_granted"] == ["docx_write"]


@pytest.mark.asyncio
async def test_auth_start_refuses_raw_scope_strings(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A raw/invented scope is refused here, not sent to Feishu as a broken page."""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    for bad in ("docs:doc:readonly", "docx:write", "drive:drive:drive:readonly"):
        result = await _impl.auth_start_impl(bad, "ou_a")
        assert result["ok"] is False
        assert "authorize_url" not in result
        assert "capability_keys" in result


@pytest.mark.asyncio
async def test_auth_start_wrapper_takes_capabilities_not_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool exposes capability keys, never raw scopes."""
    auth_mod = importlib.import_module("feishu_auth")
    params = inspect.signature(auth_mod.feishu_auth_start).parameters
    assert "scopes" not in params
    assert list(params) == ["user_key", "capabilities"]

    captured: dict[str, Any] = {}

    async def _fake_start(capabilities: str = "", user_key: str = "") -> dict[str, Any]:
        captured["capabilities"] = capabilities
        captured["user_key"] = user_key
        return {"ok": True, "authorize_url": "x"}

    monkeypatch.setattr(auth_mod._f, "auth_start_impl", _fake_start)
    await auth_mod.feishu_auth_start("ou_a", "docx_write,wiki_write")
    assert captured["user_key"] == "ou_a"
    assert captured["capabilities"] == "docx_write,wiki_write"


def test_auth_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_auth")
    for name in (
        "feishu_auth_start",
        "feishu_auth_card",
        "feishu_auth_wait",
        "feishu_auth_complete",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


def test_extract_code_from_url_or_bare() -> None:
    assert _impl._extract_code("https://localhost/?code=ABC123&state=x") == "ABC123"
    assert _impl._extract_code("  ABC123  ") == "ABC123"


@pytest.mark.asyncio
async def test_auth_complete_exchanges_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    pending = tmp_path / "pending.json"
    pending.write_text(
        json.dumps({"state": "st", "code_verifier": "v" * 64, "redirect_uri": "http://localhost/", "mode": "manual"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))

    stored: dict[str, Any] = {}

    class _Store:
        async def set(self, k: str, v: Any) -> None:
            stored["uat"] = v

    monkeypatch.setattr(_impl, "_get_token_store", lambda: _Store())

    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_post(url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        calls.append((url, body))
        if "app_access_token" in url:
            return {"code": 0, "app_access_token": "a-tok"}
        return {
            "code": 0,
            "data": {
                "access_token": "u-tok",
                "refresh_token": "r-tok",
                "expires_in": 7200,
                "open_id": "ou_me",
                "scope": "docs:doc:readonly",
            },
        }

    monkeypatch.setattr(_impl, "_post_json", _fake_post)
    result = await _impl.auth_complete_impl("https://localhost/?code=THECODE&state=x")
    assert result["ok"] is True
    assert result["open_id"] == "ou_me"
    # token-exchange call carried the extracted code
    exchange = next(c for c in calls if c[0].endswith("/authen/v1/access_token"))
    assert exchange[1]["grant_type"] == "authorization_code"
    assert exchange[1]["code"] == "THECODE"
    # PKCE verifier + redirect_uri must match the authorize step (Feishu 20071 otherwise)
    assert exchange[1]["code_verifier"] == "v" * 64
    assert exchange[1]["redirect_uri"] == "http://localhost/"
    assert stored["uat"].access_token == "u-tok"


@pytest.mark.asyncio
async def test_auth_wait_receives_code_and_completes(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """自动通道拿回 code 后直接完成授权 —— 用户不复制任何东西。"""
    pending = tmp_path / "pending.json"
    pending.write_text(
        json.dumps({"state": "st", "code_verifier": "v" * 64, "redirect_uri": "https://gw/x", "mode": "gateway"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))

    async def _fake_poll(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        assert state == "st"
        return {"code": "AUTOCODE"}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _fake_poll)

    completed: dict[str, Any] = {}

    async def _fake_complete(code: str, user_key: str = "") -> dict[str, Any]:
        completed["code"] = code
        return {"ok": True}

    monkeypatch.setattr(_impl, "auth_complete_impl", _fake_complete)
    result = await _impl.auth_wait_impl("", 30)
    assert result["ok"] is True
    assert completed["code"] == "AUTOCODE"


@pytest.mark.asyncio
async def test_auth_wait_without_pending_asks_for_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "missing.json"))
    result = await _impl.auth_wait_impl("")
    assert result["ok"] is False
    assert "feishu_auth_start" in result["message"]


@pytest.mark.asyncio
async def test_auth_wait_manual_mode_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "manual"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))
    result = await _impl.auth_wait_impl("")
    assert result["ok"] is False
    assert result["manual_required"] is True


@pytest.mark.asyncio
async def test_auth_wait_timeout_is_retryable(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "gateway"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))

    async def _no_code(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        return {}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _no_code)
    result = await _impl.auth_wait_impl("", 10)
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert "feishu_auth_wait" in result["message"]


@pytest.mark.asyncio
async def test_auth_wait_surfaces_user_denial(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "gateway"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))

    async def _denied(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        return {"error": "access_denied"}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _denied)
    result = await _impl.auth_wait_impl("", 10)
    assert result["ok"] is False
    assert "access_denied" in result["message"]


def _auth_card_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, mode: str = "gateway") -> dict[str, Any]:
    """Configure an app + a receiver channel, and capture what gets sent."""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    monkeypatch.setattr(
        _impl._oauth_rx,
        "plan_receiver",
        lambda explicit="": _impl._oauth_rx.ReceiverPlan(
            mode=mode,
            redirect_uri="https://gw.example.com/oauth/callback" if mode == "gateway" else "http://localhost/",
        ),
    )
    captured: dict[str, Any] = {}

    async def _fake_send(
        receive_id: str,
        card_json: str,
        receive_id_type: str,
        user_key: Any = None,
        business_context_json: str = "{}",
        action_handlers_json: str = "{}",
    ) -> dict[str, Any]:
        captured.update(
            receive_id=receive_id,
            card=json.loads(card_json),
            receive_id_type=receive_id_type,
            user_key=user_key,
            business_context=json.loads(business_context_json),
            action_handlers=json.loads(action_handlers_json),
        )
        return {"ok": True, "message_id": "om_auth", "callback_context_saved": True}

    monkeypatch.setattr(_impl, "send_card_impl", _fake_send)
    return captured


def _card_button(card: dict[str, Any]) -> dict[str, Any]:
    return next(e for e in card["body"]["elements"] if e.get("tag") == "button")


@pytest.mark.asyncio
async def test_auth_card_button_both_opens_url_and_calls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """One tap must do both: without the callback the agent never learns to start waiting."""
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_card_impl("ou_a", "bitable_write", "要把台账建在你名下")
    assert result["ok"] is True
    behaviors = _card_button(captured["card"])["behaviors"]
    by_type = {b["type"]: b for b in behaviors}
    assert set(by_type) == {"open_url", "callback"}
    # the jump target is the real authorize URL, carrying the requested scope
    authorize_url = by_type["open_url"]["default_url"]
    assert parse_qs(urlparse(authorize_url).query)["redirect_uri"] == ["https://gw.example.com/oauth/callback"]
    assert "bitable:app" in parse_qs(urlparse(authorize_url).query)["scope"][0]
    # the callback carries the action name the handler map is keyed on, plus whose auth it is
    assert by_type["callback"]["value"] == {"action": _impl._AUTH_CARD_ACTION, "user_key": "ou_a"}
    assert captured["action_handlers"] == {_impl._AUTH_CARD_ACTION: "feishu_auth_wait"}
    assert captured["business_context"]["user_key"] == "ou_a"
    assert captured["business_context"]["capabilities"] == ["bitable_write"]
    assert "要把台账建在你名下" in json.dumps(captured["card"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_auth_card_defaults_to_a_dm_to_the_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    captured = _auth_card_env(monkeypatch, tmp_path)
    await _impl.auth_card_impl("ou_a")
    assert captured["receive_id"] == "ou_a"
    assert captured["receive_id_type"] == "open_id"


@pytest.mark.asyncio
async def test_auth_card_refuses_group_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A card tapped in a group lands in the tapper's own session, which has no pending auth."""
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_card_impl("ou_a", receive_id="oc_group")
    assert result["ok"] is False
    assert "私聊" in result["message"]
    assert captured == {}  # nothing sent, and no authorization started


@pytest.mark.asyncio
async def test_auth_card_unions_with_already_granted(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Same union rule as auth_start: a second grant must not drop working capabilities."""
    captured = _auth_card_env(monkeypatch, tmp_path)
    _impl._record_granted_capabilities("ou_a", ["docx_write"])
    result = await _impl.auth_card_impl("ou_a", "bitable_write")
    scope = parse_qs(urlparse(_card_button(captured["card"])["behaviors"][0]["default_url"]).query)["scope"][0]
    assert "docx:document" in scope
    assert "bitable:app" in scope
    assert result["newly_requested"] == ["bitable_write"]
    assert result["already_granted"] == ["docx_write"]


@pytest.mark.asyncio
async def test_auth_card_tells_the_agent_to_end_its_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Waiting in the sending turn would hold the Session turn lock for minutes."""
    _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_card_impl("ou_a")
    assert result["action_handler"] == "feishu_auth_wait"
    msg = result["message"]
    assert "这一轮到此为止" in msg
    assert "feishu_auth_wait" in msg
    # single-use cards: the recovery path must be a fresh card, not another tap
    assert "feishu_auth_card" in msg


@pytest.mark.asyncio
async def test_auth_card_refuses_when_no_automatic_channel(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A button that still needs the user to copy code= would be a broken promise."""
    captured = _auth_card_env(monkeypatch, tmp_path, mode="manual")
    result = await _impl.auth_card_impl("ou_a")
    assert result["ok"] is False
    assert result["manual_required"] is True
    assert result["authorize_url"]  # the manual path is still reachable
    assert captured == {}


@pytest.mark.asyncio
async def test_auth_card_requires_a_user_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_card_impl("   ")
    assert result["ok"] is False
    assert "user_key" in result["message"]
    assert captured == {}


@pytest.mark.asyncio
async def test_auth_card_reports_send_failure_with_a_link_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    _auth_card_env(monkeypatch, tmp_path)

    async def _failing_send(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": False, "message": "card send failed"}

    monkeypatch.setattr(_impl, "send_card_impl", _failing_send)
    result = await _impl.auth_card_impl("ou_a")
    assert result["ok"] is False
    assert result["authorize_url"]
    assert "authorize_url" in result["fallback"]


@pytest.mark.asyncio
async def test_auth_card_refuses_raw_scope_strings(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_card_impl("ou_a", "docx:document")
    assert result["ok"] is False
    assert "capability_keys" in result
    assert captured == {}


@pytest.mark.asyncio
async def test_auth_card_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_mod = importlib.import_module("feishu_auth")
    captured: dict[str, Any] = {}

    async def _fake(user_key: str, capabilities: str = "", reason: str = "", receive_id: str = "") -> dict[str, Any]:
        captured.update(user_key=user_key, capabilities=capabilities, reason=reason, receive_id=receive_id)
        return {"ok": True, "message_id": "om_auth"}

    monkeypatch.setattr(auth_mod._f, "auth_card_impl", _fake)
    out = await auth_mod.feishu_auth_card("ou_a", "docx_write", "建周报")
    assert json.loads(out)["message_id"] == "om_auth"
    assert captured == {"user_key": "ou_a", "capabilities": "docx_write", "reason": "建周报", "receive_id": ""}


def test_auth_prompt_leads_with_the_card_and_keeps_the_manual_fallback() -> None:
    """need_auth guidance must name the card first — and still describe the manual path."""
    prompt = _impl._AUTH_PROMPT
    assert prompt.index("feishu_auth_card") < prompt.index("feishu_auth_start")
    assert "feishu_auth_wait" in prompt
    assert "地址栏" in prompt
    assert "feishu_auth_complete" in prompt


def test_norm_user_key_empty_falls_back_to_default() -> None:
    assert _impl._norm_user_key("") == "default"
    assert _impl._norm_user_key("   ") == "default"
    assert _impl._norm_user_key("ou_abc") == "ou_abc"


def test_pending_auth_path_is_per_user() -> None:
    a = _impl._pending_auth_path("ou_a")
    b = _impl._pending_auth_path("ou_b")
    default = _impl._pending_auth_path("")
    assert a != b
    assert a != default
    # unsafe chars in an open_id must not escape the feishu dir
    weird = _impl._pending_auth_path("../../etc/x")
    assert "pending_auth_" in weird
    assert ".." not in Path(weird).name


@pytest.mark.asyncio
async def test_uat_isolated_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two users' tokens live under separate keys and never overwrite each other."""

    class _MultiStore:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}

        async def get(self, key: str) -> Any:
            return self.data.get(key)

        async def set(self, key: str, val: Any) -> None:
            self.data[key] = val

    store = _MultiStore()
    monkeypatch.setattr(_impl, "_get_token_store", lambda: store)

    await store.set("ou_a", _FakeUAT("tok_a"))
    await store.set("ou_b", _FakeUAT("tok_b"))

    uat_a = await _impl._get_valid_uat("ou_a")
    uat_b = await _impl._get_valid_uat("ou_b")
    assert uat_a.access_token == "tok_a"
    assert uat_b.access_token == "tok_b"
    # storing a third user leaves the first two intact
    assert set(store.data) == {"ou_a", "ou_b"}


@pytest.mark.asyncio
async def test_search_docs_forwards_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_docs_impl must resolve the UAT for the passed user_key."""
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())
    seen: dict[str, str] = {}

    async def _capture(user_key: str = "") -> Any:
        seen["user_key"] = user_key
        return None  # None -> need_auth, enough to assert the key was forwarded

    monkeypatch.setattr(_impl, "_get_valid_uat", _capture)
    result = await _impl.search_docs_impl("周报", 20, 0, "", "ou_zhang")
    assert seen["user_key"] == "ou_zhang"
    assert result.get("need_auth") is True


def test_search_auth_tools_async_with_docstrings() -> None:
    docs_mod = importlib.import_module("feishu_docs")
    auth_mod = importlib.import_module("feishu_auth")
    for fn in (docs_mod.feishu_docs_search, auth_mod.feishu_auth_start, auth_mod.feishu_auth_complete):
        assert inspect.iscoroutinefunction(fn), fn.__name__
        assert (inspect.getdoc(fn) or "").strip(), f"{fn.__name__} needs a docstring"


# ── Bitable — list tables, list/create records ────────────────────────────────


@pytest.mark.asyncio
async def test_list_bitable_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"table_id": "tbl1", "name": "反馈表"}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_bitable_tables_impl("appX", 100, "")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables"
    assert req.paths["app_token"] == "appX"
    assert result["tables"] == [{"table_id": "tbl1", "name": "反馈表"}]


@pytest.mark.asyncio
async def test_list_bitable_records(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [{"record_id": "rec1", "fields": {"新人": "张三"}}],
            "has_more": True,
            "page_token": "pt2",
            "total": 5,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_bitable_records_impl("appX", "tbl1", 100, "", "", '["日期 DESC"]', "")
    req = cap.request
    q = _qdict(req)
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    assert req.paths["table_id"] == "tbl1"
    assert q.get("sort") == '["日期 DESC"]'
    assert result["records"][0] == {"record_id": "rec1", "fields": {"新人": "张三"}}
    assert result["has_more"] is True
    assert result["total"] == 5


@pytest.mark.asyncio
async def test_search_bitable_records(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"items": [{"record_id": "rec1", "fields": {"状态": "进行中"}}], "has_more": False, "total": 1}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.search_bitable_records_impl(
        "appX",
        "tbl1",
        '{"conjunction":"and","conditions":[{"field_name":"状态","operator":"is","value":["进行中"]}]}',
        '[{"field_name":"日期","desc":true}]',
        '["状态"]',
    )
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/search"
    assert req.body["filter"]["conjunction"] == "and"
    assert req.body["filter"]["conditions"][0]["field_name"] == "状态"
    assert req.body["sort"] == [{"field_name": "日期", "desc": True}]
    assert req.body["field_names"] == ["状态"]
    assert result["records"][0]["record_id"] == "rec1"
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_search_bitable_records_view_only(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.search_bitable_records_impl("appX", "tbl1", view_id="vewA", automatic_fields=True)
    assert cap.request.body == {"view_id": "vewA", "automatic_fields": True}
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_search_bitable_records_rejects_view_with_filter() -> None:
    result = await _impl.search_bitable_records_impl(
        "appX",
        "tbl1",
        '{"conjunction":"and","conditions":[{"field_name":"a","operator":"is","value":["b"]}]}',
        view_id="vewA",
    )
    assert result["ok"] is False
    assert "view_id" in result["message"]


@pytest.mark.asyncio
async def test_search_bitable_records_bad_filter() -> None:
    # not JSON / missing conjunction / no conditions / unsupported operator / non-array value
    bad = [
        "not json",
        '{"conditions":[{"field_name":"a","operator":"is"}]}',
        '{"conjunction":"and","conditions":[]}',
        '{"conjunction":"and","conditions":[{"field_name":"a","operator":"like","value":["b"]}]}',
        '{"conjunction":"and","conditions":[{"field_name":"a","operator":"is","value":"b"}]}',
        '{"conjunction":"and","conditions":[{"operator":"is","value":["b"]}]}',
    ]
    for f in bad:
        result = await _impl.search_bitable_records_impl("appX", "tbl1", f)
        assert result["ok"] is False, f


@pytest.mark.asyncio
async def test_search_bitable_records_page_size_bounds() -> None:
    assert (await _impl.search_bitable_records_impl("appX", "tbl1", page_size=501))["ok"] is False
    assert (await _impl.search_bitable_records_impl("appX", "tbl1", page_size=0))["ok"] is False


@pytest.mark.asyncio
async def test_get_bitable_record(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "record": {
                "record_id": "rec1",
                "fields": {"状态": "进行中"},
                "record_url": "https://x.feishu.cn/base/appX?table=tbl1&record=rec1",
                "created_time": 1691049973000,
            }
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_bitable_record_impl("appX", "tbl1", "rec1", automatic_fields=True)
    req = cap.request
    q = _qdict(req)
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"
    assert req.paths["record_id"] == "rec1"
    assert q.get("automatic_fields") == "true"
    assert result["fields"] == {"状态": "进行中"}
    assert result["url"].endswith("record=rec1")
    assert result["created_time"] == 1691049973000


@pytest.mark.asyncio
async def test_get_bitable_record_requires_ids() -> None:
    assert (await _impl.get_bitable_record_impl("", "tbl1", "rec1"))["ok"] is False
    assert (await _impl.get_bitable_record_impl("appX", "", "rec1"))["ok"] is False
    assert (await _impl.get_bitable_record_impl("appX", "tbl1", ""))["ok"] is False


@pytest.mark.asyncio
async def test_create_bitable_records_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"field_id": "f1", "field_name": "姓名", "type": 1}], "has_more": False},
            {
                "records": [
                    {"record_id": "recA", "fields": {"姓名": "张三"}},
                    {"record_id": "recB", "fields": {"姓名": "李四"}},
                ]
            },
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.create_bitable_records_impl("appX", "tbl1", '[{"姓名":"张三"},{"姓名":"李四"}]')
    req = paged.requests[-1]
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/records/batch_create")
    assert req.body["records"] == [{"fields": {"姓名": "张三"}}, {"fields": {"姓名": "李四"}}]
    assert result["created"] == ["recA", "recB"]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_create_bitable_records_accepts_fields_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"records": [{"record_id": "recA", "fields": {"姓名": "张三"}}]})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_records_impl(
        "appX", "tbl1", '[{"fields":{"姓名":"张三"}}]', validate_fields=False
    )
    assert cap.request.body["records"] == [{"fields": {"姓名": "张三"}}]
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_create_bitable_records_warns_on_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"records": [{"record_id": "recA", "fields": {"姓名": "张三"}}]})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_records_impl(
        "appX", "tbl1", '[{"姓名":"张三","Mentor":"李四"}]', validate_fields=False
    )
    assert result["dropped_fields"] == ["Mentor"]
    assert "Mentor" in result["warning"]


@pytest.mark.asyncio
async def test_create_bitable_records_rejects_unknown_column(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"field_id": "f1", "field_name": "姓名", "type": 1}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_records_impl("appX", "tbl1", '[{"Name":"张三"}]')
    assert result["ok"] is False
    assert result["unknown_fields"] == ["Name"]


@pytest.mark.asyncio
async def test_create_bitable_records_bad_input() -> None:
    assert (await _impl.create_bitable_records_impl("appX", "tbl1", "not json"))["ok"] is False
    assert (await _impl.create_bitable_records_impl("appX", "tbl1", "[]"))["ok"] is False
    assert (await _impl.create_bitable_records_impl("appX", "tbl1", '["a"]'))["ok"] is False
    assert (await _impl.create_bitable_records_impl("appX", "tbl1", "[{}]"))["ok"] is False
    assert (await _impl.create_bitable_records_impl("", "tbl1", '[{"a":1}]'))["ok"] is False


@pytest.mark.asyncio
async def test_create_bitable_record(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"record": {"record_id": "recNew", "fields": {"新人": "张三"}}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_record_impl("appX", "tbl1", '{"新人":"张三","评分":4}')
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    assert req.body["fields"] == {"新人": "张三", "评分": 4}
    assert result["record_id"] == "recNew"


@pytest.mark.asyncio
async def test_create_bitable_record_bad_json() -> None:
    result = await _impl.create_bitable_record_impl("appX", "tbl1", "not json")
    assert result["ok"] is False
    assert "JSON" in result["message"]


@pytest.mark.asyncio
async def test_create_bitable_record_non_object() -> None:
    result = await _impl.create_bitable_record_impl("appX", "tbl1", '["a","b"]')
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_update_bitable_record(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"field_id": "f1", "field_name": "状态", "type": 3}], "has_more": False},
            {"record": {"record_id": "rec1", "fields": {"状态": "已完成"}}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.update_bitable_record_impl("appX", "tbl1", "rec1", '{"状态":"已完成"}')
    req = paged.requests[-1]
    assert req.http_method.name == "PUT"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"
    assert req.paths["record_id"] == "rec1"
    assert req.body["fields"] == {"状态": "已完成"}
    assert result["ok"] is True
    assert result["updated_fields"] == ["状态"]
    assert "dropped_fields" not in result


@pytest.mark.asyncio
async def test_update_bitable_record_skips_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"record": {"record_id": "rec1", "fields": {"任意列": 1}}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_bitable_record_impl("appX", "tbl1", "rec1", '{"任意列":1}', validate_fields=False)
    # Only one call — no field listing when validation is off.
    assert cap.request.http_method.name == "PUT"
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_update_bitable_record_rejects_unknown_column(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"field_id": "f1", "field_name": "状态", "type": 3}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_bitable_record_impl("appX", "tbl1", "rec1", '{"Status":"done"}')
    assert result["ok"] is False
    assert result["unknown_fields"] == ["Status"]
    assert result["valid_fields"] == ["状态"]


@pytest.mark.asyncio
async def test_update_bitable_record_warns_on_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {
                "items": [
                    {"field_id": "f1", "field_name": "状态", "type": 3},
                    {"field_id": "f2", "field_name": "评分", "type": 2},
                ],
                "has_more": False,
            },
            {"record": {"record_id": "rec1", "fields": {"状态": "已完成"}}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.update_bitable_record_impl("appX", "tbl1", "rec1", '{"状态":"已完成","评分":5}')
    assert result["ok"] is True
    assert result["dropped_fields"] == ["评分"]
    assert "评分" in result["warning"]


@pytest.mark.asyncio
async def test_update_bitable_record_allows_null_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"field_id": "f1", "field_name": "备注", "type": 1}], "has_more": False},
            {"record": {"record_id": "rec1", "fields": {}}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.update_bitable_record_impl("appX", "tbl1", "rec1", '{"备注":null}')
    assert paged.requests[-1].body["fields"] == {"备注": None}
    # A cleared cell is absent from the echo by design — not a dropped write.
    assert result["ok"] is True
    assert "dropped_fields" not in result


@pytest.mark.asyncio
async def test_update_bitable_record_bad_input() -> None:
    assert (await _impl.update_bitable_record_impl("appX", "tbl1", "rec1", "not json"))["ok"] is False
    assert (await _impl.update_bitable_record_impl("appX", "tbl1", "rec1", "{}"))["ok"] is False
    assert (await _impl.update_bitable_record_impl("appX", "tbl1", "", '{"a":1}'))["ok"] is False
    assert (await _impl.update_bitable_record_impl("", "tbl1", "rec1", '{"a":1}'))["ok"] is False


@pytest.mark.asyncio
async def test_update_bitable_records_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"field_id": "f1", "field_name": "状态", "type": 3}], "has_more": False},
            {"records": [{"record_id": "recA", "fields": {"状态": "已完成"}}, {"record_id": "recB", "fields": {}}]},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.update_bitable_records_impl(
        "appX",
        "tbl1",
        '[{"record_id":"recA","fields":{"状态":"已完成"}},{"record_id":"recB","fields":{"状态":"进行中"}}]',
    )
    req = paged.requests[-1]
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/records/batch_update")
    assert req.body["records"][1]["record_id"] == "recB"
    assert result["updated"] == ["recA", "recB"]
    assert result["count"] == 2
    # recB came back with no fields written at all.
    assert result["dropped_fields"] == ["recB.状态"]


@pytest.mark.asyncio
async def test_update_bitable_records_bad_input() -> None:
    assert (await _impl.update_bitable_records_impl("appX", "tbl1", "not json"))["ok"] is False
    assert (await _impl.update_bitable_records_impl("appX", "tbl1", "[]"))["ok"] is False
    assert (await _impl.update_bitable_records_impl("appX", "tbl1", '[{"fields":{"a":1}}]'))["ok"] is False
    assert (await _impl.update_bitable_records_impl("appX", "tbl1", '[{"record_id":"recA"}]'))["ok"] is False


@pytest.mark.asyncio
async def test_update_bitable_records_survives_unreadable_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed field-list check must not block the write itself."""

    class _Invoke:
        def __init__(self) -> None:
            self.requests: list[Any] = []

        async def __call__(
            self,
            request: Any,
            user_key: str | None = None,
            prefer: str = "tenant",
            identity: str = "",
            capabilities: list[str] | None = None,
        ) -> dict[str, Any]:
            req = request() if callable(request) else request
            self.requests.append(req)
            if req.http_method.name == "GET":
                return {"ok": False, "message": "no permission to list fields"}
            echo = {"records": [{"record_id": "recA", "fields": {"状态": "x"}}]}
            return {"ok": True, "code": 0, "msg": "", "data": echo}

    inv = _Invoke()
    monkeypatch.setattr(_impl, "_invoke", inv)
    result = await _impl.update_bitable_records_impl("appX", "tbl1", '[{"record_id":"recA","fields":{"状态":"x"}}]')
    assert result["ok"] is True
    assert result["updated"] == ["recA"]


@pytest.mark.asyncio
async def test_delete_bitable_records(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.delete_bitable_records_impl("appX", "tbl1", "recA, recB")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/records/batch_delete")
    assert req.paths["table_id"] == "tbl1"
    assert req.body["records"] == ["recA", "recB"]
    assert result["deleted"] == 2


@pytest.mark.asyncio
async def test_delete_bitable_records_empty() -> None:
    result = await _impl.delete_bitable_records_impl("appX", "tbl1", " , ")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_clear_bitable_table(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"record_id": "r1"}, {"record_id": "r2"}], "has_more": True, "page_token": "pt2"},
            {"items": [{"record_id": "r3"}], "has_more": False, "page_token": ""},
            {},  # batch_delete response
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.clear_bitable_table_impl("appX", "tbl1")
    assert result["deleted"] == 3
    # last request is the batch_delete carrying all 3 ids
    assert paged.requests[-1].body["records"] == ["r1", "r2", "r3"]


@pytest.mark.asyncio
async def test_clear_bitable_table_already_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.clear_bitable_table_impl("appX", "tbl1")
    assert result["ok"] is True
    assert result["deleted"] == 0


@pytest.mark.asyncio
async def test_list_bitable_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [
                {"field_id": "fld1", "field_name": "标题", "type": 1, "is_primary": True},
                {"field_id": "fld2", "field_name": "文本", "type": 1, "is_primary": False},
            ],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_bitable_fields_impl("appX", "tbl1")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/fields")
    assert result["fields"][0] == {"field_id": "fld1", "name": "标题", "type": "文本", "is_primary": True}
    assert result["fields"][1]["is_primary"] is False
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_delete_bitable_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke([{}, {}])
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.delete_bitable_fields_impl("appX", "tbl1", "fldA, fldB")
    assert result["deleted"] == ["fldA", "fldB"]
    assert result["count"] == 2
    last = paged.requests[-1]
    assert last.http_method.name == "DELETE"
    assert last.uri.endswith("/fields/:field_id")
    assert last.paths["field_id"] == "fldB"


@pytest.mark.asyncio
async def test_delete_bitable_fields_empty() -> None:
    result = await _impl.delete_bitable_fields_impl("appX", "tbl1", "")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_create_bitable_app_builds_post(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "app": {
                "app_token": "bascnNew",
                "name": "合同台账",
                "folder_token": "fldA",
                "default_table_id": "tblDefault",
                "time_zone": "Asia/Shanghai",
                "url": "https://feishu.cn/base/bascnNew",
            }
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_app_impl("合同台账", "fldA", "Asia/Shanghai", "ou_1")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/bitable/v1/apps"
    assert req.body == {"name": "合同台账", "folder_token": "fldA", "time_zone": "Asia/Shanghai"}
    assert cap.prefer == "user"
    assert cap.user_key == "ou_1"
    assert result["app_token"] == "bascnNew"
    assert result["default_table_id"] == "tblDefault"
    assert result["url"] == "https://feishu.cn/base/bascnNew"


@pytest.mark.asyncio
async def test_create_bitable_app_omits_blank_optionals(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"app": {"app_token": "bascnX"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_app_impl("台账")
    assert cap.request.body == {"name": "台账"}
    # No url in the response: derive one from the app_token so the user gets a link.
    assert result["url"] == "https://feishu.cn/base/bascnX"


@pytest.mark.asyncio
async def test_create_bitable_table_with_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"table_id": "tblNew", "default_view_id": "vew1", "field_id_list": ["fld1", "fld2"]})
    monkeypatch.setattr(_impl, "_invoke", cap)
    fields = '[{"field_name":"编号","type":1},{"field_name":"金额","type":2}]'
    result = await _impl.create_bitable_table_impl("appX", "合同", fields, "表格视图", "ou_1")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables"
    assert req.paths["app_token"] == "appX"
    assert req.body["table"]["name"] == "合同"
    assert req.body["table"]["fields"][0] == {"field_name": "编号", "type": 1}
    assert req.body["table"]["default_view_name"] == "表格视图"
    assert cap.prefer == "user"
    assert result["table_id"] == "tblNew"
    assert result["field_ids"] == ["fld1", "fld2"]


@pytest.mark.asyncio
async def test_create_bitable_table_without_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"table_id": "tblBare"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_table_impl("appX", "空表")
    assert cap.request.body == {"table": {"name": "空表"}}
    assert result["table_id"] == "tblBare"
    assert result["field_ids"] == []


@pytest.mark.asyncio
async def test_create_bitable_table_requires_app_token_and_name() -> None:
    assert (await _impl.create_bitable_table_impl("", "表"))["ok"] is False
    assert (await _impl.create_bitable_table_impl("appX", " "))["ok"] is False


@pytest.mark.asyncio
async def test_create_bitable_table_bad_fields_json() -> None:
    assert (await _impl.create_bitable_table_impl("appX", "表", "not json"))["ok"] is False
    assert (await _impl.create_bitable_table_impl("appX", "表", "{}"))["ok"] is False
    assert (await _impl.create_bitable_table_impl("appX", "表", "[]"))["ok"] is False
    missing_name = await _impl.create_bitable_table_impl("appX", "表", '[{"type":1}]')
    assert missing_name["ok"] is False
    assert "field_name" in missing_name["message"]
    bad_type = await _impl.create_bitable_table_impl("appX", "表", '[{"field_name":"a","type":"1"}]')
    assert bad_type["ok"] is False
    assert "integer" in bad_type["message"]
    lookup = await _impl.create_bitable_table_impl(
        "appX", "表", '[{"field_name":"a","type":1},{"field_name":"b","type":19}]'
    )
    assert lookup["ok"] is False
    assert "19" in lookup["message"]


@pytest.mark.asyncio
async def test_create_bitable_table_rejects_bad_index_field_type() -> None:
    # A 人员 (11) column cannot be the index column — Feishu answers 1254012.
    result = await _impl.create_bitable_table_impl("appX", "表", '[{"field_name":"负责人","type":11}]')
    assert result["ok"] is False
    assert "index" in result["message"]


@pytest.mark.asyncio
async def test_create_bitable_table_view_name_needs_fields() -> None:
    result = await _impl.create_bitable_table_impl("appX", "表", "", "视图")
    assert result["ok"] is False
    assert "fields_json" in result["message"]


@pytest.mark.asyncio
async def test_create_bitable_field_builds_post(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"field": {"field_id": "fldNew", "field_name": "状态", "type": 3, "is_primary": False}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_field_impl(
        "appX", "tbl1", "状态", 3, '{"options":[{"name":"生效","color":0}]}', "SingleSelect", "ou_1"
    )
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields"
    assert req.paths["table_id"] == "tbl1"
    assert req.body["field_name"] == "状态"
    assert req.body["type"] == 3
    assert req.body["property"] == {"options": [{"name": "生效", "color": 0}]}
    assert req.body["ui_type"] == "SingleSelect"
    assert cap.prefer == "user"
    assert result["field_id"] == "fldNew"
    assert result["type"] == "单选"  # decoded via _BITABLE_FIELD_TYPES


@pytest.mark.asyncio
async def test_create_bitable_field_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"field": {"field_id": "fldT", "field_name": "备注", "type": 1}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_field_impl("appX", "tbl1", "备注")
    assert cap.request.body == {"field_name": "备注", "type": 1}
    assert result["type"] == "文本"


@pytest.mark.asyncio
async def test_create_bitable_field_validates_args() -> None:
    assert (await _impl.create_bitable_field_impl("", "tbl1", "a"))["ok"] is False
    assert (await _impl.create_bitable_field_impl("appX", "", "a"))["ok"] is False
    assert (await _impl.create_bitable_field_impl("appX", "tbl1", " "))["ok"] is False
    assert (await _impl.create_bitable_field_impl("appX", "tbl1", "a", 19))["ok"] is False
    bad_prop = await _impl.create_bitable_field_impl("appX", "tbl1", "a", 3, "{not json")
    assert bad_prop["ok"] is False
    assert "JSON" in bad_prop["message"]
    non_object = await _impl.create_bitable_field_impl("appX", "tbl1", "a", 3, '["x"]')
    assert non_object["ok"] is False


@pytest.mark.asyncio
async def test_create_bitable_field_allows_person_type(monkeypatch: pytest.MonkeyPatch) -> None:
    # 11 is rejected only as a table's first (index) column, not as an added field.
    cap = _CapturedInvoke({"field": {"field_id": "fldP", "field_name": "负责人", "type": 11}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_field_impl("appX", "tbl1", "负责人", 11)
    assert result["ok"] is True
    assert result["type"] == "人员"


@pytest.mark.asyncio
async def test_update_bitable_field_renames_and_keeps_property(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omitting type/property must carry the current definition, not reset it."""
    paged = _PagedInvoke(
        [
            {
                "items": [
                    {
                        "field_id": "fldA",
                        "field_name": "备注",
                        "type": 3,
                        "property": {"options": [{"name": "高", "color": 0}]},
                    }
                ],
                "has_more": False,
            },
            {"field": {"field_id": "fldA", "field_name": "审批意见", "type": 3}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.update_bitable_field_impl("appX", "tbl1", "fldA", "审批意见")
    req = paged.requests[-1]
    assert req.http_method.name == "PUT"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id"
    assert req.paths["field_id"] == "fldA"
    assert req.body["field_name"] == "审批意见"
    assert req.body["type"] == 3
    assert req.body["property"] == {"options": [{"name": "高", "color": 0}]}
    assert result["name"] == "审批意见"


@pytest.mark.asyncio
async def test_update_bitable_field_explicit_args_skip_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"field": {"field_id": "fldA", "field_name": "金额", "type": 2}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_bitable_field_impl("appX", "tbl1", "fldA", "金额", 2, '{"formatter":"0.00"}')
    assert cap.request.http_method.name == "PUT"  # no GET for the field list
    assert cap.request.body["property"] == {"formatter": "0.00"}
    assert result["type"] == "数字"


@pytest.mark.asyncio
async def test_update_bitable_field_rejects_unknown_field_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"field_id": "fldA", "field_name": "备注", "type": 1}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_bitable_field_impl("appX", "tbl1", "fldZZZ", "新名")
    assert result["ok"] is False
    assert "fldZZZ" in result["message"]


@pytest.mark.asyncio
async def test_update_bitable_field_guards_primary_column_type(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"items": [{"field_id": "fldA", "field_name": "编号", "type": 1, "is_primary": True}], "has_more": False}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_bitable_field_impl("appX", "tbl1", "fldA", field_type=11)
    assert result["ok"] is False
    assert "1254012" in result["message"]


@pytest.mark.asyncio
async def test_update_bitable_field_rejects_lookup_type() -> None:
    result = await _impl.update_bitable_field_impl("appX", "tbl1", "fldA", "引用", 19)
    assert result["ok"] is False
    assert "19" in result["message"]


@pytest.mark.asyncio
async def test_update_bitable_field_requires_ids() -> None:
    assert (await _impl.update_bitable_field_impl("", "tbl1", "fldA", "x", 1))["ok"] is False
    assert (await _impl.update_bitable_field_impl("appX", "", "fldA", "x", 1))["ok"] is False
    assert (await _impl.update_bitable_field_impl("appX", "tbl1", "", "x", 1))["ok"] is False


@pytest.mark.asyncio
async def test_create_bitable_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"table_ids": ["tblA", "tblB"]})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_tables_impl("appX", "合同, 付款")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/tables/batch_create")
    assert req.body["tables"] == [{"name": "合同"}, {"name": "付款"}]
    assert result["tables"] == [{"table_id": "tblA", "name": "合同"}, {"table_id": "tblB", "name": "付款"}]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_create_bitable_tables_validates_names() -> None:
    assert (await _impl.create_bitable_tables_impl("appX", " , "))["ok"] is False
    assert (await _impl.create_bitable_tables_impl("appX", "合同/付款"))["ok"] is False
    assert (await _impl.create_bitable_tables_impl("appX", ",".join(f"t{i}" for i in range(51))))["ok"] is False
    assert (await _impl.create_bitable_tables_impl("", "合同"))["ok"] is False


@pytest.mark.asyncio
async def test_delete_bitable_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.delete_bitable_tables_impl("appX", "tblA, tblB")
    req = cap.request
    assert req.uri.endswith("/tables/batch_delete")
    assert req.body["table_ids"] == ["tblA", "tblB"]
    assert result["deleted"] == ["tblA", "tblB"]


@pytest.mark.asyncio
async def test_delete_bitable_tables_last_table_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(request: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "code": "1254034", "message": "The last table cannot be deleted"}

    monkeypatch.setattr(_impl, "_invoke", _refuse)
    result = await _impl.delete_bitable_tables_impl("appX", "tblA")
    assert result["ok"] is False
    assert "last one" in result["hint"]


@pytest.mark.asyncio
async def test_delete_bitable_tables_validates() -> None:
    assert (await _impl.delete_bitable_tables_impl("appX", " , "))["ok"] is False
    assert (await _impl.delete_bitable_tables_impl("appX", ",".join(f"tbl{i}" for i in range(51))))["ok"] is False


@pytest.mark.asyncio
async def test_get_bitable_app(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "app": {
                "app_token": "appX",
                "name": "合同台账",
                "is_advanced": True,
                "time_zone": "Asia/Shanghai",
                "revision": 7,
            }
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_bitable_app_impl("appX")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token"
    assert result["name"] == "合同台账"
    assert result["is_advanced"] is True
    assert result["revision"] == 7
    assert result["url"].endswith("/base/appX")


@pytest.mark.asyncio
async def test_update_bitable_app(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"app": {"app_token": "appX", "name": "新名字", "is_advanced": True}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_bitable_app_impl("appX", "新名字", "true")
    req = cap.request
    assert req.http_method.name == "PUT"
    assert req.body == {"name": "新名字", "is_advanced": True}
    assert result["changed"] == ["is_advanced", "name"]
    assert result["is_advanced"] is True


@pytest.mark.asyncio
async def test_update_bitable_app_validates() -> None:
    assert (await _impl.update_bitable_app_impl("appX"))["ok"] is False  # nothing to change
    assert (await _impl.update_bitable_app_impl("appX", "a/b"))["ok"] is False  # illegal char
    assert (await _impl.update_bitable_app_impl("appX", "x" * 101))["ok"] is False  # too long
    assert (await _impl.update_bitable_app_impl("appX", is_advanced="yes"))["ok"] is False
    assert (await _impl.update_bitable_app_impl("", "新名字"))["ok"] is False


@pytest.mark.asyncio
async def test_update_bitable_app_advanced_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(request: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "code": "1254301", "message": "advanced permission unsupported"}

    monkeypatch.setattr(_impl, "_invoke", _refuse)
    result = await _impl.update_bitable_app_impl("appX", is_advanced="true")
    assert result["ok"] is False
    assert "wiki" in result["hint"]


@pytest.mark.asyncio
async def test_copy_bitable_app(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"app": {"app_token": "appNew", "name": "台账副本", "folder_token": "fldA", "url": "https://x/base/appNew"}}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.copy_bitable_app_impl("appX", "台账副本", "fldA", True, "Asia/Shanghai")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/bitable/v1/apps/:app_token/copy"
    assert req.body == {
        "name": "台账副本",
        "folder_token": "fldA",
        "without_content": True,
        "time_zone": "Asia/Shanghai",
    }
    assert result["app_token"] == "appNew"
    assert result["without_content"] is True


@pytest.mark.asyncio
async def test_copy_bitable_app_omits_blanks(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"app": {"app_token": "appNew"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.copy_bitable_app_impl("appX")
    assert cap.request.body == {}
    assert result["url"].endswith("/base/appNew")


@pytest.mark.asyncio
async def test_copy_bitable_app_in_progress_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(request: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "code": "1254036", "message": "copying"}

    monkeypatch.setattr(_impl, "_invoke", _refuse)
    result = await _impl.copy_bitable_app_impl("appX")
    assert result["ok"] is False
    assert "retry" in result["hint"]


@pytest.mark.asyncio
async def test_copy_bitable_app_requires_token() -> None:
    assert (await _impl.copy_bitable_app_impl(""))["ok"] is False


def test_bitable_tools_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_bitable")
    for name in (
        "feishu_bitable_list_tables",
        "feishu_bitable_list_records",
        "feishu_bitable_search_records",
        "feishu_bitable_get_record",
        "feishu_bitable_create_record",
        "feishu_bitable_create_records",
        "feishu_bitable_update_record",
        "feishu_bitable_update_records",
        "feishu_bitable_delete_records",
        "feishu_bitable_clear_table",
        "feishu_bitable_list_fields",
        "feishu_bitable_delete_fields",
        "feishu_bitable_update_field",
        "feishu_bitable_create_app",
        "feishu_bitable_get_app",
        "feishu_bitable_update_app",
        "feishu_bitable_copy_app",
        "feishu_bitable_create_table",
        "feishu_bitable_create_tables",
        "feishu_bitable_delete_tables",
        "feishu_bitable_create_field",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


# ── Attendance — query clock results (read-only) ──────────────────────────────


@pytest.mark.asyncio
async def test_query_attendance_builds_request_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "user_task_results": [
                {
                    "user_id": "e1",
                    "employee_name": "张三",
                    "day": 20260714,
                    "records": [
                        {
                            "check_in_record": {"check_time": "1752460200", "location_name": "总部"},
                            "check_in_result": "Normal",
                            "check_out_record": {"check_time": "1752490200", "location_name": "总部"},
                            "check_out_result": "Late",
                        }
                    ],
                }
            ],
            "invalid_user_ids": ["bad1"],
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.query_attendance_impl("e1, e2", "20260714", "20260714", "employee_id", False)
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/attendance/v1/user_tasks/query"
    assert _qdict(req).get("employee_type") == "employee_id"
    assert req.body["user_ids"] == ["e1", "e2"]  # comma string split
    assert req.body["check_date_from"] == 20260714
    r0 = result["results"][0]
    assert r0["name"] == "张三"
    assert r0["check_in_result"] == "Normal"
    assert r0["check_out_result"] == "Late"
    assert r0["check_in_time"]  # timestamp formatted to a non-empty string
    assert result["invalid_user_ids"] == ["bad1"]


@pytest.mark.asyncio
async def test_query_attendance_empty_users() -> None:
    result = await _impl.query_attendance_impl("  ,  ", "20260714", "20260714", "employee_id", False)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_query_attendance_bad_date() -> None:
    result = await _impl.query_attendance_impl("e1", "2026-07-14", "20260714", "employee_id", False)
    assert result["ok"] is False
    assert "yyyyMMdd" in result["message"]


@pytest.mark.asyncio
async def test_query_attendance_missing_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "user_task_results": [
                {
                    "user_id": "e1",
                    "employee_name": "李四",
                    "day": 20260714,
                    "records": [{"check_in_result": "Lack"}],
                }
            ]
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.query_attendance_impl("e1", "20260714", "20260714")
    r0 = result["results"][0]
    assert r0["check_out_time"] == ""  # no check_out_record -> empty, no crash
    assert r0["check_in_result"] == "Lack"


def test_attendance_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_attendance")
    fn = mod.feishu_attendance_query
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Attendance admin config — groups (考勤组) & shifts (班次), read-only ────────


@pytest.mark.asyncio
async def test_list_attendance_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "group_list": [
                {"group_id": "g1", "group_name": "总部考勤"},
                {"group_id": "g2", "group_name": "研发考勤"},
            ],
            "has_more": True,
            "page_token": "tok2",
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_attendance_groups_impl(50, "")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/attendance/v1/groups"
    assert _qdict(req).get("page_size") == "50"
    assert "page_token" not in _qdict(req)  # empty token not sent
    assert result["count"] == 2
    assert result["groups"][0] == {"group_id": "g1", "group_name": "总部考勤"}
    assert result["has_more"] is True
    assert result["page_token"] == "tok2"


@pytest.mark.asyncio
async def test_list_attendance_groups_clamps_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"group_list": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.list_attendance_groups_impl(999, "ptok")
    q = _qdict(cap.request)
    assert q.get("page_size") == "50"  # clamped to max 50
    assert q.get("page_token") == "ptok"


@pytest.mark.asyncio
async def test_get_attendance_group_config(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "group_id": "g1",
            "group_name": "总部考勤",
            "group_type": 0,
            "punch_type": 3,
            "allow_out_punch": True,
            "allow_pc_punch": False,
            "work_day_no_punch_as_lack": True,
            "punch_day_shift_ids": ["s1", "s2"],
            "ignored_field": "dropped",
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_attendance_group_impl("g1", "employee_id", "open_id")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/attendance/v1/groups/:group_id"
    assert req.paths["group_id"] == "g1"
    assert _qdict(req).get("employee_type") == "employee_id"
    assert _qdict(req).get("dept_type") == "open_id"
    grp = result["group"]
    assert grp["punch_type"] == 3
    assert grp["punch_day_shift_ids"] == ["s1", "s2"]
    assert grp["work_day_no_punch_as_lack"] is True
    assert "ignored_field" not in grp  # only whitelisted config fields kept


@pytest.mark.asyncio
async def test_get_attendance_group_requires_id() -> None:
    result = await _impl.get_attendance_group_impl("  ")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_list_shifts(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "shift_list": [
                {"shift_id": "s1", "shift_name": "早班", "punch_times": 2, "is_flexible": True},
            ],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_shifts_impl(20, "")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/attendance/v1/shifts"
    assert _qdict(req).get("page_size") == "20"
    s0 = result["shifts"][0]
    assert s0["shift_name"] == "早班"
    assert s0["punch_times"] == 2
    assert s0["is_flexible"] is True
    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_get_shift_config(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "shift_id": "s1",
            "shift_name": "早班",
            "punch_times": 2,
            "is_flexible": True,
            "flexible_minutes": 30,
            "flexible_rule": [{"flexible_early_minutes": 30, "flexible_late_minutes": 30}],
            "punch_time_rule": [{"on_time": "09:00", "off_time": "18:00", "late_minutes_as_late": 10}],
            "not_a_config_field": "dropped",
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_shift_impl("s1")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/attendance/v1/shifts/:shift_id"
    assert req.paths["shift_id"] == "s1"
    shift = result["shift"]
    assert shift["punch_time_rule"][0]["on_time"] == "09:00"
    assert shift["flexible_rule"][0]["flexible_late_minutes"] == 30
    assert shift["flexible_minutes"] == 30
    assert "not_a_config_field" not in shift  # only whitelisted config fields kept


@pytest.mark.asyncio
async def test_get_shift_requires_id() -> None:
    result = await _impl.get_shift_impl("")
    assert result["ok"] is False


def test_attendance_config_tools_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_attendance")
    for name in (
        "feishu_attendance_groups",
        "feishu_attendance_group_config",
        "feishu_attendance_shifts",
        "feishu_attendance_shift_config",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), name


# ── Tasks — create/assign, list, update, complete ─────────────────────────────


@pytest.mark.asyncio
async def test_create_task_builds_members_and_due(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"task": {"guid": "g1", "summary": "写周报", "url": "http://t/g1"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_task_impl("写周报", "本周总结", "2026-07-15 18:00", "ou_a,ou_b", "ou_c")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/task/v2/tasks"
    assert req.body["summary"] == "写周报"
    assert req.body["description"] == "本周总结"
    assert req.body["due"]["timestamp"].isdigit()
    roles = [(m["id"], m["role"]) for m in req.body["members"]]
    assert ("ou_a", "assignee") in roles
    assert ("ou_b", "assignee") in roles
    assert ("ou_c", "follower") in roles
    # member kind must be "user" + id_type "open_id" (type="open_id" is rejected 1470400)
    assert all(m["type"] == "user" and m["id_type"] == "open_id" for m in req.body["members"])
    assert result["task_guid"] == "g1"


@pytest.mark.asyncio
async def test_create_task_requires_summary() -> None:
    result = await _impl.create_task_impl("  ", "", "", "", "")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_create_task_no_due_no_members(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"task": {"guid": "g2", "summary": "s"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.create_task_impl("s", "", "", "", "")
    assert "due" not in cap.request.body
    assert "members" not in cap.request.body


def test_due_to_ms_parsing() -> None:
    assert _impl._due_to_ms("") is None
    assert _impl._due_to_ms("not a date") is None
    assert _impl._due_to_ms("2026-07-15").isdigit()
    assert _impl._due_to_ms("2026-07-15 18:00").isdigit()


@pytest.mark.asyncio
async def test_list_tasks_query(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [{"guid": "g1", "summary": "s1", "status": "todo", "due": {"timestamp": "123"}, "url": "u"}],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_tasks_impl("false", 50, "")
    q = _qdict(cap.request)
    assert cap.request.http_method.name == "GET"
    assert q.get("type") == "my_tasks"
    assert q.get("completed") == "false"
    assert result["tasks"][0] == {"guid": "g1", "summary": "s1", "status": "todo", "due": "123", "url": "u"}


@pytest.mark.asyncio
async def test_complete_task_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.complete_task_impl("g1", True)
    req = cap.request
    assert req.http_method.name == "PATCH"
    assert req.paths["task_guid"] == "g1"
    assert req.body["update_fields"] == ["completed_at"]
    assert req.body["task"]["completed_at"] != "0"
    # reopen
    cap2 = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap2)
    await _impl.complete_task_impl("g1", False)
    assert cap2.request.body["task"]["completed_at"] == "0"


@pytest.mark.asyncio
async def test_update_task_only_provided_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_task_impl("g1", "新标题", "", "")
    assert cap.request.body["update_fields"] == ["summary"]  # description/due omitted -> not cleared
    assert cap.request.body["task"] == {"summary": "新标题"}
    assert result["updated"] == ["summary"]


@pytest.mark.asyncio
async def test_update_task_nothing_to_update() -> None:
    result = await _impl.update_task_impl("g1", "", "", "")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_get_task_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "task": {
                "guid": "g1",
                "summary": "写周报",
                "status": "done",
                "completed_at": "1752490200000",
                "members": [{"id": "ou_a", "name": "王炜博", "role": "assignee"}],
                "assignee_related": [{"id": "ou_a", "completed_at": "1752490200000"}],
                "url": "http://t/g1",
            }
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_task_impl("g1")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/task/v2/tasks/:task_guid"
    assert req.paths["task_guid"] == "g1"
    assert result["status"] == "done"
    assert result["completed"] is True
    assert result["completed_at"]  # formatted, non-empty
    assert result["members"][0]["name"] == "王炜博"
    assert result["assignee_completion"][0]["id"] == "ou_a"


@pytest.mark.asyncio
async def test_get_task_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"task": {"guid": "g1", "summary": "s", "status": "todo", "members": []}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_task_impl("g1")
    assert result["completed"] is False
    assert result["completed_at"] == ""


def test_task_tools_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_task")
    for name in (
        "feishu_task_create",
        "feishu_task_get",
        "feishu_task_list",
        "feishu_task_update",
        "feishu_task_complete",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


# ── Calendar — create event ───────────────────────────────────────────────────


def test_time_to_info_parsing() -> None:
    timed = _impl._time_to_info("2026-07-15 14:30", "Asia/Shanghai")
    assert timed is not None and timed["timestamp"].isdigit() and timed["timezone"] == "Asia/Shanghai"
    allday = _impl._time_to_info("2026-07-15", "Asia/Shanghai")
    assert allday == {"date": "2026-07-15", "timezone": "Asia/Shanghai"}
    assert _impl._time_to_info("", "Asia/Shanghai") is None
    assert _impl._time_to_info("bad", "Asia/Shanghai") is None


@pytest.mark.asyncio
async def test_create_event_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cal_id() -> str:
        return "cal_1"

    monkeypatch.setattr(_impl, "_get_primary_calendar_id", _cal_id)
    cap = _CapturedInvoke({"event": {"event_id": "ev_1", "summary": "周会"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_event_impl("周会", "2026-07-15 14:00", "2026-07-15 15:00", "议题…")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/calendar/v4/calendars/:calendar_id/events"
    assert req.paths["calendar_id"] == "cal_1"
    assert req.body["summary"] == "周会"
    assert req.body["start_time"]["timestamp"].isdigit()
    assert req.body["end_time"]["timezone"] == "Asia/Shanghai"
    assert result["event_id"] == "ev_1"


@pytest.mark.asyncio
async def test_create_event_with_attendees(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cal_id() -> str:
        return "cal_1"

    monkeypatch.setattr(_impl, "_get_primary_calendar_id", _cal_id)
    paged = _PagedInvoke([{"event": {"event_id": "ev_1"}}, {}])  # create, then add-attendees
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.create_event_impl("周会", "2026-07-15", "2026-07-15", "", "ou_a, ou_b")
    assert len(paged.requests) == 2
    att_req = paged.requests[1]
    assert att_req.uri == "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees"
    assert att_req.paths["event_id"] == "ev_1"
    ids = [a["user_id"] for a in att_req.body["attendees"]]
    assert ids == ["ou_a", "ou_b"]
    assert all(a["type"] == "user" for a in att_req.body["attendees"])
    assert result["attendees_added"] == ["ou_a", "ou_b"]


@pytest.mark.asyncio
async def test_create_event_bad_time() -> None:
    result = await _impl.create_event_impl("x", "not-a-date", "2026-07-15 15:00")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_create_event_no_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none() -> None:
        return None

    monkeypatch.setattr(_impl, "_get_primary_calendar_id", _none)
    result = await _impl.create_event_impl("x", "2026-07-15 14:00", "2026-07-15 15:00")
    assert result["ok"] is False


def test_calendar_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_calendar")
    fn = mod.feishu_calendar_create_event
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Calendar — list events (read schedule) ────────────────────────────────────


@pytest.mark.asyncio
async def test_list_events_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_events_impl("2026-07-15 09:00", "2026-07-15 18:00", "cal_x")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/calendar/v4/calendars/:calendar_id/events"
    assert req.paths["calendar_id"] == "cal_x"
    q = _qdict(req)
    assert q["start_time"].isdigit() and q["end_time"].isdigit()
    assert int(q["end_time"]) > int(q["start_time"])
    assert q["user_id_type"] == "open_id"
    assert result["ok"] is True and result["calendar_id"] == "cal_x"


@pytest.mark.asyncio
async def test_list_events_uses_primary_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cal_id() -> str:
        return "cal_primary"

    monkeypatch.setattr(_impl, "_get_primary_calendar_id", _cal_id)
    cap = _CapturedInvoke({"items": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_events_impl("2026-07-15", "2026-07-16")
    assert cap.request.paths["calendar_id"] == "cal_primary"
    assert result["calendar_id"] == "cal_primary"


@pytest.mark.asyncio
async def test_list_events_bad_time() -> None:
    result = await _impl.list_events_impl("nope", "2026-07-15")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_list_events_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        {
            "event_id": "ev_1",
            "summary": "周会",
            "description": "议题",
            "start_time": {"timestamp": "1752562800"},
            "end_time": {"timestamp": "1752566400"},
            "status": "confirmed",
        },
        {
            "event_id": "ev_2",
            "summary": "全天",
            "start_time": {"date": "2026-07-15"},
            "end_time": {"date": "2026-07-16"},
        },
    ]
    cap = _CapturedInvoke({"items": items, "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_events_impl("2026-07-15", "2026-07-16", "cal_x")
    assert result["count"] == 2
    assert result["events"][0]["event_id"] == "ev_1" and result["events"][0]["summary"] == "周会"
    assert result["events"][0]["is_all_day"] is False
    assert result["events"][1]["is_all_day"] is True and result["events"][1]["start"] == "2026-07-15"


# ── Calendar — create one event per person ────────────────────────────────────


@pytest.mark.asyncio
async def test_create_per_person_one_event_each(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _cal_id() -> str:
        return "cal_1"

    monkeypatch.setattr(_impl, "_get_primary_calendar_id", _cal_id)
    # For each person: create event, then add-attendees. 3 people -> 6 pages.
    paged = _PagedInvoke(
        [{"event": {"event_id": "ev_a"}}, {}, {"event": {"event_id": "ev_b"}}, {}, {"event": {"event_id": "ev_c"}}, {}]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.create_events_per_person_impl(
        "值班", "2026-07-15 09:00", "2026-07-15 18:00", "ou_a, ou_b, ou_c"
    )
    assert result["ok"] is True
    assert result["count"] == 3
    assert [c["open_id"] for c in result["created"]] == ["ou_a", "ou_b", "ou_c"]
    # each add-attendees request invites exactly that one person
    att_reqs = [r for r in paged.requests if "attendees" in r.uri]
    invited = [[a["user_id"] for a in r.body["attendees"]] for r in att_reqs]
    assert invited == [["ou_a"], ["ou_b"], ["ou_c"]]


@pytest.mark.asyncio
async def test_create_per_person_empty_attendees() -> None:
    result = await _impl.create_events_per_person_impl("x", "2026-07-15", "2026-07-15", "  ")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_create_per_person_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def _fake_create(
        summary: str, start: str, end: str, description: str = "", attendees: str = "", timezone: str = "Asia/Shanghai"
    ) -> dict[str, Any]:
        calls["n"] += 1
        if attendees == "ou_bad":
            return {"ok": False, "message": "Feishu API error 190002: no permission"}
        return {"ok": True, "event_id": f"ev_{attendees}"}

    monkeypatch.setattr(_impl, "create_event_impl", _fake_create)
    result = await _impl.create_events_per_person_impl("值班", "2026-07-15", "2026-07-15", "ou_ok, ou_bad")
    assert result["ok"] is False
    assert [c["open_id"] for c in result["created"]] == ["ou_ok"]
    assert result["failed"][0]["open_id"] == "ou_bad"


def test_calendar_read_write_tools_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_calendar")
    for name in ("feishu_calendar_list_events", "feishu_calendar_create_per_person"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), name


# ── Thread read — clean sender + text extraction ──────────────────────────────


def test_message_plain_text_variants() -> None:
    # plain text
    txt = _impl._message_plain_text({"body": {"content": '{"text":"你好 <at></at>"}'}})
    assert txt == "你好 <at></at>"
    # post rich text — nested title/blocks, text nodes concatenated
    post = {
        "body": {
            "content": json.dumps(
                {"zh_cn": {"content": [[{"tag": "at", "user_id": "ou_x"}, {"tag": "text", "text": "看看这个清单"}]]}}
            )
        }
    }
    assert "看看这个清单" in _impl._message_plain_text(post)
    # recalled message -> empty
    assert _impl._message_plain_text({"deleted": True, "body": {"content": '{"text":"x"}'}}) == ""


@pytest.mark.asyncio
async def test_read_thread_parses_sender_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [
                {
                    "message_id": "om_1",
                    "msg_type": "text",
                    "create_time": "1752000000000",
                    "sender": {"id": "ou_zhang", "sender_type": "user"},
                    "body": {"content": '{"text":"我的todo: 1.写周报 2.交方案"}'},
                },
                {
                    "message_id": "om_2",
                    "msg_type": "text",
                    "sender": {"id": "cli_bot", "sender_type": "app"},
                    "body": {"content": '{"text":"机器人消息"}'},
                },
            ],
            "has_more": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_thread_impl("omt_1")
    req = cap.request
    assert req.uri == "/open-apis/im/v1/messages"
    assert _qdict(req).get("container_id_type") == "thread"
    m0 = result["messages"][0]
    assert m0["sender_open_id"] == "ou_zhang"  # user sender -> open_id
    assert "写周报" in m0["text"]
    assert result["messages"][1]["sender_open_id"] == ""  # app sender -> no open_id
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_read_thread_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {
                "items": [
                    {
                        "message_id": "m1",
                        "sender": {"id": "ou_a", "sender_type": "user"},
                        "body": {"content": '{"text":"a"}'},
                    }
                ],
                "has_more": True,
                "page_token": "pt2",
            },
            {
                "items": [
                    {
                        "message_id": "m2",
                        "sender": {"id": "ou_b", "sender_type": "user"},
                        "body": {"content": '{"text":"b"}'},
                    }
                ],
                "has_more": False,
            },
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.read_thread_impl("omt_1")
    assert len(paged.requests) == 2
    assert result["count"] == 2


def test_thread_read_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_message")
    fn = mod.feishu_thread_read
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Contact — list department members ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_department_members_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"user_id": "e1", "open_id": "ou_1", "name": "张三"}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_department_members_impl("0", "open_department_id", "open_id", False)
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/contact/v3/users/find_by_department")
    q = _qdict(req)
    assert q.get("department_id") == "0"
    assert q.get("user_id_type") == "open_id"
    assert q.get("page_size") == "50"
    assert result["members"] == [{"user_id": "e1", "open_id": "ou_1", "name": "张三"}]
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_department_members_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    paged = _PagedInvoke(
        [
            {"items": [{"open_id": "ou_1", "name": "A"}], "has_more": True, "page_token": "pt2"},
            {"items": [{"open_id": "ou_2", "name": "B"}], "has_more": False, "page_token": ""},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", paged)
    result = await _impl.list_department_members_impl("d1", "department_id", "open_id", False)
    assert len(paged.requests) == 2
    assert _qdict(paged.requests[1]).get("page_token") == "pt2"
    assert result["count"] == 2


def test_contact_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_contact")
    fn = mod.feishu_department_members
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


@pytest.mark.asyncio
async def test_department_members_recursive_walks_children(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_invoke(req: Any) -> dict[str, Any]:
        calls.append(req.uri)
        if req.uri.endswith("/children"):
            did = req.paths["department_id"]
            # root "0" has one child "c1"; c1 has no children
            items = [{"open_department_id": "c1"}] if did == "0" else []
            return {"ok": True, "code": 0, "msg": "", "data": {"items": items, "has_more": False}}
        did = _qdict(req).get("department_id")
        name = "root-user" if did == "0" else "child-user"
        oid = "ou_root" if did == "0" else "ou_child"
        return {
            "ok": True,
            "code": 0,
            "msg": "",
            "data": {"items": [{"open_id": oid, "name": name}], "has_more": False},
        }

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    result = await _impl.list_department_members_impl("0", "open_department_id", "open_id", True)
    assert result["count"] == 2  # root + child, de-duped
    assert any(u.endswith("/children") for u in calls)  # walked children


# ── Approval — list instances + attachment parsing ────────────────────────────


@pytest.mark.asyncio
async def test_list_approval_instances_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"instance_code_list": ["i1", "i2"], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_approval_instances_impl("APV_CODE", "1000", "2000")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/approval/v4/instances")
    q = _qdict(req)
    assert q.get("approval_code") == "APV_CODE"
    assert q.get("start_time") == "1000"
    assert result["instance_codes"] == ["i1", "i2"]
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_list_approval_instances_requires_code() -> None:
    result = await _impl.list_approval_instances_impl("", "1000", "2000")
    assert result["ok"] is False
    assert "approval_code" in result["message"]


def test_parse_approval_attachments_url_and_drive() -> None:
    form = json.dumps(
        [
            {"id": "w1", "name": "发票", "type": "attachmentV2", "value": ["https://f.co/a.jpg", "https://f.co/b.jpg"]},
            {"id": "w2", "name": "合同", "type": "document", "value": ["doccnXXX"]},
            {"id": "w3", "name": "金额", "type": "number", "value": "100"},
        ]
    )
    atts = _impl._parse_approval_attachments(form)
    kinds = {(a["kind"], a["value"]) for a in atts}
    assert ("url", "https://f.co/a.jpg") in kinds
    assert ("url", "https://f.co/b.jpg") in kinds
    assert ("drive", "doccnXXX") in kinds
    assert all(a["value"] != "100" for a in atts)  # non-file widget ignored


@pytest.mark.asyncio
async def test_get_approval_instance_exposes_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    form = json.dumps([{"name": "发票", "type": "image", "value": ["https://f.co/x.png"]}])
    cap = _CapturedInvoke({"approval_code": "APV", "status": "APPROVED", "user_id": "e1", "form": form})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_approval_instance_impl("inst1", "open_id")
    assert cap.request.paths["instance_id"] == "inst1"
    assert result["attachments"] == [{"name": "发票", "type": "image", "kind": "url", "value": "https://f.co/x.png"}]


def test_approval_list_instances_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_approval")
    fn = mod.feishu_approval_list_instances
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Drive — download file/attachment ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_file_via_media_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        async def arequest(self, req: Any) -> Any:
            captured["uri"] = req.uri
            captured["token"] = req.paths.get("file_token")
            return _FakeResp(None, "", b"\x89PNG\r\nbinary")

    monkeypatch.setattr(_impl, "_get_client", lambda: _Client())
    dest = tmp_path / "sub" / "receipt.png"
    result = await _impl.download_file_impl("media_tok", str(dest), False)
    assert result["ok"] is True
    assert captured["uri"].endswith("/drive/v1/medias/:file_token/download")
    assert captured["token"] == "media_tok"
    assert dest.read_bytes() == b"\x89PNG\r\nbinary"
    assert result["bytes"] == len(b"\x89PNG\r\nbinary")


@pytest.mark.asyncio
async def test_download_file_via_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_url_bytes(url: str) -> tuple[bytes | None, str]:
        assert url == "https://f.co/a.jpg"
        return b"JPEGDATA", ""

    monkeypatch.setattr(_impl, "_download_url_bytes", fake_url_bytes)
    dest = tmp_path / "claim" / "a.jpg"
    result = await _impl.download_file_impl("https://f.co/a.jpg", str(dest), True)
    assert result["ok"] is True
    assert dest.read_bytes() == b"JPEGDATA"


@pytest.mark.asyncio
async def test_download_file_url_expired_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def fake_url_bytes(url: str) -> tuple[bytes | None, str]:
        return None, "HTTP 403 — the attachment link may have expired (approval-form URLs are valid ~12h)."

    monkeypatch.setattr(_impl, "_download_url_bytes", fake_url_bytes)
    result = await _impl.download_file_impl("https://f.co/gone.jpg", str(tmp_path / "x.jpg"), True)
    assert result["ok"] is False
    assert "expired" in result["message"]


def test_file_download_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_drive")
    fn = mod.feishu_file_download
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


@pytest.mark.asyncio
async def test_download_media_with_user_key_uses_uat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _UatClient:
        async def arequest(self, req: Any, option: Any = None) -> Any:
            captured["uri"] = req.uri
            captured["option"] = option
            return _FakeResp(None, "", b"PDFBYTES")

    monkeypatch.setattr(_impl, "_get_uat_client", lambda: _UatClient())

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    dest = tmp_path / "章程.pdf"
    result = await _impl.download_file_impl("media_tok", str(dest), False, "ou_a")
    assert result["ok"] is True
    assert dest.read_bytes() == b"PDFBYTES"
    assert captured["uri"].endswith("/drive/v1/medias/:file_token/download")
    assert captured["option"].user_access_token == "uat_tok"


@pytest.mark.asyncio
async def test_download_media_user_key_not_authorized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    result = await _impl.download_file_impl("media_tok", str(tmp_path / "x.pdf"), False, "ou_a")
    assert result["ok"] is False
    assert result.get("need_auth") is True


@pytest.mark.asyncio
async def test_download_media_tenant_first_skips_uat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Even with a user_key, the bot's tenant token is tried first; if it can fetch
    the file, the UAT is never resolved (no needless authorization)."""

    class _TenantClient:
        async def arequest(self, req: Any) -> Any:  # tenant path (no option)
            return _FakeResp(None, "", b"TENANTBYTES")

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())

    async def _uat_should_not_run(user_key: str = "") -> Any:
        raise AssertionError("UAT must not run when tenant can download the file")

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat_should_not_run)
    dest = tmp_path / "t.pdf"
    result = await _impl.download_file_impl("media_tok", str(dest), False, "ou_a")
    assert result["ok"] is True
    assert dest.read_bytes() == b"TENANTBYTES"


@pytest.mark.asyncio
async def test_download_media_empty_user_key_uses_tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _TenantClient:
        async def arequest(self, req: Any) -> Any:  # no option arg → tenant path
            captured["uri"] = req.uri
            return _FakeResp(None, "", b"BYTES")

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())

    async def _uat_should_not_run(user_key: str = "") -> Any:
        raise AssertionError("UAT path must not run for empty user_key")

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat_should_not_run)
    result = await _impl.download_file_impl("media_tok", str(tmp_path / "y.bin"), False, "")
    assert result["ok"] is True
    assert captured["uri"].endswith("/drive/v1/medias/:file_token/download")


@pytest.mark.asyncio
async def test_get_message_image_via_tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        async def arequest(self, req: Any) -> Any:
            captured["uri"] = req.uri
            captured["message_id"] = req.paths.get("message_id")
            captured["file_key"] = req.paths.get("file_key")
            captured["queries"] = req.queries
            return _FakeResp(None, "", b"\x89PNG\r\nimg")

    monkeypatch.setattr(_impl, "_get_client", lambda: _Client())
    dest = tmp_path / "sub" / "pic.png"
    result = await _impl.get_message_image_impl("om_1", "img_v3_abc", str(dest))
    assert result["ok"] is True
    assert captured["uri"].endswith("/im/v1/messages/:message_id/resources/:file_key")
    assert captured["message_id"] == "om_1"
    assert captured["file_key"] == "img_v3_abc"
    assert ("type", "image") in captured["queries"]
    assert dest.read_bytes() == b"\x89PNG\r\nimg"
    assert result["bytes"] == len(b"\x89PNG\r\nimg")


@pytest.mark.asyncio
async def test_get_message_image_file_type_query(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        async def arequest(self, req: Any) -> Any:
            captured["queries"] = req.queries
            return _FakeResp(None, "", b"FILEBYTES")

    monkeypatch.setattr(_impl, "_get_client", lambda: _Client())
    dest = tmp_path / "a.mp4"
    result = await _impl.get_message_image_impl("om_2", "file_v3_x", str(dest), "file")
    assert result["ok"] is True
    assert ("type", "file") in captured["queries"]
    assert dest.read_bytes() == b"FILEBYTES"


@pytest.mark.asyncio
async def test_get_message_image_with_user_key_uses_uat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _UatClient:
        async def arequest(self, req: Any, option: Any = None) -> Any:
            captured["uri"] = req.uri
            captured["option"] = option
            return _FakeResp(None, "", b"UATIMG")

    monkeypatch.setattr(_impl, "_get_client", lambda: None)
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: _UatClient())

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    dest = tmp_path / "u.png"
    result = await _impl.get_message_image_impl("om_3", "img_v3_u", str(dest), "image", "ou_a")
    assert result["ok"] is True
    assert dest.read_bytes() == b"UATIMG"
    assert captured["uri"].endswith("/im/v1/messages/:message_id/resources/:file_key")
    assert captured["option"].user_access_token == "uat_tok"


@pytest.mark.asyncio
async def test_get_message_image_user_key_not_authorized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_impl, "_get_client", lambda: None)
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    result = await _impl.get_message_image_impl("om_4", "img_v3_z", str(tmp_path / "x.png"), "image", "ou_a")
    assert result["ok"] is False
    assert result.get("need_auth") is True


@pytest.mark.asyncio
async def test_get_message_image_requires_args() -> None:
    result = await _impl.get_message_image_impl("", "img_v3", "x.png")
    assert result["ok"] is False


def test_image_get_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_message")
    fn = mod.feishu_image_get
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


@pytest.mark.asyncio
async def test_delete_file_builds_delete_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"task_id": ""})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.delete_file_impl("doccnX", "docx")
    assert result["ok"] is True
    assert result["file_token"] == "doccnX"
    assert result["type"] == "docx"
    req = cap.request
    assert req.http_method.name == "DELETE"
    assert req.uri == "/open-apis/drive/v1/files/:file_token"
    assert req.paths["file_token"] == "doccnX"
    assert _impl.AccessTokenType.USER in req.token_types
    # empty user_key -> tenant path (no user_key forwarded)
    assert cap.user_key in (None, "")


@pytest.mark.asyncio
async def test_delete_file_requires_token() -> None:
    result = await _impl.delete_file_impl("  ", "docx")
    assert result["ok"] is False
    assert "file_token" in result["message"]


@pytest.mark.asyncio
async def test_delete_file_rejects_bad_type() -> None:
    result = await _impl.delete_file_impl("doccnX", "video")
    assert result["ok"] is False
    assert "file_type" in result["message"]


@pytest.mark.asyncio
async def test_delete_file_folder_returns_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"task_id": "tsk_123"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.delete_file_impl("fldrX", "folder")
    assert result["ok"] is True
    assert result["task_id"] == "tsk_123"


@pytest.mark.asyncio
async def test_delete_file_user_key_routes_through_uat(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CapturingUatClient({"code": 0, "data": {}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    monkeypatch.setattr(_impl, "missing_capabilities", lambda key, needed: [])
    result = await _impl.delete_file_impl("doccnX", "docx", "ou_a", "user")
    assert result["ok"] is True
    assert client.option.user_access_token == "uat_tok"
    assert client.request.http_method.name == "DELETE"


@pytest.mark.asyncio
async def test_delete_file_as_user_without_token_prompts_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acting as the user with no usable token asks them to authorize."""
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    monkeypatch.setattr(_impl, "missing_capabilities", lambda key, needed: [])
    result = await _impl.delete_file_impl("doccnX", "docx", "ou_a", "user")
    assert result["ok"] is False
    assert result.get("need_auth") is True


@pytest.mark.asyncio
async def test_delete_file_as_bot_uses_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """identity='bot': the bot deletes it with its own permissions, no auth prompt."""

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"task_id": "tsk_1"}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())
    result = await _impl.delete_file_impl("doccnX", "docx", "ou_a", "bot")
    assert result["ok"] is True
    assert result.get("need_auth") is not True


def test_delete_file_tool_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_drive")
    fn = mod.feishu_drive_delete_file
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Create documents: docx + wiki nodes + list spaces + append content ────────


@pytest.mark.asyncio
async def test_create_docx_builds_request_and_parses_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"document_id": "doccnXXXX", "title": "T", "revision_id": 1})
    # Feishu wraps the created doc under data.document
    cap._data = {"document": {"document_id": "doccnXXXX", "title": "T", "revision_id": 1}}
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_docx_impl("  My Doc  ", "fld123")
    assert result["ok"] is True
    assert result["document_id"] == "doccnXXXX"
    assert result["url"].endswith("/docx/doccnXXXX")
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/docx/v1/documents"
    assert req.body == {"title": "My Doc", "folder_token": "fld123"}


@pytest.mark.asyncio
async def test_create_docx_omits_empty_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"document": {"document_id": "d1"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.create_docx_impl("Title", "")
    assert cap.request.body == {"title": "Title"}


@pytest.mark.asyncio
async def test_create_wiki_node_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"node": {"node_token": "nodeAAA", "obj_token": "docxBBB", "obj_type": "docx", "space_id": "sp1"}}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_wiki_node_impl("sp1", "Onboarding", "docx", "parentTok")
    assert result["ok"] is True
    assert result["node_token"] == "nodeAAA"
    assert result["obj_token"] == "docxBBB"  # == the docx document_id for writing the body
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/wiki/v2/spaces/:space_id/nodes"
    assert req.paths["space_id"] == "sp1"
    assert req.body == {
        "obj_type": "docx",
        "node_type": "origin",
        "parent_node_token": "parentTok",
        "title": "Onboarding",
    }


@pytest.mark.asyncio
async def test_create_wiki_node_upgrades_deprecated_doc_type(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"node": {"node_token": "n", "obj_token": "o"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.create_wiki_node_impl("sp1", "T", "doc", "")  # 'doc' is deprecated (131010)
    assert cap.request.body["obj_type"] == "docx"


@pytest.mark.asyncio
async def test_create_wiki_node_requires_space_id() -> None:
    result = await _impl.create_wiki_node_impl("  ", "T")
    assert result["ok"] is False
    assert "space_id" in result["message"]


@pytest.mark.asyncio
async def test_create_wiki_doc_with_content_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="", identity="") -> dict[str, Any]:
        return {"ok": True, "node_token": "nodeX", "obj_token": "docX", "space_id": space_id, "title": title}

    appended: dict[str, Any] = {}

    async def _fake_append(document_id, content, user_key="", identity="") -> dict[str, Any]:
        appended["document_id"] = document_id
        appended["user_key"] = user_key
        return {"ok": True, "document_id": document_id, "added": 3}

    monkeypatch.setattr(_impl, "create_wiki_node_impl", _fake_node)
    monkeypatch.setattr(_impl, "append_doc_content_impl", _fake_append)
    result = await _impl.create_wiki_doc_with_content_impl("sp1", "T", "# H\nbody\nmore", user_key="ou_a")
    assert result["ok"] is True
    assert result["body_written"] is True
    assert result["added"] == 3
    assert result["node_token"] == "nodeX"
    # body written into the node's docx, as the same user
    assert appended["document_id"] == "docX"
    assert appended["user_key"] == "ou_a"


@pytest.mark.asyncio
async def test_create_wiki_doc_with_content_body_fails_returns_node(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="", identity="") -> dict[str, Any]:
        return {"ok": True, "node_token": "nodeX", "obj_token": "docX"}

    async def _fake_append(document_id, content, user_key="", identity="") -> dict[str, Any]:
        return {"ok": False, "message": "boom", "added": 0}

    monkeypatch.setattr(_impl, "create_wiki_node_impl", _fake_node)
    monkeypatch.setattr(_impl, "append_doc_content_impl", _fake_append)
    result = await _impl.create_wiki_doc_with_content_impl("sp1", "T", "body")
    assert result["ok"] is False
    assert result["body_written"] is False
    # the half-created node is still surfaced so nothing is silently blank
    assert result["node_token"] == "nodeX"
    assert result["obj_token"] == "docX"


@pytest.mark.asyncio
async def test_create_wiki_doc_with_content_empty_body_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="", identity="") -> dict[str, Any]:
        return {"ok": True, "node_token": "nodeX", "obj_token": "docX"}

    async def _fail_append(document_id, content, user_key="") -> dict[str, Any]:
        raise AssertionError("append must not be called for empty content")

    monkeypatch.setattr(_impl, "create_wiki_node_impl", _fake_node)
    monkeypatch.setattr(_impl, "append_doc_content_impl", _fail_append)
    result = await _impl.create_wiki_doc_with_content_impl("sp1", "T", "\n  \n")
    assert result["ok"] is True
    assert result["added"] == 0


@pytest.mark.asyncio
async def test_create_wiki_doc_with_content_node_fails_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="", identity="") -> dict[str, Any]:
        return {"ok": False, "message": "no space"}

    async def _fail_append(document_id, content, user_key="") -> dict[str, Any]:
        raise AssertionError("append must not be called when node creation fails")

    monkeypatch.setattr(_impl, "create_wiki_node_impl", _fake_node)
    monkeypatch.setattr(_impl, "append_doc_content_impl", _fail_append)
    result = await _impl.create_wiki_doc_with_content_impl("", "T", "body")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_create_wiki_space_builds_uat_request(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CapturingUatClient({"code": 0, "data": {"space": {"space_id": "spNEW", "name": "团队库"}}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.create_wiki_space_impl("团队库", "描述", "closed", "ou_a")
    req = client.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/wiki/v2/spaces"
    assert _impl.AccessTokenType.USER in req.token_types
    assert req.body == {"name": "团队库", "description": "描述", "open_sharing": "closed"}
    assert client.option.user_access_token == "uat_tok"
    assert result["ok"] is True
    assert result["space_id"] == "spNEW"


@pytest.mark.asyncio
async def test_create_wiki_space_not_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    result = await _impl.create_wiki_space_impl("团队库")
    assert result["ok"] is False
    assert result.get("need_auth") is True


@pytest.mark.asyncio
async def test_create_wiki_space_rejects_bad_open_sharing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.create_wiki_space_impl("团队库", "", "public")
    assert result["ok"] is False
    assert "open_sharing" in result["message"]


@pytest.mark.asyncio
async def test_create_wiki_space_forwards_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())
    seen: dict[str, str] = {}

    async def _capture(user_key: str = "") -> Any:
        seen["user_key"] = user_key
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _capture)
    await _impl.create_wiki_space_impl("团队库", user_key="ou_zhang")
    assert seen["user_key"] == "ou_zhang"


@pytest.mark.asyncio
async def test_invoke_empty_user_key_uses_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """_invoke with no/empty user_key must go through the tenant client, not UAT."""
    calls: dict[str, Any] = {}

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            calls["option"] = option
            raw = _FakeRaw(json.dumps({"code": 0, "data": {}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())

    async def _uat_should_not_be_called(user_key: str = "") -> Any:
        raise AssertionError("UAT path must not run for empty user_key")

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat_should_not_be_called)
    res = await _impl._invoke(object())  # no user_key
    assert res["ok"] is True
    assert calls["option"] is None  # tenant send, no user_access_token option


@pytest.mark.asyncio
async def test_invoke_prefer_user_routes_through_uat(monkeypatch: pytest.MonkeyPatch) -> None:
    """identity='user' with a cached UAT must act as the user (content owned by them)."""
    client = _CapturingUatClient({"code": 0, "data": {"ok": 1}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    res = await _impl._invoke(object(), user_key="ou_a", prefer="user", identity="user", capabilities=[])
    assert res["ok"] is True
    assert client.option.user_access_token == "uat_tok"


@pytest.mark.asyncio
async def test_invoke_prefer_tenant_uses_tenant_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """prefer='tenant' (default): tenant is tried first even when a user_key is given;
    the UAT is not touched when tenant succeeds (so no needless authorization)."""
    calls: dict[str, Any] = {}

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            calls["tenant"] = True
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"ok": 1}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())

    async def _uat_should_not_run(user_key: str = "") -> Any:
        raise AssertionError("UAT must not be resolved when tenant succeeds")

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat_should_not_run)
    res = await _impl._invoke(object(), user_key="ou_a")  # prefer defaults to tenant
    assert res["ok"] is True
    assert calls.get("tenant") is True


@pytest.mark.asyncio
async def test_invoke_tenant_permission_error_falls_back_to_uat(monkeypatch: pytest.MonkeyPatch) -> None:
    """prefer='tenant': on a permission error, transparently retry as the user."""
    tenant_body = json.dumps({"code": 99991672, "msg": "permission denied", "data": {}}).encode()
    monkeypatch.setattr(
        _impl, "_get_client", lambda: _FakeClient(_FakeResp(99991672, "permission denied", tenant_body))
    )
    uat_client = _CapturingUatClient({"code": 0, "data": {"ok": 1}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: uat_client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    res = await _impl._invoke(object(), user_key="ou_a")
    assert res["ok"] is True
    assert uat_client.option.user_access_token == "uat_tok"


@pytest.mark.asyncio
async def test_invoke_tenant_permission_error_no_user_key_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """No user_key to fall back to → surface the original tenant permission error, not need_auth."""
    body = json.dumps({"code": 99991672, "msg": "permission denied", "data": {}}).encode()
    monkeypatch.setattr(_impl, "_get_client", lambda: _FakeClient(_FakeResp(99991672, "permission denied", body)))
    res = await _impl._invoke(object())  # no user_key
    assert res["ok"] is False
    assert res["code"] == 99991672
    assert res.get("need_auth") is not True


@pytest.mark.asyncio
async def test_invoke_write_identity_bot_uses_tenant_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """identity='bot': the user's token is never touched, so the output is the bot's."""
    calls: dict[str, Any] = {}

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            calls["tenant"] = True
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"ok": 1}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())

    async def _uat_must_not_run(user_key: str = "") -> Any:
        raise AssertionError("the user's token must not be used when they chose 'bot'")

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat_must_not_run)
    res = await _impl._invoke(object(), user_key="ou_a", prefer="user", identity="bot", capabilities=[])
    assert res["ok"] is True
    assert calls.get("tenant") is True


@pytest.mark.asyncio
async def test_invoke_write_identity_user_without_token_asks_to_authorize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """identity='user' but no token: ask them to authorize.

    It must NOT quietly fall back to the bot — the user just said they want to own
    this, and bot-owned output would contradict that choice behind their back.
    """

    class _TenantMustNotRun:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raise AssertionError("must not silently produce bot-owned content")

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantMustNotRun())
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    res = await _impl._invoke(object(), user_key="ou_a", prefer="user", identity="user", capabilities=[])
    assert res["ok"] is False
    assert res.get("need_auth") is True


@pytest.mark.asyncio
async def test_invoke_write_without_choice_asks_who_owns_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A user who was never asked gets the ownership question — and nothing is sent."""
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))

    class _NothingMayBeSent:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raise AssertionError("ownership must be settled before anything is created")

    monkeypatch.setattr(_impl, "_get_client", lambda: _NothingMayBeSent())
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: _NothingMayBeSent())
    res = await _impl._invoke(object(), user_key="ou_new", prefer="user", capabilities=["docx_write"])
    assert res["ok"] is False
    assert res.get("need_identity_choice") is True
    assert res["identity_options"] == ["user", "bot"]
    assert res["would_need_capabilities"] == ["docx_write"]


@pytest.mark.asyncio
async def test_invoke_write_uses_remembered_choice_without_asking_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Asked once, remembered after: an un-updated call site still honours the choice."""
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    assert _impl.set_identity("ou_a", "bot") == ""
    calls: dict[str, Any] = {}

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            calls["tenant"] = True
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"ok": 1}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())
    # identity omitted entirely, as a legacy call site would
    res = await _impl._invoke(object(), user_key="ou_a", prefer="user", capabilities=[])
    assert res["ok"] is True
    assert calls.get("tenant") is True
    assert res.get("need_identity_choice") is not True


@pytest.mark.asyncio
async def test_invoke_write_missing_capability_names_what_to_authorize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A user who authorized docs reading is not re-asked for it, only for the gap."""
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    _impl.set_identity("ou_a", "user")
    _impl._record_granted_capabilities("ou_a", ["docs_read"])

    class _NothingMayBeSent:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raise AssertionError("must not send without the required permission")

    monkeypatch.setattr(_impl, "_get_client", lambda: _NothingMayBeSent())
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: _NothingMayBeSent())
    res = await _impl._invoke(object(), user_key="ou_a", prefer="user", capabilities=["docs_read", "bitable_write"])
    assert res["ok"] is False
    assert res.get("need_auth") is True
    assert res["need_capabilities"] == ["bitable_write"]


@pytest.mark.asyncio
async def test_invoke_write_no_user_key_uses_tenant_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    """No user_key: nobody to attribute to and nobody to ask, so the bot proceeds."""
    calls: dict[str, Any] = {}

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            calls["tenant"] = True
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"ok": 1}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())
    res = await _impl._invoke(object(), prefer="user", capabilities=["docx_write"])
    assert res["ok"] is True
    assert calls.get("tenant") is True
    assert res.get("need_identity_choice") is not True


@pytest.mark.asyncio
async def test_create_wiki_node_forwards_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_wiki_node_impl must pass user_key down to _invoke."""
    seen: dict[str, Any] = {}

    async def _fake_invoke(
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        seen["user_key"] = user_key
        return {"ok": True, "code": 0, "msg": "", "data": {"node": {"node_token": "n", "obj_token": "o"}}}

    monkeypatch.setattr(_impl, "_invoke", _fake_invoke)
    await _impl.create_wiki_node_impl("sp1", "T", "docx", "", "ou_zhang")
    assert seen["user_key"] == "ou_zhang"


@pytest.mark.asyncio
async def test_list_wiki_spaces_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [{"space_id": "sp1", "name": "KB One", "space_type": "team"}],
            "page_token": "pt2",
            "has_more": True,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_wiki_spaces_impl(80, "pt1")  # 80 clamped to 50
    assert result["ok"] is True
    assert result["spaces"] == [{"space_id": "sp1", "name": "KB One", "space_type": "team"}]
    assert result["has_more"] is True
    q = _qdict(cap.request)
    assert q.get("page_size") == "50"
    assert q.get("page_token") == "pt1"
    assert cap.request.uri == "/open-apis/wiki/v2/spaces"


@pytest.mark.asyncio
async def test_list_wiki_spaces_empty_tenant_falls_back_to_uat(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bot usually isn't a wiki member → tenant returns an empty list. With a
    user_key + cached UAT, transparently retry as the user and return their spaces."""

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"items": []}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())
    client = _CapturingUatClient({"code": 0, "data": {"items": [{"space_id": "sp1", "name": "我的库"}]}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.list_wiki_spaces_impl(20, "", "ou_a")
    assert result["ok"] is True
    assert result["spaces"][0]["space_id"] == "sp1"
    assert client.option.user_access_token == "uat_tok"


@pytest.mark.asyncio
async def test_list_wiki_spaces_nonempty_tenant_no_uat_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the bot's tenant token already sees spaces, don't touch the UAT."""

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"items": [{"space_id": "spT", "name": "bot库"}]}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())

    async def _uat_should_not_run(user_key: str = "") -> Any:
        raise AssertionError("UAT must not run when tenant already returns spaces")

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat_should_not_run)
    result = await _impl.list_wiki_spaces_impl(20, "", "ou_a")
    assert result["ok"] is True
    assert result["spaces"][0]["space_id"] == "spT"


@pytest.mark.asyncio
async def test_get_wiki_node_forwards_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_invoke(
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        seen["user_key"] = user_key
        return {"ok": True, "code": 0, "msg": "", "data": {"node": {"obj_token": "o", "obj_type": "docx"}}}

    monkeypatch.setattr(_impl, "_invoke", _fake_invoke)
    await _impl.get_wiki_node_impl("nodeTok", "ou_zhang")
    assert seen["user_key"] == "ou_zhang"


@pytest.mark.asyncio
async def test_list_wiki_nodes_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [
                {"node_token": "n1", "obj_token": "o1", "obj_type": "docx", "title": "入职手册", "has_child": True}
            ],
            "page_token": "pt2",
            "has_more": True,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_wiki_nodes_impl("sp1", 80, "pt1", "parentTok")  # 80 clamped to 50
    assert result["ok"] is True
    assert result["nodes"][0] == {
        "node_token": "n1",
        "obj_token": "o1",
        "obj_type": "docx",
        "title": "入职手册",
        "has_child": True,
    }
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/wiki/v2/spaces/:space_id/nodes"
    assert req.paths["space_id"] == "sp1"
    q = _qdict(req)
    assert q.get("page_size") == "50"
    assert q.get("page_token") == "pt1"
    assert q.get("parent_node_token") == "parentTok"


@pytest.mark.asyncio
async def test_list_wiki_nodes_requires_space_id() -> None:
    result = await _impl.list_wiki_nodes_impl("  ")
    assert result["ok"] is False
    assert "space_id" in result["message"]


def test_content_to_blocks_maps_headings_and_paragraphs() -> None:
    content = "# Title\n\nA paragraph.\n## Sub\nAnother line.\n"
    blocks = _impl._content_to_blocks(content)
    # blank line skipped → 4 blocks
    assert [b["block_type"] for b in blocks] == [3, 2, 4, 2]
    assert blocks[0]["heading1"]["elements"][0]["text_run"]["content"] == "Title"
    assert blocks[1]["text"]["elements"][0]["text_run"]["content"] == "A paragraph."
    assert blocks[2]["heading2"]["elements"][0]["text_run"]["content"] == "Sub"


def test_content_to_blocks_hash_without_space_is_paragraph() -> None:
    # "#tag" (no space) is not a heading — stays a plain paragraph
    blocks = _impl._content_to_blocks("#notaheading")
    assert blocks[0]["block_type"] == 2
    assert blocks[0]["text"]["elements"][0]["text_run"]["content"] == "#notaheading"


@pytest.mark.asyncio
async def test_append_doc_content_builds_root_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_content_impl("doc1", "# H\nbody")
    assert result["ok"] is True
    assert result["added"] == 2
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    # root block: document_id doubles as block_id
    assert req.paths["document_id"] == "doc1"
    assert req.paths["block_id"] == "doc1"
    assert len(req.body["children"]) == 2


@pytest.mark.asyncio
async def test_append_doc_content_batches_over_50(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def fake_invoke(
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        calls.append(len(request.body["children"]))
        return {"ok": True, "code": 0, "msg": "", "data": {}}

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    content = "\n".join(f"line {i}" for i in range(120))
    result = await _impl.append_doc_content_impl("doc1", content)
    assert result["ok"] is True
    assert result["added"] == 120
    assert calls == [50, 50, 20]  # batched at the API's 50-child cap


@pytest.mark.asyncio
async def test_append_doc_content_empty_errors() -> None:
    result = await _impl.append_doc_content_impl("doc1", "\n\n  \n")
    assert result["ok"] is False
    assert "empty" in result["message"]


@pytest.mark.asyncio
async def test_append_doc_content_requires_document_id() -> None:
    result = await _impl.append_doc_content_impl("  ", "body")
    assert result["ok"] is False


# ── Tables + flowchart/swimlane (rendered as tables) ──────────────────────────


def test_table_descendants_shape() -> None:
    table_id, desc = _impl._table_descendants([["A", "B"], ["1", "2"]], header_row=True)
    # 1 table + 4 cells + 4 text blocks = 9 descendants
    assert len(desc) == 9
    table = desc[0]
    assert table["block_id"] == table_id
    assert table["block_type"] == 31
    assert table["table"]["property"]["row_size"] == 2
    assert table["table"]["property"]["column_size"] == 2
    assert table["table"]["property"]["header_row"] is True
    # cells list references exactly the 4 cell block_ids, in order
    assert table["table"]["cells"] == [d["block_id"] for d in desc[1:5]]
    # each cell (block_type 32) points at its own text block
    for cell in desc[1:5]:
        assert cell["block_type"] == 32
        assert len(cell["children"]) == 1
    # header row text runs are bold
    assert desc[5]["text"]["elements"][0]["text_run"]["text_style"]["bold"] is True


def test_table_descendants_pads_ragged_rows() -> None:
    _tid, desc = _impl._table_descendants([["a", "b", "c"], ["x"]], header_row=False)
    table = desc[0]
    assert table["table"]["property"]["column_size"] == 3
    # 6 cells for a 2x3 grid; the short row's missing cells are empty strings
    cells = [d for d in desc if d["block_type"] == 32]
    assert len(cells) == 6


@pytest.mark.asyncio
async def test_append_doc_table_builds_descendant_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_table_impl("doc1", '[["名","部门"],["张三","研发"]]', True, "[120,200]")
    assert result["ok"] is True
    assert result["rows"] == 2
    assert result["columns"] == 2
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/descendant"
    assert req.paths["document_id"] == "doc1"
    # the table block_id is the sole top-level child at the insert point
    assert len(req.body["children_id"]) == 1
    assert req.body["children_id"][0] == req.body["descendants"][0]["block_id"]
    assert req.body["descendants"][0]["table"]["property"]["column_width"] == [120, 200]


@pytest.mark.asyncio
async def test_append_doc_table_rejects_bad_json() -> None:
    result = await _impl.append_doc_table_impl("doc1", "not json")
    assert result["ok"] is False
    assert "2-D array" in result["message"]


@pytest.mark.asyncio
async def test_append_doc_table_rejects_non_list_rows() -> None:
    result = await _impl.append_doc_table_impl("doc1", '{"a":1}')
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_append_doc_flowchart_interleaves_arrows(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_flowchart_impl("doc1", '["开始","审批","结束"]', "请假流程")
    assert result["ok"] is True
    desc = cap.request.body["descendants"]
    texts = [d["text"]["elements"][0]["text_run"]["content"] for d in desc if d["block_type"] == 2]
    # title + 3 steps + 2 arrows between them
    assert texts == ["请假流程", "开始", "↓", "审批", "↓", "结束"]


@pytest.mark.asyncio
async def test_append_doc_flowchart_rejects_empty() -> None:
    result = await _impl.append_doc_flowchart_impl("doc1", "[]")
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_append_doc_swimlane_from_object(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_swimlane_impl("doc1", '{"客户":["下单","付款"],"仓库":["发货"]}')
    assert result["ok"] is True
    # 2 lanes → 2 columns; header row + 2 stage rows (deepest lane has 2)
    assert result["columns"] == 2
    assert result["rows"] == 3
    desc = cap.request.body["descendants"]
    header_texts = [
        desc[i]["text"]["elements"][0]["text_run"]["content"] for i, d in enumerate(desc) if d["block_type"] == 2
    ][:2]
    assert header_texts == ["客户", "仓库"]


@pytest.mark.asyncio
async def test_append_doc_swimlane_from_array_with_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_swimlane_impl("doc1", '["客户","客服","仓库"]', '[["下单","接单","发货"]]')
    assert result["ok"] is True
    assert result["columns"] == 3
    assert result["rows"] == 2  # header + 1 body row


@pytest.mark.asyncio
async def test_append_doc_swimlane_rejects_bad_json() -> None:
    result = await _impl.append_doc_swimlane_impl("doc1", "42")
    assert result["ok"] is False


def test_create_tools_are_async_with_docstrings() -> None:
    doc_mod = importlib.import_module("feishu_doc")
    wiki_mod = importlib.import_module("feishu_wiki")
    for fn in (
        doc_mod.feishu_doc_create,
        doc_mod.feishu_doc_append_content,
        doc_mod.feishu_doc_append_table,
        doc_mod.feishu_doc_append_flowchart,
        doc_mod.feishu_doc_append_swimlane,
        wiki_mod.feishu_wiki_list_spaces,
        wiki_mod.feishu_wiki_create_doc,
    ):
        assert inspect.iscoroutinefunction(fn)
        assert (inspect.getdoc(fn) or "").strip()


@pytest.mark.asyncio
async def test_wiki_create_doc_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"node": {"node_token": "n1", "obj_token": "d1", "obj_type": "docx"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    wiki_mod = importlib.import_module("feishu_wiki")
    out = await wiki_mod.feishu_wiki_create_doc("sp1", "Doc")
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["obj_token"] == "d1"


# ── Drive permission impl tests (公开 / 差异化访问) ─────────────────────────────


@pytest.mark.asyncio
async def test_add_permission_member_builds_post(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"member": {"member_id": "od_dept"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.add_permission_member_impl(
        "tok", "docx", "od_dept", perm="view", member_type="opendepartmentid", member_kind="department"
    )
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/permissions/:token/members")
    assert req.paths["token"] == "tok"
    assert _qdict(req).get("type") == "docx"
    assert req.body["member_id"] == "od_dept"
    assert req.body["perm"] == "view"
    assert req.body["type"] == "department"


@pytest.mark.asyncio
async def test_add_permission_member_rejects_bad_perm() -> None:
    result = await _impl.add_permission_member_impl("tok", "docx", "u1", perm="admin")
    assert result["ok"] is False
    assert "perm must be" in result["message"]


@pytest.mark.asyncio
async def test_add_permission_member_rejects_bad_member_type() -> None:
    result = await _impl.add_permission_member_impl("tok", "docx", "u1", member_type="badtype")
    assert result["ok"] is False
    assert "member_type must be" in result["message"]


@pytest.mark.asyncio
async def test_list_permission_members_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"member_id": "u1", "member_type": "openid", "perm": "view", "type": "user"}]})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_permission_members_impl("tok", "docx")
    assert result["ok"] is True
    assert result["member_total"] == 1
    assert result["members"][0]["member_id"] == "u1"
    assert cap.request.http_method.name == "GET"


@pytest.mark.asyncio
async def test_delete_permission_member_builds_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.delete_permission_member_impl("tok", "docx", "u1")
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "DELETE"
    assert req.paths["member_id"] == "u1"
    assert _qdict(req).get("member_type") == "openid"


def test_permission_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_permission")
    for name in ("feishu_permission_add_member", "feishu_permission_list_members", "feishu_permission_remove_member"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


# ── Bitable role impl tests (一份资料按角色显示不同内容) ────────────────────────


@pytest.mark.asyncio
async def test_create_bitable_role_builds_post(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"role": {"role_id": "r1", "role_name": "读者"}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.create_bitable_role_impl("app1", "读者", '[{"table_id": "tbl1", "table_perm": 1}]')
    assert result["ok"] is True
    assert result["role_id"] == "r1"
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.paths["app_token"] == "app1"
    assert req.body["role_name"] == "读者"
    assert req.body["table_roles"][0]["table_id"] == "tbl1"


@pytest.mark.asyncio
async def test_create_bitable_role_rejects_bad_json() -> None:
    result = await _impl.create_bitable_role_impl("app1", "读者", "{not json")
    assert result["ok"] is False
    assert "not valid JSON" in result["message"]


@pytest.mark.asyncio
async def test_create_bitable_role_requires_array() -> None:
    result = await _impl.create_bitable_role_impl("app1", "读者", '{"table_id": "tbl1"}')
    assert result["ok"] is False
    assert "JSON array" in result["message"]


@pytest.mark.asyncio
async def test_list_bitable_roles_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"role_id": "r1", "role_name": "读者", "table_roles": []}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_bitable_roles_impl("app1")
    assert result["ok"] is True
    assert result["roles"][0]["role_id"] == "r1"


@pytest.mark.asyncio
async def test_add_bitable_role_member_builds_post(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.add_bitable_role_member_impl("app1", "r1", "u1")
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.paths["role_id"] == "r1"
    assert req.body["member_id"] == "u1"
    assert _qdict(req).get("member_id_type") == "open_id"


def test_bitable_role_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_bitable")
    for name in ("feishu_bitable_create_role", "feishu_bitable_list_roles", "feishu_bitable_add_role_member"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


# ── eLearning impl tests (查每人学习记录) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_list_course_registrations_builds_query(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [{"user_id": "u1", "status": "completed"}], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_course_registrations_impl(user_ids="u1,u2", page_size=50)
    assert result["ok"] is True
    assert result["registrations"][0]["status"] == "completed"
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/elearning/v2/course_registrations")
    # user_ids repeated as multiple query params
    user_id_vals = [v for (k, v) in req.queries if k == "user_ids"]
    assert user_id_vals == ["u1", "u2"]
    assert _qdict(req).get("page_size") == "50"


@pytest.mark.asyncio
async def test_list_course_registrations_no_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [], "has_more": False})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_course_registrations_impl()
    assert result["ok"] is True
    assert [v for (k, v) in cap.request.queries if k == "user_ids"] == []


def test_elearning_tool_is_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_elearning")
    fn = mod.feishu_elearning_list_registrations
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Drive media upload impl tests (视频证据上传) ──────────────────────────────


@pytest.mark.asyncio
async def test_upload_media_builds_multipart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "proof.mp4"
    f.write_bytes(b"video-bytes")
    cap = _CapturedInvoke({"file_token": "media1"})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.upload_media_impl(str(f), parent_type="explorer", parent_node="fldrtok")
    assert result["ok"] is True
    assert result["file_token"] == "media1"
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri.endswith("/drive/v1/medias/upload_all")
    # The binary must be an io.IOBase in the BODY. Asserting on req.files instead would
    # pass while the request goes out as application/json: the SDK overwrites req.files
    # with whatever it can extract from the body, and ignores what we put there.
    sent = req.body["file"]
    assert isinstance(sent, io.IOBase)
    assert sent.name == "proof.mp4"
    assert sent.read() == b"video-bytes"
    assert req.body["parent_node"] == "fldrtok"
    assert req.body["size"] == str(len(b"video-bytes"))


@pytest.mark.asyncio
async def test_upload_media_missing_file() -> None:
    result = await _impl.upload_media_impl("/no/such/file.mp4", parent_node="fldrtok")
    assert result["ok"] is False
    assert "file not found" in result["message"]


@pytest.mark.asyncio
async def test_upload_media_requires_parent_node(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_bytes(b"hi")
    result = await _impl.upload_media_impl(str(f), parent_node="")
    assert result["ok"] is False
    assert "parent_node is required" in result["message"]


@pytest.mark.asyncio
async def test_upload_media_rejects_oversize(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "big.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(_impl, "_UPLOAD_ALL_MAX_BYTES", 0)
    result = await _impl.upload_media_impl(str(f), parent_node="fldrtok")
    assert result["ok"] is False
    assert "20MB" in result["message"]


def test_drive_upload_tool_is_async_with_docstring() -> None:
    mod = importlib.import_module("feishu_drive")
    fn = mod.feishu_drive_upload
    assert inspect.iscoroutinefunction(fn)
    assert (inspect.getdoc(fn) or "").strip()


# ── Contact batch user detail impl tests (卡点找人 / 取负责人联系方式) ──────────


@pytest.mark.asyncio
async def test_get_users_batch_builds_query(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [
                {
                    "open_id": "ou_1",
                    "user_id": "e1",
                    "name": "张三",
                    "mobile": "138",
                    "email": "z@x.com",
                    "job_title": "SRE",
                    "department_ids": ["od_1"],
                    "leader_user_id": "ou_boss",
                }
            ]
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_users_batch_impl("ou_1, ou_2", "open_id")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri.endswith("/contact/v3/users/batch")
    # user_ids is a repeated query param — inspect the raw list, not the collapsed dict.
    uid_vals = [v for k, v in req.queries if k == "user_ids"]
    assert uid_vals == ["ou_1", "ou_2"]
    assert _qdict(req).get("user_id_type") == "open_id"
    assert result["count"] == 1
    u = result["users"][0]
    assert u["mobile"] == "138"
    assert u["job_title"] == "SRE"
    assert u["leader_user_id"] == "ou_boss"


@pytest.mark.asyncio
async def test_get_users_batch_requires_ids() -> None:
    result = await _impl.get_users_batch_impl("  ,  ")
    assert result["ok"] is False
    assert "required" in result["message"]


@pytest.mark.asyncio
async def test_get_users_batch_rejects_over_50() -> None:
    ids = ",".join(f"ou_{i}" for i in range(51))
    result = await _impl.get_users_batch_impl(ids)
    assert result["ok"] is False
    assert "50" in result["message"]


# ── Contact — global user search by name (user_access_token) ──────────────────


@pytest.mark.asyncio
async def test_search_users_not_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    result = await _impl.search_users_impl("张三")
    assert result["ok"] is False
    assert result.get("need_auth") is True


@pytest.mark.asyncio
async def test_search_users_requires_query() -> None:
    result = await _impl.search_users_impl("   ")
    assert result["ok"] is False
    assert "required" in result["message"]


@pytest.mark.asyncio
async def test_search_users_rejects_bad_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.search_users_impl("张三", page_size=500)
    assert result["ok"] is False
    assert "200" in result["message"]


@pytest.mark.asyncio
async def test_search_users_builds_request_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "code": 0,
        "data": {
            "users": [
                {
                    "open_id": "ou_1",
                    "user_id": "e1",
                    "name": "张三",
                    "avatar": {"avatar_240": "https://x/240.png", "avatar_72": "https://x/72.png"},
                    "department_ids": ["od_a"],
                }
            ],
            "has_more": True,
            "page_token": "pt2",
        },
    }
    client = _CapturingUatClient(body)
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.search_users_impl("张三", 10, "pt1", "ou_me")
    req = client.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/search/v1/user"
    assert _impl.AccessTokenType.USER in req.token_types
    q = _qdict(req)
    assert q.get("query") == "张三"
    assert q.get("page_size") == "10"
    assert q.get("page_token") == "pt1"
    assert client.option.user_access_token == "uat_tok"
    assert result["users"][0] == {
        "open_id": "ou_1",
        "user_id": "e1",
        "name": "张三",
        "avatar": "https://x/240.png",
        "department_ids": ["od_a"],
    }
    assert result["count"] == 1
    assert result["has_more"] is True
    assert result["page_token"] == "pt2"


@pytest.mark.asyncio
async def test_search_users_forwards_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_users_impl must resolve the UAT for the passed user_key."""
    seen: dict[str, str] = {}

    async def _uat(user_key: str = "") -> Any:
        seen["user_key"] = user_key
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_uat_client", lambda: _CapturingUatClient({"code": 0, "data": {"users": []}}))
    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    await _impl.search_users_impl("张三", 20, "", "ou_zhang")
    assert seen["user_key"] == "ou_zhang"


@pytest.mark.asyncio
async def test_search_users_api_error_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _CapturingUatClient({"code": 99991663, "msg": "permission denied", "data": {}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.search_users_impl("x")
    assert result["ok"] is False
    assert result["code"] == 99991663


@pytest.mark.asyncio
async def test_contact_search_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_contact")

    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "users": [{"open_id": "ou_1", "name": "张三"}], "count": 1}

    monkeypatch.setattr(_impl, "search_users_impl", _fake)
    out = await mod.feishu_contact_search(query="张三")
    assert inspect.iscoroutinefunction(mod.feishu_contact_search)
    assert json.loads(out)["users"][0]["open_id"] == "ou_1"


def test_contact_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_contact")
    for name in ("feishu_department_members", "feishu_user_get", "feishu_contact_search"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


@pytest.mark.asyncio
async def test_subscribe_approval_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.subscribe_approval_impl("appr_1")
    assert result == {"ok": True, "approval_code": "appr_1", "subscribed": True}
    req = cap.request
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/approval/v4/approvals/:approval_code/subscribe"
    assert req.paths["approval_code"] == "appr_1"


@pytest.mark.asyncio
async def test_unsubscribe_approval_builds_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.unsubscribe_approval_impl("appr_1")
    assert result == {"ok": True, "approval_code": "appr_1", "subscribed": False}
    assert cap.request.uri == "/open-apis/approval/v4/approvals/:approval_code/unsubscribe"


@pytest.mark.asyncio
async def test_subscribe_approval_requires_code() -> None:
    result = await _impl.subscribe_approval_impl("")
    assert result["ok"] is False
    assert "approval_code" in result["message"]


@pytest.mark.asyncio
async def test_subscribe_approval_propagates_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        return {"ok": False, "code": 99991672, "msg": "no permission", "message": "err"}

    monkeypatch.setattr(_impl, "_invoke", _fail)
    result = await _impl.subscribe_approval_impl("appr_1")
    assert result["ok"] is False


def test_approval_subscribe_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_approval")
    for name in ("feishu_approval_subscribe", "feishu_approval_unsubscribe"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


# ── Rate limiting (HTTP 429) ───────────────────────────────────────────────────


def test_empty_429_body_reports_the_rate_limit_not_none() -> None:
    """A throttled request must say so.

    Feishu answers 429 with an EMPTY body and no JSON content-type, so the SDK leaves
    ``code`` as None and there is nothing to parse. Without the HTTP-status fallback
    every rate limit read "Feishu API error None: " — which is how a plain 429 got
    misdiagnosed as a document lock and as a broken upload API.
    """
    res = _impl._resp_to_result(_FakeResp(None, "", b"", status_code=429))
    assert res["ok"] is False
    assert res["http_status"] == 429
    assert "频率限制" in res["msg"]
    assert "None" not in res["message"]


def test_empty_gateway_error_body_still_names_the_status() -> None:
    res = _impl._resp_to_result(_FakeResp(None, "", b"", status_code=502))
    assert res["http_status"] == 502
    assert "502" in res["message"]


def test_json_error_body_keeps_the_feishu_code() -> None:
    """The status fallback must not shadow a real Feishu error code."""
    body = json.dumps({"code": 1770032, "msg": "forBidden", "data": {}}).encode()
    res = _impl._resp_to_result(_FakeResp(1770032, "forBidden", body, status_code=403))
    assert res["code"] == 1770032
    assert "http_status" not in res
    assert _impl._is_permission_error(res) is True


def test_rate_limit_is_not_mistaken_for_a_permission_error() -> None:
    """Otherwise a 429 would trigger the auth flow, asking the user to re-authorize
    for a problem that authorization has nothing to do with."""
    res = _impl._resp_to_result(_FakeResp(None, "", b"", status_code=429))
    assert _impl._is_rate_limited(res) is True
    assert _impl._is_permission_error(res) is False


@pytest.mark.asyncio
async def test_invoke_retries_while_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 means "too fast", not "not allowed" — the same request works moments later."""
    attempts = 0

    async def once(request: Any, user_key: Any = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return {"ok": False, "code": None, "http_status": 429, "msg": "too many"}
        return {"ok": True, "code": 0, "msg": "", "data": {}}

    slept: list[float] = []

    async def no_wait(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_impl, "_invoke_once", once)
    monkeypatch.setattr(_impl.anyio, "sleep", no_wait)
    res = await _impl._invoke(object(), user_key="ou_x", prefer="user")
    assert res["ok"] is True
    assert attempts == 3
    # Backoff grows, so a throttled batch spreads out instead of hammering.
    assert len(slept) == 2
    assert slept[1] > slept[0]


@pytest.mark.asyncio
async def test_invoke_gives_up_with_a_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries are bounded: a persistent limit is reported, never hung on."""
    attempts = 0

    async def always_limited(request: Any, user_key: Any = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {"ok": False, "code": None, "http_status": 429, "msg": "触发飞书接口频率限制"}

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(_impl, "_invoke_once", always_limited)
    monkeypatch.setattr(_impl.anyio, "sleep", no_wait)
    res = await _impl._invoke(object())
    assert res["ok"] is False
    assert res["http_status"] == 429
    assert attempts == _impl._RATE_LIMIT_ATTEMPTS


@pytest.mark.asyncio
async def test_invoke_does_not_retry_other_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only rate limits are worth repeating; a permission denial would just be denied again."""
    attempts = 0

    async def denied(request: Any, user_key: Any = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {"ok": False, "code": 1770032, "msg": "forBidden"}

    monkeypatch.setattr(_impl, "_invoke_once", denied)
    res = await _impl._invoke(object())
    assert res["ok"] is False
    assert attempts == 1


@pytest.mark.asyncio
async def test_wiki_read_user_retry_also_survives_a_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiki fallback sends as the user directly, so it needs the same backoff —
    otherwise a throttled retry would look like "you have no knowledge bases"."""
    attempts = 0

    async def as_user(request: Any, key: str) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"ok": False, "code": None, "http_status": 429, "msg": "too many"}
        return {"ok": True, "code": 0, "msg": "", "data": {"items": [{"space_id": "7"}]}}

    async def tenant_empty(request: Any, user_key: Any = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "code": 0, "msg": "", "data": {"items": []}}

    async def no_wait(seconds: float) -> None:
        return None

    monkeypatch.setattr(_impl, "_invoke", tenant_empty)
    monkeypatch.setattr(_impl, "_send_as_user", as_user)
    monkeypatch.setattr(_impl.anyio, "sleep", no_wait)
    res = await _impl._invoke_wiki_read(object(), "ou_x", lambda r: not r["data"]["items"])
    assert res["data"]["items"] == [{"space_id": "7"}]
    assert attempts == 2


@pytest.mark.asyncio
async def test_wiki_read_keeps_tenant_result_when_the_user_has_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing UAT (``None``) is not a rate limit: don't retry, don't crash."""

    async def no_token(request: Any, key: str) -> None:
        return None

    async def tenant_empty(request: Any, user_key: Any = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "code": 0, "msg": "", "data": {"items": []}}

    monkeypatch.setattr(_impl, "_invoke", tenant_empty)
    monkeypatch.setattr(_impl, "_send_as_user", no_token)
    res = await _impl._invoke_wiki_read(object(), "ou_x", lambda r: not r["data"]["items"])
    assert res["ok"] is True
    assert res["data"]["items"] == []


def test_backoff_is_bounded_and_jittered() -> None:
    """Jitter matters: without it a batch throttled together retries in lockstep and
    throttles itself again."""
    limited = {"ok": False, "http_status": 429}
    first = {_impl._retry_after_seconds(limited, 1) for _ in range(20)}
    assert len(first) > 1, "backoff must be jittered, not a fixed delay"
    assert min(first) >= _impl._RATE_LIMIT_BACKOFF
    # Growth is capped so the last attempts stay responsive instead of doubling forever.
    cap = _impl._RATE_LIMIT_MAX_WAIT * 1.25
    assert max(_impl._retry_after_seconds(limited, 12) for _ in range(20)) <= cap
    assert _impl._retry_after_seconds({"retry_after": 999}, 1) == _impl._RATE_LIMIT_MAX_WAIT


# ── Capability catalog & per-user grant memory ────────────────────────────────


def test_capability_catalog_holds_only_real_scopes() -> None:
    """Every catalog entry must look like a Feishu scope, not an invented name.

    A scope Feishu doesn't know fails the authorize page outright (20043), so this is
    the guard against a plausible-sounding typo reaching users.
    """
    assert _impl.scope_catalog_keys()  # non-empty
    for key, scopes in _impl._SCOPE_CATALOG.items():
        assert isinstance(scopes, tuple) and scopes, key
        for scope in scopes:
            assert ":" in scope, (key, scope)
            assert not scope.startswith(":") and not scope.endswith(":"), (key, scope)
            assert " " not in scope, (key, scope)


def test_parse_capabilities_accepts_keys_and_refuses_raw_scopes() -> None:
    keys, err = _impl._parse_capabilities("docx_write, wiki_write")
    assert err == ""
    assert keys == ["docx_write", "wiki_write"]
    # empty -> documented default set
    assert _impl._parse_capabilities("")[0] == list(_impl._DEFAULT_CAPABILITIES)
    # duplicates collapse rather than listing a permission twice on the consent screen
    assert _impl._parse_capabilities("docx_write docx_write")[0] == ["docx_write"]
    # a raw scope string is NOT a capability key
    for bad in ("docx:document", "offline_access", "made_up_key"):
        keys, err = _impl._parse_capabilities(bad)
        assert keys == []
        assert "未知的权限能力键" in err


def test_scope_string_dedupes_and_always_allows_refresh() -> None:
    scope = _impl._scope_string(["contact_read", "contact_phone_email_read"])
    parts = scope.split()
    assert len(parts) == len(set(parts)), "a shared scope must not be listed twice"
    assert parts[-1] == _impl._OFFLINE_SCOPE, "without offline_access every expiry re-prompts"
    assert "contact:contact.base:readonly" in parts


def test_granted_capabilities_are_remembered_and_only_grow(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracked in our own file, not read back from the token.

    A refresh response need not echo ``scope``; trusting the token would make granted
    permissions look revoked and re-prompt the user for what already works.
    """
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    assert _impl.granted_capabilities("ou_a") == []
    _impl._record_granted_capabilities("ou_a", ["docx_write"])
    assert _impl.granted_capabilities("ou_a") == ["docx_write"]
    # a later, unrelated grant must not drop the earlier one
    _impl._record_granted_capabilities("ou_a", ["bitable_write"])
    assert set(_impl.granted_capabilities("ou_a")) == {"docx_write", "bitable_write"}
    # keys are per user
    assert _impl.granted_capabilities("ou_b") == []
    # junk is not persisted as if it were a capability
    _impl._record_granted_capabilities("ou_a", ["not_a_capability"])
    assert "not_a_capability" not in _impl.granted_capabilities("ou_a")


def test_missing_capabilities_reports_only_the_gap(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    _impl._record_granted_capabilities("ou_a", ["docs_read"])
    assert _impl.missing_capabilities("ou_a", ["docs_read"]) == []
    assert _impl.missing_capabilities("ou_a", ["docs_read", "wiki_write"]) == ["wiki_write"]


def test_corrupt_store_reads_as_empty_instead_of_breaking(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged file must degrade to "ask again", never to a crash on every write."""
    path = tmp_path / "granted.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(path))
    assert _impl.granted_capabilities("ou_a") == []
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(path))
    assert _impl.get_identity("ou_a") == ""


# ── Write-ownership identity ──────────────────────────────────────────────────


def test_identity_is_remembered_per_user(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    assert _impl.get_identity("ou_a") == "", "never asked -> no assumed answer"
    assert _impl.set_identity("ou_a", "user") == ""
    assert _impl.get_identity("ou_a") == "user"
    # one person's answer is not another's
    assert _impl.get_identity("ou_b") == ""
    # changeable
    assert _impl.set_identity("ou_a", "bot") == ""
    assert _impl.get_identity("ou_a") == "bot"


def test_identity_rejects_anything_but_user_or_bot(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    for bad in ("", "nobody", "tenant", "USERR"):
        assert _impl.set_identity("ou_a", bad) != ""
    assert _impl.get_identity("ou_a") == "", "a rejected value must not be stored"
    # case/space tolerance for a real answer
    assert _impl.set_identity("ou_a", " User ") == ""
    assert _impl.get_identity("ou_a") == "user"


@pytest.mark.asyncio
async def test_identity_tools_report_choice_and_capabilities(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    auth_mod = importlib.import_module("feishu_auth")

    before = json.loads(await auth_mod.feishu_identity_get("ou_a"))
    assert before["ok"] is True
    assert before["identity"] == ""
    assert before["asked"] is False
    assert "docx_write" in before["capability_keys"]

    setres = json.loads(await auth_mod.feishu_identity_set("ou_a", "bot"))
    assert setres["ok"] is True
    assert setres["identity"] == "bot"

    after = json.loads(await auth_mod.feishu_identity_get("ou_a"))
    assert after["identity"] == "bot"
    assert after["asked"] is True

    bad = json.loads(await auth_mod.feishu_identity_set("ou_a", "whatever"))
    assert bad["ok"] is False
    assert bad["identity_options"] == ["user", "bot"]


# ── Capability inference from the API path ────────────────────────────────────


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("/open-apis/docx/v1/documents", ["docx_write"]),
        ("/open-apis/docx/v1/documents/doc1/blocks/b1/children", ["docx_write"]),
        ("/open-apis/wiki/v2/spaces/s1/nodes", ["wiki_write"]),
        ("/open-apis/bitable/v1/apps", ["bitable_write"]),
        ("/open-apis/task/v2/tasks", ["task_write"]),
        ("/open-apis/calendar/v4/calendars/primary", ["calendar_write"]),
        # spreadsheets and file/permission/media work are all cloud-drive writes
        ("/open-apis/sheets/v2/spreadsheets/tok/values", ["drive_write"]),
        ("/open-apis/drive/v1/permissions/tok/members", ["drive_write"]),
        ("/open-apis/drive/v1/medias/upload_all", ["drive_write"]),
        ("/open-apis/drive/v1/files/tok", ["drive_write"]),
        ("/open-apis/contact/v3/users/batch", ["contact_read"]),
        # unattributable path -> claim nothing rather than prompt for the wrong scope
        ("/open-apis/im/v1/messages", []),
        ("", []),
    ],
)
def test_capabilities_inferred_from_request_path(uri: str, expected: list[str]) -> None:
    req = _impl.BaseRequest()
    req.uri = uri
    assert _impl.capabilities_for(req) == expected


def test_capabilities_for_inspects_a_factory_without_sending() -> None:
    """Retry-safe call sites pass a factory; it must be inspected, not treated as opaque."""
    calls = {"n": 0}

    def factory() -> Any:
        calls["n"] += 1
        req = _impl.BaseRequest()
        req.uri = "/open-apis/bitable/v1/apps"
        return req

    assert _impl.capabilities_for(factory) == ["bitable_write"]
    assert calls["n"] == 1


def test_capabilities_for_survives_a_broken_factory() -> None:
    """Inference is best-effort: a factory that raises must not break the write."""

    def boom() -> Any:
        raise RuntimeError("nope")

    assert _impl.capabilities_for(boom) == []


@pytest.mark.asyncio
async def test_write_infers_capability_when_caller_names_none(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy call site that declares no capabilities still asks for the right one."""
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    _impl.set_identity("ou_a", "user")

    class _NothingMayBeSent:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raise AssertionError("must not send without the required permission")

    monkeypatch.setattr(_impl, "_get_client", lambda: _NothingMayBeSent())
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: _NothingMayBeSent())
    req = _impl.BaseRequest()
    req.uri = "/open-apis/bitable/v1/apps"
    res = await _impl._invoke(req, user_key="ou_a", prefer="user")  # no capabilities= given
    assert res["ok"] is False
    assert res["need_capabilities"] == ["bitable_write"]


@pytest.mark.asyncio
async def test_reads_never_ask_about_ownership(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading creates nothing, so a user who was never asked must not be interrupted."""
    monkeypatch.setattr(_impl, "_identity_path", lambda: str(tmp_path / "identity.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))

    class _TenantClient:
        async def arequest(self, request: Any, option: Any = None) -> Any:
            raw = _FakeRaw(json.dumps({"code": 0, "data": {"ok": 1}}).encode())
            return type("R", (), {"raw": raw, "code": 0, "msg": ""})()

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())
    res = await _impl._invoke(object(), user_key="ou_never_asked")  # prefer defaults to tenant
    assert res["ok"] is True
    assert res.get("need_identity_choice") is not True


@pytest.mark.asyncio
async def test_auth_complete_records_granted_capabilities(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """What the grant covered is decided at auth_start and must survive to the store."""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))

    started = await _impl.auth_start_impl("bitable_write", "ou_a")
    assert started["ok"] is True

    async def _fake_app_token() -> str:
        return "app_tok"

    async def _fake_post(url: str, body: Any, headers: Any = None) -> dict[str, Any]:
        return {"code": 0, "data": {"access_token": "u-tok", "expires_in": 7200, "open_id": "ou_a"}}

    monkeypatch.setattr(_impl, "_get_app_access_token", _fake_app_token)
    monkeypatch.setattr(_impl, "_post_json", _fake_post)

    class _Store:
        def __init__(self) -> None:
            self.saved: dict[str, Any] = {}

        async def set(self, key: str, uat: Any) -> None:
            self.saved[key] = uat

    monkeypatch.setattr(_impl, "_get_token_store", lambda: _Store())
    done = await _impl.auth_complete_impl("THECODE", "ou_a")
    assert done["ok"] is True
    assert done["capabilities"] == ["bitable_write"]
    assert _impl.granted_capabilities("ou_a") == ["bitable_write"]
    # the pending file is consumed, so a replayed code can't re-grant silently
    assert not (tmp_path / "pending.json").exists()


def test_write_tools_expose_identity(tmp_path: Any) -> None:
    """Every ownership-creating tool must let the caller state who owns the result."""
    expected = {
        "feishu_doc": ["feishu_doc_create", "feishu_doc_append_content", "feishu_doc_append_table"],
        "feishu_wiki": ["feishu_wiki_create_doc", "feishu_wiki_create_doc_with_content"],
        "feishu_bitable": ["feishu_bitable_create_app", "feishu_bitable_create_table"],
        "feishu_sheet": ["feishu_sheet_write", "feishu_sheet_append", "feishu_sheet_format"],
        "feishu_task": ["feishu_task_create"],
        "feishu_drive": ["feishu_drive_delete_file", "feishu_drive_upload"],
        "feishu_permission": ["feishu_permission_add_member"],
    }
    for mod_name, tools in expected.items():
        mod = importlib.import_module(mod_name)
        for tool in tools:
            params = inspect.signature(getattr(mod, tool)).parameters
            assert "identity" in params, f"{tool} cannot state who owns its output"
            assert params["identity"].default == "", f"{tool} must default to the remembered choice"


def test_read_tools_do_not_take_identity() -> None:
    """Reads own nothing, so offering an ownership knob there would only confuse."""
    for mod_name, tool in [
        ("feishu_doc", "feishu_doc_read"),
        ("feishu_sheet", "feishu_sheet_read"),
        ("feishu_wiki", "feishu_wiki_list_spaces"),
        ("feishu_bitable", "feishu_bitable_list_records"),
    ]:
        mod = importlib.import_module(mod_name)
        assert "identity" not in inspect.signature(getattr(mod, tool)).parameters, tool


# ── Block-level editing: list / update / delete ──────────────────────────────────


class _ScriptedInvoke:
    """An ``_invoke`` stand-in that records every call and replays queued responses.

    ``_CapturedInvoke`` keeps only the last request, which is no use for the delete
    flow (list children, then one delete per block) where the *order* of the calls is
    the behaviour under test.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.requests: list[Any] = []
        self.prefers: list[str] = []

    async def __call__(
        self,
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        self.requests.append(request() if callable(request) else request)
        self.prefers.append(prefer)
        if self._responses:
            return self._responses.pop(0)
        return {"ok": True, "code": 0, "msg": "", "data": {}}


def _block(block_id: str, block_type: int, text: str = "", parent_id: str = "doc1") -> dict[str, Any]:
    raw: dict[str, Any] = {"block_id": block_id, "block_type": block_type, "parent_id": parent_id}
    key = _impl._TEXTUAL_BLOCK_KEYS.get(block_type)
    if key:
        raw[key] = {"elements": [{"text_run": {"content": text}}]}
    return raw


@pytest.mark.asyncio
async def test_list_doc_blocks_builds_document_blocks_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [_block("b1", 4, "标题"), _block("b2", 2, "正文")], "page_token": ""})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_doc_blocks_impl("doc1")
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/docx/v1/documents/:document_id/blocks"
    assert req.paths["document_id"] == "doc1"
    # page_size asks for no more than the caller's remaining budget (default 200)
    assert _qdict(req).get("page_size") == "200"
    # a read authorizes as the bot first, so a doc it can see needs no user grant
    assert cap.prefer == "tenant"


@pytest.mark.asyncio
async def test_list_doc_blocks_reports_type_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [_block("b1", 4, "标题"), _block("b9", 27)], "page_token": ""})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_doc_blocks_impl("doc1")
    heading, image = result["blocks"]
    assert (heading["block_id"], heading["type_name"], heading["text"]) == ("b1", "heading2", "标题")
    assert heading["editable_text"] is True
    # an image block has no text runs, so update_block can't rewrite it
    assert (image["type_name"], image["text"], image["editable_text"]) == ("image", "", False)


@pytest.mark.asyncio
async def test_list_doc_blocks_trims_long_text(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [_block("b1", 2, "x" * 500)], "page_token": ""})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_doc_blocks_impl("doc1")
    assert result["blocks"][0]["text"] == "x" * 200 + "…"


@pytest.mark.asyncio
async def test_list_doc_blocks_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    scripted = _ScriptedInvoke(
        [
            {"ok": True, "data": {"items": [_block("b1", 2, "one")], "page_token": "pt2"}},
            {"ok": True, "data": {"items": [_block("b2", 2, "two")], "page_token": ""}},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.list_doc_blocks_impl("doc1")
    assert [b["block_id"] for b in result["blocks"]] == ["b1", "b2"]
    assert result["truncated"] is False
    assert _qdict(scripted.requests[1]).get("page_token") == "pt2"


@pytest.mark.asyncio
async def test_list_doc_blocks_marks_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": [_block("b1", 2, "a"), _block("b2", 2, "b")], "page_token": ""})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_doc_blocks_impl("doc1", max_blocks=1)
    assert result["count"] == 1
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_list_doc_blocks_requires_document_id() -> None:
    assert (await _impl.list_doc_blocks_impl("  "))["ok"] is False


@pytest.mark.asyncio
async def test_update_doc_block_patches_text_elements(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.update_doc_block_impl("doc1", "b2", "改好的正文")
    assert result["ok"] is True
    req = cap.request
    assert req.http_method.name == "PATCH"
    assert req.uri == "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    assert req.paths["block_id"] == "b2"
    els = req.body["update_text_elements"]["elements"]
    assert els == [{"text_run": {"content": "改好的正文"}}]
    # a write goes as the user when there is one, so the edit is attributable
    assert cap.prefer == "user"


@pytest.mark.asyncio
async def test_update_doc_block_rejects_root_block() -> None:
    """The document_id doubles as the root block_id, and the root holds no text."""
    result = await _impl.update_doc_block_impl("doc1", "doc1", "text")
    assert result["ok"] is False
    assert "root" in result["message"]


@pytest.mark.asyncio
async def test_update_doc_block_requires_block_and_text() -> None:
    assert (await _impl.update_doc_block_impl("doc1", "", "t"))["ok"] is False
    assert (await _impl.update_doc_block_impl("", "b1", "t"))["ok"] is False
    empty = await _impl.update_doc_block_impl("doc1", "b1", "")
    assert empty["ok"] is False
    # an empty rewrite is a delete in disguise; point at the tool that really does it
    assert "delete_blocks" in empty["message"]


@pytest.mark.asyncio
async def test_delete_doc_blocks_resolves_id_to_index(monkeypatch: pytest.MonkeyPatch) -> None:
    children = {"items": [_block("b1", 2, "a"), _block("b2", 2, "b"), _block("b3", 2, "c")]}
    scripted = _ScriptedInvoke([{"ok": True, "data": children}, {"ok": True, "data": {}}])
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.delete_doc_blocks_impl("doc1", '["b2"]')
    assert result["ok"] is True
    assert result["deleted"] == ["b2"]
    delete_req = scripted.requests[1]
    assert delete_req.http_method.name == "DELETE"
    assert delete_req.uri.endswith("/children/batch_delete")
    # b2 sits at index 1, and the range is half-open
    assert delete_req.body == {"start_index": 1, "end_index": 2}
    assert delete_req.paths["block_id"] == "doc1"


@pytest.mark.asyncio
async def test_delete_doc_blocks_deletes_highest_index_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting low-to-high would shift later siblings down and hit the wrong blocks."""
    children = {"items": [_block("b1", 2, "a"), _block("b2", 2, "b"), _block("b3", 2, "c")]}
    scripted = _ScriptedInvoke([{"ok": True, "data": children}, {"ok": True, "data": {}}, {"ok": True, "data": {}}])
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.delete_doc_blocks_impl("doc1", '["b1","b3"]')
    assert result["deleted"] == ["b3", "b1"]
    assert [r.body["start_index"] for r in scripted.requests[1:]] == [2, 0]


@pytest.mark.asyncio
async def test_delete_doc_blocks_reports_unknown_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    children = {"items": [_block("b1", 2, "a")]}
    scripted = _ScriptedInvoke([{"ok": True, "data": children}, {"ok": True, "data": {}}])
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.delete_doc_blocks_impl("doc1", '["b1","nope"]')
    assert result["ok"] is True
    assert result["deleted"] == ["b1"]
    assert result["not_found"] == ["nope"]


@pytest.mark.asyncio
async def test_delete_doc_blocks_errors_when_nothing_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """No index is ever guessed: an unlocatable id is refused, not deleted blind."""
    scripted = _ScriptedInvoke([{"ok": True, "data": {"items": [_block("b1", 2, "a")]}}])
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.delete_doc_blocks_impl("doc1", '["ghost"]')
    assert result["ok"] is False
    assert result["not_found"] == ["ghost"]
    # nothing was sent beyond the lookup
    assert len(scripted.requests) == 1


@pytest.mark.asyncio
async def test_delete_doc_blocks_uses_parent_for_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    children = {"items": [_block("c1", 2, "cell text", parent_id="cell1")]}
    scripted = _ScriptedInvoke([{"ok": True, "data": children}, {"ok": True, "data": {}}])
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.delete_doc_blocks_impl("doc1", '["c1"]', parent_block_id="cell1")
    assert result["ok"] is True
    assert result["parent_block_id"] == "cell1"
    assert all(r.paths["block_id"] == "cell1" for r in scripted.requests)


@pytest.mark.asyncio
async def test_delete_doc_blocks_refuses_root_block() -> None:
    result = await _impl.delete_doc_blocks_impl("doc1", '["doc1"]')
    assert result["ok"] is False
    assert "root" in result["message"]


@pytest.mark.asyncio
async def test_delete_doc_blocks_validates_input() -> None:
    assert (await _impl.delete_doc_blocks_impl("", '["b1"]'))["ok"] is False
    assert (await _impl.delete_doc_blocks_impl("doc1", "not json"))["ok"] is False
    assert (await _impl.delete_doc_blocks_impl("doc1", "[]"))["ok"] is False
    assert (await _impl.delete_doc_blocks_impl("doc1", '["  "]'))["ok"] is False


@pytest.mark.asyncio
async def test_delete_doc_blocks_accepts_bare_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single id is a common agent slip; accept it rather than erroring on a typo."""
    scripted = _ScriptedInvoke([{"ok": True, "data": {"items": [_block("b1", 2, "a")]}}, {"ok": True, "data": {}}])
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.delete_doc_blocks_impl("doc1", '"b1"')
    assert result["deleted"] == ["b1"]


@pytest.mark.asyncio
async def test_delete_doc_blocks_stops_and_reports_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    children = {"items": [_block("b1", 2, "a"), _block("b2", 2, "b")]}
    scripted = _ScriptedInvoke(
        [
            {"ok": True, "data": children},
            {"ok": True, "data": {}},
            {"ok": False, "message": "permission denied", "code": 99991672},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", scripted)
    result = await _impl.delete_doc_blocks_impl("doc1", '["b1","b2"]')
    assert result["ok"] is False
    # b2 (the higher index) went first, so the caller learns exactly what survived
    assert result["deleted"] == ["b2"]


def test_block_editing_tools_are_async_with_docstrings() -> None:
    doc_mod = importlib.import_module("feishu_doc")
    for fn in (doc_mod.feishu_doc_list_blocks, doc_mod.feishu_doc_update_block, doc_mod.feishu_doc_delete_blocks):
        assert inspect.iscoroutinefunction(fn)
        assert (inspect.getdoc(fn) or "").strip()


def test_block_write_tools_expose_identity_and_list_does_not() -> None:
    doc_mod = importlib.import_module("feishu_doc")
    for tool in ("feishu_doc_update_block", "feishu_doc_delete_blocks"):
        params = inspect.signature(getattr(doc_mod, tool)).parameters
        assert params["identity"].default == ""
    # listing blocks owns nothing, so it takes no ownership knob
    assert "identity" not in inspect.signature(doc_mod.feishu_doc_list_blocks).parameters


@pytest.mark.asyncio
async def test_block_editing_tools_return_json(monkeypatch: pytest.MonkeyPatch) -> None:
    doc_mod = importlib.import_module("feishu_doc")
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke({"items": [_block("b1", 2, "hi")], "page_token": ""}))
    assert json.loads(await doc_mod.feishu_doc_list_blocks("doc1"))["ok"] is True
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke({}))
    assert json.loads(await doc_mod.feishu_doc_update_block("doc1", "b1", "new"))["ok"] is True
