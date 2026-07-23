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

    async def __call__(self, request: Any, user_key: str | None = None) -> dict[str, Any]:
        self.request = request
        self.user_key = user_key
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

    async def __call__(self, request: Any, user_key: str | None = None) -> dict[str, Any]:
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
    async def _fake(_req: Any, user_key: str | None = None) -> dict[str, Any]:
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
    result = await _impl.auth_start_impl("")
    assert result["ok"] is True
    parsed = urlparse(result["authorize_url"])
    assert parsed.hostname == "accounts.feishu.cn"
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["cli_x"]
    assert q["response_type"] == ["code"]
    assert "offline_access" in q["scope"][0]
    # scope must be exactly the fixed default — no fabricated/invalid scope
    # (e.g. "drive:drive:drive:readonly") that Feishu rejects with error 20043
    assert q["scope"][0] == _impl._DEFAULT_SCOPES
    assert "drive:drive:drive" not in q["scope"][0]
    # state persisted for CSRF check
    assert json.loads((tmp_path / "pending.json").read_text())["state"] == q["state"][0]


@pytest.mark.asyncio
async def test_auth_start_wrapper_ignores_llm_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The feishu_auth_start tool exposes no scopes arg — LLM can't inject a bad scope."""
    auth_mod = importlib.import_module("feishu_auth")
    params = inspect.signature(auth_mod.feishu_auth_start).parameters
    assert "scopes" not in params
    assert list(params) == ["user_key"]

    captured: dict[str, Any] = {}

    async def _fake_start(scopes: str = "", user_key: str = "") -> dict[str, Any]:
        captured["scopes"] = scopes
        return {"ok": True, "authorize_url": "x"}

    monkeypatch.setattr(auth_mod._f, "auth_start_impl", _fake_start)
    await auth_mod.feishu_auth_start("ou_a")
    # wrapper always passes empty scopes -> impl uses the fixed default
    assert captured["scopes"] == ""


def test_extract_code_from_url_or_bare() -> None:
    assert _impl._extract_code("https://localhost/?code=ABC123&state=x") == "ABC123"
    assert _impl._extract_code("  ABC123  ") == "ABC123"


@pytest.mark.asyncio
async def test_auth_complete_exchanges_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))

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


def test_bitable_tools_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_bitable")
    for name in (
        "feishu_bitable_list_tables",
        "feishu_bitable_list_records",
        "feishu_bitable_create_record",
        "feishu_bitable_delete_records",
        "feishu_bitable_clear_table",
        "feishu_bitable_list_fields",
        "feishu_bitable_delete_fields",
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
    result = await _impl.delete_file_impl("doccnX", "docx", "ou_a")
    assert result["ok"] is True
    assert client.option.user_access_token == "uat_tok"
    assert client.request.http_method.name == "DELETE"


@pytest.mark.asyncio
async def test_delete_file_user_key_not_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    result = await _impl.delete_file_impl("doccnX", "docx", "ou_a")
    assert result["ok"] is False
    assert result.get("need_auth") is True


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
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="") -> dict[str, Any]:
        return {"ok": True, "node_token": "nodeX", "obj_token": "docX", "space_id": space_id, "title": title}

    appended: dict[str, Any] = {}

    async def _fake_append(document_id, content, user_key="") -> dict[str, Any]:
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
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="") -> dict[str, Any]:
        return {"ok": True, "node_token": "nodeX", "obj_token": "docX"}

    async def _fake_append(document_id, content, user_key="") -> dict[str, Any]:
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
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="") -> dict[str, Any]:
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
    async def _fake_node(space_id, title, obj_type="docx", parent="", user_key="") -> dict[str, Any]:
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
async def test_invoke_user_key_routes_through_uat(monkeypatch: pytest.MonkeyPatch) -> None:
    """_invoke with a user_key must attach the user's UAT to the request."""
    client = _CapturingUatClient({"code": 0, "data": {"ok": 1}})
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: client)

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    res = await _impl._invoke(object(), user_key="ou_a")
    assert res["ok"] is True
    assert client.option.user_access_token == "uat_tok"


@pytest.mark.asyncio
async def test_invoke_user_key_not_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: object())

    async def _no_uat(user_key: str = "") -> Any:
        return None

    monkeypatch.setattr(_impl, "_get_valid_uat", _no_uat)
    res = await _impl._invoke(object(), user_key="ou_a")
    assert res["ok"] is False
    assert res.get("need_auth") is True


@pytest.mark.asyncio
async def test_create_wiki_node_forwards_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_wiki_node_impl must pass user_key down to _invoke."""
    seen: dict[str, Any] = {}

    async def _fake_invoke(request: Any, user_key: str | None = None) -> dict[str, Any]:
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
async def test_list_wiki_spaces_user_key_routes_through_uat(monkeypatch: pytest.MonkeyPatch) -> None:
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
async def test_get_wiki_node_forwards_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_invoke(request: Any, user_key: str | None = None) -> dict[str, Any]:
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

    async def fake_invoke(request: Any, user_key: str | None = None) -> dict[str, Any]:
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


def test_create_tools_are_async_with_docstrings() -> None:
    doc_mod = importlib.import_module("feishu_doc")
    wiki_mod = importlib.import_module("feishu_wiki")
    for fn in (
        doc_mod.feishu_doc_create,
        doc_mod.feishu_doc_append_content,
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
    assert req.files is not None and "file" in req.files
    assert req.files["file"][0] == "proof.mp4"
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


def test_contact_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_contact")
    for name in ("feishu_department_members", "feishu_user_get"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"
