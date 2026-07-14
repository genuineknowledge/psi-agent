from __future__ import annotations

import importlib
import inspect
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


def test_approval_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_approval")
    for name in ("feishu_approval_list_tasks", "feishu_approval_get", "feishu_approval_decide"):
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
    async def _fake(_req: Any) -> dict[str, Any]:
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

    async def _no_uat() -> Any:
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

    async def _uat() -> Any:
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

    async def _uat() -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    result = await _impl.search_docs_impl("x", 20, 0, "")
    assert result["ok"] is False
    assert result["code"] == 99991663


@pytest.mark.asyncio
async def test_auth_start_builds_authorize_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda: str(tmp_path / "pending.json"))
    result = await _impl.auth_start_impl("")
    assert result["ok"] is True
    parsed = urlparse(result["authorize_url"])
    assert parsed.hostname == "accounts.feishu.cn"
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["cli_x"]
    assert q["response_type"] == ["code"]
    assert "offline_access" in q["scope"][0]
    # state persisted for CSRF check
    assert json.loads((tmp_path / "pending.json").read_text())["state"] == q["state"][0]


def test_extract_code_from_url_or_bare() -> None:
    assert _impl._extract_code("https://localhost/?code=ABC123&state=x") == "ABC123"
    assert _impl._extract_code("  ABC123  ") == "ABC123"


@pytest.mark.asyncio
async def test_auth_complete_exchanges_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda: str(tmp_path / "pending.json"))

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
    assert stored["uat"].access_token == "u-tok"


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


def test_bitable_tools_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_bitable")
    for name in ("feishu_bitable_list_tables", "feishu_bitable_list_records", "feishu_bitable_create_record"):
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
                    "check_in_record": {"check_time": "1752460200", "location_name": "总部"},
                    "check_in_result": "Normal",
                    "check_out_record": {"check_time": "1752490200", "location_name": "总部"},
                    "check_out_result": "Late",
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
        {"user_task_results": [{"user_id": "e1", "employee_name": "李四", "day": 20260714, "check_in_result": "Lack"}]}
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


def test_task_tools_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_task")
    for name in ("feishu_task_create", "feishu_task_list", "feishu_task_update", "feishu_task_complete"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"
