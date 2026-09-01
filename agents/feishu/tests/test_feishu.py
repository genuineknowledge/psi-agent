from __future__ import annotations

import asyncio
import importlib
import inspect
import io
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import anyio
import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl: Any = importlib.import_module("_feishu_impl")
_watch: Any = importlib.import_module("_feishu_auth_watch")
# 授权那半边的真实命名空间。``_feishu_impl`` 只是把名字再导出一遍, 所以打桩必须打在这里:
# ``_notify_auth_outcome`` 在**自己模块的 globals** 里解析 live_agent / get_session_id,
# 打在再导出上不会被它看见 (静默失效, 测试照过)。
_auth: Any = importlib.import_module("_feishu.auth")


async def _instant(value: Any) -> Any:
    """把一个现成的值包成 awaitable (给 monkeypatch 当替身用)。"""
    return value


def _record_dm(sink: list[tuple[str, str]]) -> Any:
    """替掉 send_message_impl, 把后台回告的私聊记下来。"""

    async def _send(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
        sink.append((receive_id, text))
        return {"ok": True, "message_id": "om_x"}

    return _send


async def _settle(impl: Any, user_key: str, *, seconds: float = 2.0) -> None:
    """等后台 watcher 跑完 —— 它是脱离本轮的任务, 不等就会在断言时还没写结果。

    直接 await 它自己的 task (而不是轮询状态位): 任务结束时 ``_run`` 已经把结果和回告都写
    完了, 所以这是「跑完」唯一准确的信号。
    """
    state = _watch.status(impl._norm_user_key(user_key))
    assert state is not None and state.task is not None
    with anyio.fail_after(seconds):
        await asyncio.shield(state.task)


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
        "feishu_drive_reply_comment",
        "feishu_file_download",
        "feishu_drive_upload",
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


# ── Editing an already-sent message (改内容而不撤回重发) ────────────────────────


@pytest.mark.asyncio
async def test_edit_message_builds_put_request(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.edit_message_impl("om_abc", "改好的内容", user_key="ou_sender")
    req = cap.request
    assert req.http_method.name == "PUT"
    assert req.uri == "/open-apis/im/v1/messages/:message_id"
    assert req.paths["message_id"] == "om_abc"
    assert req.body["msg_type"] == "text"
    assert json.loads(req.body["content"]) == {"text": "改好的内容"}
    # tenant first: the bot edits its own messages, the UAT is only the fallback
    assert cap.prefer == "tenant"
    assert cap.user_key == "ou_sender"
    assert result == {"ok": True, "message_id": "om_abc", "edited": True, "msg_type": "text"}


@pytest.mark.asyncio
async def test_edit_message_with_mention_becomes_post(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.edit_message_impl("om_abc", '<at user_id="ou_z"></at> 看一下')
    # A plain-text <at> renders as a raw tag, so an edit that mentions must switch to post
    assert cap.request.body["msg_type"] == "post"
    line = json.loads(cap.request.body["content"])["zh_cn"]["content"][0]
    assert line[0] == {"tag": "at", "user_id": "ou_z"}
    assert result["msg_type"] == "post"


@pytest.mark.asyncio
async def test_edit_message_rejects_non_message_id_and_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    for bad in ("", "   "):
        assert "message_id is required" in (await _impl.edit_message_impl(bad, "x"))["message"]
    for bad in ("oc_group", "ou_person"):
        assert "must be a message id" in (await _impl.edit_message_impl(bad, "x"))["message"]
    # Editing replaces the whole content; empty text is a recall, not an edit
    empty = await _impl.edit_message_impl("om_abc", "  ")
    assert empty["ok"] is False
    assert "DELETE /open-apis/im/v1/messages/:message_id" in empty["message"]
    assert cap.request is None  # all rejected before spending a request


@pytest.mark.asyncio
async def test_edit_message_hints_sender_only_and_edit_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    for code, needle in ((230071, "发送者"), (230072, "20 次"), (230075, "时限"), (230054, "撤回重发")):

        async def _fake(*a: Any, _code: int = code, **k: Any) -> dict[str, Any]:
            return {"ok": False, "code": _code, "msg": "nope", "message": "err"}

        monkeypatch.setattr(_impl, "_invoke", _fake)
        result = await _impl.edit_message_impl("om_abc", "new")
        assert needle in result["hint"], code


@pytest.mark.asyncio
async def test_edit_message_keeps_unknown_error_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": False, "code": 99999, "msg": "boom", "message": "err"}

    monkeypatch.setattr(_impl, "_invoke", _fake)
    assert "hint" not in await _impl.edit_message_impl("om_abc", "new")


@pytest.mark.asyncio
async def test_edit_card_patches_and_forces_update_multi(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    card = {"config": {"wide_screen_mode": True}, "elements": [{"tag": "markdown", "content": "已通过"}]}
    result = await _impl.edit_card_impl("om_card", json.dumps(card))
    req = cap.request
    # A card is updated with PATCH (not the text/post PUT) and takes only content
    assert req.http_method.name == "PATCH"
    assert req.uri == "/open-apis/im/v1/messages/:message_id"
    assert set(req.body) == {"content"}
    sent = json.loads(req.body["content"])
    # Without update_multi Feishu updates the card for a single viewer only
    assert sent["config"] == {"wide_screen_mode": True, "update_multi": True}
    assert sent["elements"] == card["elements"]
    assert result == {"ok": True, "message_id": "om_card", "edited": True, "msg_type": "interactive"}


@pytest.mark.asyncio
async def test_edit_card_leaves_card_2_schema_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    card = {"schema": "2.0", "body": {"elements": []}}
    await _impl.edit_card_impl("om_card", json.dumps(card))
    # Card 2.0 has no update_multi flag; adding one would be inventing a field
    assert json.loads(cap.request.body["content"]) == card


@pytest.mark.asyncio
async def test_edit_card_rejects_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    assert "not valid JSON" in (await _impl.edit_card_impl("om_c", "{oops"))["message"]
    assert "must be a JSON object" in (await _impl.edit_card_impl("om_c", "[1,2]"))["message"]
    assert cap.request is None


@pytest.mark.asyncio
async def test_edit_tools_return_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")
    captured: dict[str, Any] = {}

    async def _fake_edit(message_id: str, text: str, user_key: str = "") -> dict[str, Any]:
        captured.update(message_id=message_id, text=text, user_key=user_key)
        return {"ok": True, "message_id": message_id, "edited": True, "msg_type": "text"}

    monkeypatch.setattr(_impl, "edit_message_impl", _fake_edit)
    out = await mod.feishu_message_edit(message_id="om_1", text="fixed", user_key="ou_a")
    assert json.loads(out)["edited"] is True
    assert captured == {"message_id": "om_1", "text": "fixed", "user_key": "ou_a"}

    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke({}))
    card_out = await mod.feishu_message_edit_card(message_id="om_2", card_json='{"schema":"2.0","body":{}}')
    assert json.loads(card_out)["msg_type"] == "interactive"


# ── Emoji reactions (表情回应) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_reactions_pages_and_flattens(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "items": [
                {
                    "reaction_id": "re1",
                    "reaction_type": {"emoji_type": "THUMBSUP"},
                    "operator": {"operator_id": "ou_a", "operator_type": "user"},
                    "action_time": "1700000000000",
                },
                "not-a-dict",
            ],
            "has_more": True,
            "page_token": "pt2",
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_reactions_impl("om_abc", "赞", page_size=99, page_token="pt1")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/im/v1/messages/:message_id/reactions"
    q = _qdict(req)
    assert q["reaction_type"] == "THUMBSUP"  # the alias is normalized for filtering too
    assert q["page_size"] == "50"  # clamped to Feishu's max
    assert q["page_token"] == "pt1"
    assert result["count"] == 1
    assert result["reactions"][0] == {
        "reaction_id": "re1",
        "emoji_type": "THUMBSUP",
        "operator_id": "ou_a",
        "operator_type": "user",
        "action_time": "1700000000000",
    }
    assert result["has_more"] is True
    assert result["page_token"] == "pt2"


@pytest.mark.asyncio
async def test_list_reactions_omits_filter_when_no_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"items": []})
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.list_reactions_impl("om_abc")
    assert "reaction_type" not in _qdict(cap.request)


@pytest.mark.asyncio
async def test_remove_reaction_by_reaction_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.remove_reaction_impl("om_abc", reaction_id="re1", user_key="ou_me")
    req = cap.request
    assert req.http_method.name == "DELETE"
    assert req.uri == "/open-apis/im/v1/messages/:message_id/reactions/:reaction_id"
    assert req.paths == {"message_id": "om_abc", "reaction_id": "re1"}
    # Feishu's delete response can echo nothing; the ids we know must survive that
    assert result["removed"] is True
    assert result["reaction_id"] == "re1"
    assert result["message_id"] == "om_abc"


@pytest.mark.asyncio
async def test_remove_reaction_resolves_reaction_id_from_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    async def _fake_invoke(request: Any, **k: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        calls.append(req)
        if req.http_method.name == "GET":
            return {
                "ok": True,
                "code": 0,
                "msg": "",
                "data": {"items": [{"reaction_id": "re7", "reaction_type": {"emoji_type": "THUMBSUP"}}]},
            }
        return {"ok": True, "code": 0, "msg": "", "data": {}}

    monkeypatch.setattr(_impl, "_invoke", _fake_invoke)
    # The same argument that added a reaction removes it — no id to carry around
    result = await _impl.remove_reaction_impl("om_abc", emoji_type="赞")
    assert [c.http_method.name for c in calls] == ["GET", "DELETE"]
    assert calls[1].paths["reaction_id"] == "re7"
    assert result["removed"] is True


@pytest.mark.asyncio
async def test_remove_reaction_refuses_when_ambiguous_or_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    two = [
        {"reaction_id": "re1", "reaction_type": {"emoji_type": "THUMBSUP"}, "operator": {"operator_id": "ou_a"}},
        {"reaction_id": "re2", "reaction_type": {"emoji_type": "THUMBSUP"}, "operator": {"operator_id": "ou_b"}},
    ]

    async def _listing(items: list[Any]) -> Any:
        async def _fake(request: Any, **k: Any) -> dict[str, Any]:
            req = request() if callable(request) else request
            assert req.http_method.name == "GET", "must not delete when resolution is unclear"
            return {"ok": True, "code": 0, "msg": "", "data": {"items": items}}

        return _fake

    monkeypatch.setattr(_impl, "_invoke", await _listing(two))
    ambiguous = await _impl.remove_reaction_impl("om_abc", emoji_type="THUMBSUP")
    assert ambiguous["ok"] is False
    assert ambiguous["code"] == "reaction_ambiguous"
    assert [c["reaction_id"] for c in ambiguous["candidates"]] == ["re1", "re2"]

    monkeypatch.setattr(_impl, "_invoke", await _listing([]))
    missing = await _impl.remove_reaction_impl("om_abc", emoji_type="THUMBSUP")
    assert missing["code"] == "reaction_not_found"


@pytest.mark.asyncio
async def test_remove_reaction_needs_emoji_or_id(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.remove_reaction_impl("om_abc")
    assert result["ok"] is False
    assert "either reaction_id, or emoji_type" in result["message"]
    assert cap.request is None


@pytest.mark.asyncio
async def test_reaction_tools_return_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke({}))
    assert json.loads(await mod.feishu_message_unreact("om_1", reaction_id="re1"))["removed"] is True


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


@pytest.mark.asyncio
async def test_sheet_reads_forward_user_key_as_tenant_first_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads stay tenant-first but carry the user identity as a permission fallback."""
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
    cap = _CapturedInvoke({"valueRange": {"range": "S1!A1:A3", "values": grid}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_sheet_range_impl("sht1", "S1!A1:A3", max_chars=60)
    assert result["truncated"] is True
    assert result["row_count"] == 1


@pytest.mark.asyncio
async def test_sheet_read_small_grid_keeps_all_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """``max_chars=0`` means "the per-result cap", not "unlimited".

    A grid this small is under that cap either way, so every row survives — but the
    assertion is about the rows, not about the limit being off. Anything over the cap
    is truncated even at ``max_chars=0``, since a bigger payload is cut on the wire
    regardless (and cutting there destroys the JSON).
    """
    grid = [["x" * 40], ["y" * 40]]
    cap = _CapturedInvoke({"valueRange": {"range": "S1!A1:A2", "values": grid}})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.read_sheet_range_impl("sht1", "S1!A1:A2", max_chars=0)
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


# ── Group administration — chat details, add/remove members ───────────────────


@pytest.mark.asyncio
async def test_get_chat_builds_request_and_translates_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "name": "项目群",
            "description": "季度冲刺",
            "owner_id": "ou_owner",
            "owner_id_type": "open_id",
            "user_count": "42",
            "bot_count": "2",
            "user_manager_id_list": ["ou_admin"],
            "bot_manager_id_list": ["cli_x"],
            "chat_mode": "group",
            "chat_type": "private",
            "chat_status": "normal",
            "add_member_permission": "only_owner",
            "at_all_permission": "all_members",
            "share_card_permission": "not_allowed",
            "membership_approval": "approval_required",
            "external": False,
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.get_chat_impl("oc_x")
    req = cap.request
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/im/v1/chats/:chat_id"
    assert req.paths["chat_id"] == "oc_x"
    assert _qdict(req).get("user_id_type") == "open_id"
    assert result["owner_id"] == "ou_owner"
    assert result["owner_is_bot"] is False
    # Counts arrive as strings from Feishu; a count is only useful as a number.
    assert result["user_count"] == 42
    assert result["bot_count"] == 2
    assert result["user_manager_ids"] == ["ou_admin"]
    # Bare enums are translated so the model doesn't have to guess what only_owner means.
    assert result["settings"]["谁可以加人"] == "仅群主和管理员"
    assert result["settings"]["谁可以@所有人"] == "所有群成员"
    assert result["settings"]["是否可分享群名片"] == "不允许"
    assert result["settings"]["入群是否需审批"] == "需审批"
    assert result["partial"] is False


@pytest.mark.asyncio
async def test_get_chat_reports_partial_and_bot_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    # Feishu answers a non-member with only name/avatar/counts/status — a thin result
    # must not read as "这个群没有群主/没有设置".
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke({"name": "外部群", "user_count": "3"}))
    stub = await _impl.get_chat_impl("oc_x")
    assert stub["partial"] is True
    assert stub["settings"] == {}

    # A bot-owned group returns no owner_id at all; that is not the same as partial.
    monkeypatch.setattr(_impl, "_invoke", _CapturedInvoke({"name": "群", "chat_mode": "group"}))
    bot_owned = await _impl.get_chat_impl("oc_x")
    assert bot_owned["owner_is_bot"] is True
    assert bot_owned["partial"] is False


class _ByToken:
    """Answer differently per token, the way Feishu does for group membership."""

    def __init__(self, tenant: dict[str, Any], user: dict[str, Any]) -> None:
        self.prefers: list[str] = []
        self._by_prefer = {"tenant": tenant, "user": user}

    async def __call__(
        self,
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        self.prefers.append(prefer)
        return {"ok": True, "code": 0, "msg": "", "data": self._by_prefer.get(prefer, {})}


_STUB = {"name": "卧龙山庄飞书第二分庄", "user_count": "0"}
_FULL = {"name": "卧龙山庄飞书第二分庄", "owner_id": "ou_owner", "chat_mode": "group", "user_count": "9"}


@pytest.mark.asyncio
async def test_get_chat_retries_as_the_caller_when_the_bot_is_not_a_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observed: 「机器人不在这个群里, 看不到成员」 reported for a group the *user* is in.

    Membership is judged by whichever token asked, and the bot is not in most groups a
    person is in — so asking only as the bot turns a readable group into "unreadable".
    """
    cap = _ByToken(tenant=_STUB, user=_FULL)
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = await _impl.get_chat_impl("oc_x", user_key="ou_caller")
    assert cap.prefers == ["tenant", "user"], "the stub must be retried as the caller"
    assert res["partial"] is False
    assert res["owner_id"] == "ou_owner"
    assert res["user_count"] == 9, "the non-member's 0 must not survive into the answer"
    assert res["asked_as"] == "user"


@pytest.mark.asyncio
async def test_get_chat_without_a_user_key_cannot_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """No identity to ask as means one attempt — and the stub must say what would help."""
    cap = _ByToken(tenant=_STUB, user=_FULL)
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = await _impl.get_chat_impl("oc_x")
    assert cap.prefers == ["tenant"]
    assert res["partial"] is True
    assert "user_key" in res["to_see_more"]


@pytest.mark.asyncio
async def test_a_still_partial_result_explains_the_zero_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the caller is not in the group either, ``user_count: 0`` needs a caveat.

    Reporting it bare invites "这个群没人", which is a wrong answer about a real group.
    """
    cap = _ByToken(tenant=_STUB, user=_STUB)
    monkeypatch.setattr(_impl, "_invoke", cap)
    res = await _impl.get_chat_impl("oc_x", user_key="ou_outsider")
    assert cap.prefers == ["tenant", "user"]
    assert res["partial"] is True
    assert "user_count" in res["partial_because"]
    assert "asked_as" not in res, "the retry did not help; don't claim it was answered as the user"


@pytest.mark.asyncio
async def test_get_chat_restricted_mode_expands(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {
            "chat_mode": "group",
            "restricted_mode_setting": {
                "status": True,
                "screenshot_has_permission_setting": "not_anyone",
                "download_has_permission_setting": "all_members",
            },
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    settings = (await _impl.get_chat_impl("oc_x"))["settings"]
    assert settings["保密模式"] == "已开启"
    assert settings["可截屏录屏"] == "任何人都不可"
    assert settings["可下载图片/视频/文件"] == "所有群成员"


@pytest.mark.asyncio
async def test_get_chat_requires_chat_id_and_hints_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({})
    monkeypatch.setattr(_impl, "_invoke", cap)
    assert "chat_id is required" in (await _impl.get_chat_impl("  "))["message"]
    assert cap.request is None  # rejected before spending a request

    async def _fail(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": False, "code": 232011, "msg": "out of chat", "message": "err"}

    monkeypatch.setattr(_impl, "_invoke", _fail)
    assert "机器人不在该群里" in (await _impl.get_chat_impl("oc_x"))["hint"]


@pytest.mark.asyncio
async def test_chat_admin_tools_return_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_chat")

    async def _get(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": True, "chat_id": "oc_x", "owner_id": "ou_o", "user_count": 7}

    monkeypatch.setattr(_impl, "get_chat_impl", _get)
    assert json.loads(await mod.feishu_chat_get(chat_id="oc_x"))["user_count"] == 7
    assert inspect.iscoroutinefunction(mod.feishu_chat_get)


# ── Approval — list tasks, read instance, approve/reject ──────────────────────


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
        "feishu_approval_get",
        "feishu_approval_get_definition",
        "feishu_approval_create",
    ):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


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
    """有自动通道时不再让用户复制 code, 而是引导到非阻塞的 feishu_auth_check / collect。"""
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
    # 自动通道下要把 agent 引到非阻塞的 check, 且明说本轮收尾 —— 发链接那轮阻塞等待会
    # 占住 Session 的 turn 锁, 用户这期间说什么都排队。
    assert "feishu_auth_check" in result["next_step"]
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
        "feishu_auth_request",
        "feishu_auth_card",
        "feishu_auth_collect",
        "feishu_auth_check",
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
async def test_auth_check_completes_when_code_already_arrived(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """取件箱里已经有码时, check 直接完成授权 —— 与 wait 走同一条通道。"""
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "gateway"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))

    async def _has_code(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        return {"code": "ARRIVED"}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _has_code)
    completed: dict[str, Any] = {}

    async def _fake_complete(code: str, user_key: str = "") -> dict[str, Any]:
        completed["code"] = code
        return {"ok": True}

    monkeypatch.setattr(_impl, "auth_complete_impl", _fake_complete)
    result = await _impl.auth_check_impl("")
    assert result["ok"] is True
    assert completed["code"] == "ARRIVED"


@pytest.mark.asyncio
async def test_auth_check_does_not_block_when_code_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """码还没到时 check 立刻返回 pending, 且只用极短窗口取件 —— 它不占 turn 锁的根据。"""
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "gateway"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))
    seen: dict[str, float] = {}

    async def _no_code(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        seen["timeout"] = timeout_seconds
        return {}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _no_code)
    result = await _impl.auth_check_impl("")
    assert result["ok"] is False
    assert result["pending"] is True
    # 不是失败, 也不能把用户往「手抄 code」上引。
    assert "feishu_auth_check" in result["retry_hint"]
    assert seen["timeout"] <= _impl._CHECK_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_auth_check_without_pending_asks_for_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "missing.json"))
    result = await _impl.auth_check_impl("")
    assert result["ok"] is False
    assert "feishu_auth_request" in result["message"]


@pytest.mark.asyncio
async def test_auth_check_manual_mode_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """manual 环境没有自动通道, check 也得如实说, 别让 agent 以为再查一次就有。"""
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "manual"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))
    result = await _impl.auth_check_impl("")
    assert result["ok"] is False
    assert result["manual_required"] is True


@pytest.mark.asyncio
async def test_auth_check_surfaces_user_denial(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "gateway"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))

    async def _denied(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        return {"error": "access_denied"}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _denied)
    result = await _impl.auth_check_impl("")
    assert result["ok"] is False
    assert "access_denied" in result["message"]


def _pending_gateway(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, state: str = "st") -> None:
    """写一份 gateway 模式的 pending 记录 (后台收码的前提)。"""
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": state, "mode": "gateway"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))


@pytest.fixture(autouse=True)
def _clean_auth_watchers() -> Any:
    """watcher 表是模块级的; 不清会让上一个用例的结果被下一个当成自己的。"""
    _watch.reset_all()
    yield
    _watch.reset_all()


@pytest.mark.asyncio
async def test_auth_collect_returns_before_the_code_arrives(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """这条链路的全部意义: 收码要多久, 这次工具调用都不能等 —— 等待占的是 Session 的 turn 锁。"""
    _pending_gateway(tmp_path, monkeypatch)
    released = anyio.Event()

    async def _slow_poll(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        await released.wait()  # 模拟用户半天没点「同意」
        return {"code": "LATE"}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _slow_poll)

    completed: dict[str, Any] = {}

    async def _fake_complete(code: str, user_key: str = "") -> dict[str, Any]:
        completed["code"] = code
        return {"ok": True, "message": "授权成功"}

    monkeypatch.setattr(_impl, "auth_complete_impl", _fake_complete)
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))

    # 码还没到就已经返回 —— 且返回的是「在后台等着」而不是超时/失败。
    with anyio.fail_after(2):
        result = await _impl.auth_collect_impl("ou_a")
    assert result["ok"] is True
    assert result["background"] is True
    assert result["status"] == _watch.STATUS_WATCHING
    assert completed == {}

    released.set()
    await _settle(_impl, "ou_a")
    assert completed["code"] == "LATE"
    assert _watch.status("ou_a").status == _watch.STATUS_GRANTED
    # 后台任务不在任何轮次里, 不私聊回告用户就等于悄悄成功了。
    assert dms and dms[0][0] == "ou_a"
    assert "授权成功" in dms[0][1]


@pytest.mark.asyncio
async def test_auth_collect_does_not_start_a_second_watcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """取件箱取走即删: 两个 watcher 会抢同一个码, 抢输的白等到超时。"""
    _pending_gateway(tmp_path, monkeypatch)
    polls = 0
    polling = anyio.Event()
    released = anyio.Event()

    async def _counting_poll(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        nonlocal polls
        polls += 1
        polling.set()
        await released.wait()
        return {}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _counting_poll)
    first = await _impl.auth_collect_impl("ou_a")
    # 先确认后台任务真跑到了取件那一步, 否则「没起第二个」会因为一个都没跑而假成立。
    with anyio.fail_after(2):
        await polling.wait()
    second = await _impl.auth_collect_impl("ou_a")
    assert first["already_watching"] is False
    assert second["already_watching"] is True
    assert polls == 1
    released.set()


@pytest.mark.asyncio
async def test_auth_collect_refuses_manual_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """没有自动回流通道时后台也无从收码, 得说实话而不是假装在等。"""
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps({"state": "st", "mode": "manual"}), encoding="utf-8")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))
    result = await _impl.auth_collect_impl("ou_a")
    assert result["ok"] is False
    assert result["manual_required"] is True


@pytest.mark.asyncio
async def test_auth_collect_reports_a_finished_background_grant(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """后台收完后再调不该去等一个已被取走的码, 而是直接给结论。"""
    _pending_gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(
        _impl._oauth_rx, "poll_gateway", lambda state, timeout_seconds, interval=1.0: _instant({"code": "C"})
    )
    monkeypatch.setattr(_impl, "auth_complete_impl", lambda code, user_key="": _instant({"ok": True}))
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm([]))
    await _impl.auth_collect_impl("ou_a")
    await _settle(_impl, "ou_a")
    again = await _impl.auth_collect_impl("ou_a")
    assert again["ok"] is True
    assert again["collected_in_background"] is True
    assert again["status"] == _watch.STATUS_GRANTED


@pytest.mark.asyncio
async def test_auth_collect_timeout_tells_the_user_instead_of_going_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """守到超时也得回告: 后台任务没有轮次, 不说话用户就一直等一个不会来的回音。"""
    _pending_gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", lambda state, timeout_seconds, interval=1.0: _instant({}))
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))
    await _impl.auth_collect_impl("ou_a", 10)
    await _settle(_impl, "ou_a")
    assert _watch.status("ou_a").status == _watch.STATUS_TIMEOUT
    assert dms and dms[0][0] == "ou_a"


@pytest.mark.asyncio
async def test_auth_collect_never_dms_the_default_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """本机单用户槽位没有收件人; 拿 "default" 当 open_id 发消息只会换来一个 API 报错。"""
    _pending_gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(
        _impl._oauth_rx, "poll_gateway", lambda state, timeout_seconds, interval=1.0: _instant({"code": "C"})
    )
    monkeypatch.setattr(_impl, "auth_complete_impl", lambda code, user_key="": _instant({"ok": True}))
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))
    await _impl.auth_collect_impl("")
    await _settle(_impl, "")
    assert _watch.status("default").status == _watch.STATUS_GRANTED
    assert dms == []


@pytest.mark.asyncio
async def test_auth_collect_admits_when_it_cannot_go_to_the_background(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """起不了后台任务时不能假装在收: agent 会据此收尾, 而实际上没人取码, 授权永远不落地。"""
    _pending_gateway(tmp_path, monkeypatch)

    def _no_loop(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("no running event loop")

    monkeypatch.setattr(_watch, "start", _no_loop)
    result = await _impl.auth_collect_impl("ou_a")
    assert result["ok"] is False
    assert result["background"] is False
    # 退路必须是下一轮 check (非阻塞), 而不是在这一轮原地等。
    assert result["pending"] is True
    assert "feishu_auth_check" in result["retry_hint"]


@pytest.mark.asyncio
async def test_auth_collect_survives_a_failing_collector(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """后台任务抛错必须落进状态并回告 —— 否则只剩一条 "never retrieved" 日志, 用户干等。"""
    _pending_gateway(tmp_path, monkeypatch)

    async def _boom(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        raise RuntimeError("relay exploded")

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _boom)
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))
    await _impl.auth_collect_impl("ou_a")
    await _settle(_impl, "ou_a")
    state = _watch.status("ou_a")
    assert state.status == _watch.STATUS_FAILED
    assert "relay exploded" in state.message
    assert dms and dms[0][0] == "ou_a"


def _granted_pending(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """pending + 一个立刻成功的取码/换码链路 —— 授权成功后的收尾行为专用。"""
    _pending_gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(
        _impl._oauth_rx, "poll_gateway", lambda state, timeout_seconds, interval=1.0: _instant({"code": "C"})
    )
    monkeypatch.setattr(_impl, "auth_complete_impl", lambda code, user_key="": _instant({"ok": True}))


@pytest.mark.asyncio
async def test_granted_auth_resumes_a_turn_instead_of_only_announcing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """授权到账后必须**接着把原来那件事做完**。

    只发一条「授权成功」是这条链路原来的行为, 它把用户真正要的那件事永久留在上一轮没人做 ——
    而那条消息还写着「我接着做」。后台任务没有模型也没有工具循环, 所以唯一的出路是起一个回合。
    """
    _granted_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))
    resumed: list[tuple[str, str]] = []

    async def _fake_resume(session_id: str, content: str, *, kind: str = "chat") -> bool:
        resumed.append((session_id, content))
        return True

    monkeypatch.setattr(_auth.live_agent, "resume_session_turn", _fake_resume)
    monkeypatch.setattr(_auth, "get_session_id", lambda: "feishu-ou_a")

    await _impl.auth_collect_impl("ou_a")
    await _settle(_impl, "ou_a")

    assert _watch.status("ou_a").status == _watch.STATUS_GRANTED
    assert len(resumed) == 1
    session_id, content = resumed[0]
    assert session_id == "feishu-ou_a"
    # 续跑那一轮得知道是谁授权成功了, 还得被明确要求接着做原来那件事。
    assert "<feishu_auth_granted>" in content
    assert "ou_a" in content
    assert "接着做" in content
    # 续跑那一轮自己会说话 —— 这里再发一条私聊就正是「两条不同回复」。
    assert dms == []


@pytest.mark.asyncio
async def test_granted_auth_falls_back_to_a_dm_when_no_turn_can_be_resumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """没有在服务的 live agent 时 (单进程直连 / session 已停) 不能彻底静默。

    退回私聊, 但必须如实说要用户再招呼一句 —— 不能再承诺「我接着做」, 因为没人会做。
    """
    _granted_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))
    monkeypatch.setattr(_auth.live_agent, "resume_session_turn", lambda *a, **k: _instant(False))
    monkeypatch.setattr(_auth, "get_session_id", lambda: "feishu-ou_a")

    await _impl.auth_collect_impl("ou_a")
    await _settle(_impl, "ou_a")

    assert dms and dms[0][0] == "ou_a"
    assert "回我一句" in dms[0][1]


@pytest.mark.asyncio
async def test_granted_auth_dms_when_resuming_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """续跑本身抛错也不能连回执一起吞掉, 否则用户什么都收不到。"""
    _granted_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))

    async def _boom(session_id: str, content: str, *, kind: str = "chat") -> bool:
        raise RuntimeError("resume exploded")

    monkeypatch.setattr(_auth.live_agent, "resume_session_turn", _boom)
    monkeypatch.setattr(_auth, "get_session_id", lambda: "feishu-ou_a")

    await _impl.auth_collect_impl("ou_a")
    await _settle(_impl, "ou_a")

    assert _watch.status("ou_a").status == _watch.STATUS_GRANTED
    assert dms and dms[0][0] == "ou_a"


@pytest.mark.asyncio
async def test_resumed_turn_can_request_auth_again_without_killing_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """钉住一条自指路径: 续跑那一轮跑在 watcher 自己的 task 里, 而它可以再发起授权。

    续跑的回合完全可能再要一次授权 (上次的 scope 不够, 或换个能力集), 那条路会先
    ``forget_and_wait`` 同一个 user_key —— 而这个 user_key 的 task 正是**在跑它自己的**那个,
    于是等于「自己取消自己」。今天这是安全的, 但**不是设计出来的**: 取消在 ``forget_and_wait``
    自己的 ``await task`` 处交付, 被那里的 ``suppress(CancelledError)`` 吞掉 (已实测), 回合照常
    跑完。本用例把这个结论钉住 —— 谁要收窄那处 suppress, 先在这里红一次。
    """
    _granted_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))
    progress: list[str] = []

    async def _resume_that_reauthorizes(session_id: str, content: str, **kwargs: Any) -> bool:
        progress.append("turn-start")
        # 续跑的回合再次发起授权走到的正是这一步。
        await _watch.forget_and_wait(_impl._norm_user_key("ou_a"))
        progress.append("turn-finished")
        return True

    monkeypatch.setattr(_auth.live_agent, "resume_session_turn", _resume_that_reauthorizes)
    monkeypatch.setattr(_auth, "get_session_id", lambda: "feishu-ou_a")

    await _impl.auth_collect_impl("ou_a")
    state = _watch.status("ou_a")
    assert state is not None and state.task is not None
    with anyio.fail_after(2):
        await asyncio.shield(state.task)

    # 关键断言: 回合是**跑完**的, 不是被自己掐断在中间。
    assert progress == ["turn-start", "turn-finished"]
    assert dms == []


@pytest.mark.asyncio
async def test_failed_auth_does_not_resume_a_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """超时/失败没有「接着做」可言: 起一轮只会让模型对着一个没成的授权行动。"""
    _pending_gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", lambda state, timeout_seconds, interval=1.0: _instant({}))
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm(dms := []))
    resumed: list[str] = []
    monkeypatch.setattr(
        _auth.live_agent,
        "resume_session_turn",
        lambda session_id, content, **k: (resumed.append(session_id), _instant(True))[1],
    )
    monkeypatch.setattr(_auth, "get_session_id", lambda: "feishu-ou_a")

    await _impl.auth_collect_impl("ou_a", 10)
    await _settle(_impl, "ou_a")

    assert _watch.status("ou_a").status == _watch.STATUS_TIMEOUT
    assert resumed == []
    assert dms and dms[0][0] == "ou_a"


@pytest.mark.asyncio
async def test_collect_tells_the_agent_not_to_narrate_the_wait(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """「两条不同回复」的另一半: 本轮播报等待, 往往比续跑的回话更晚到。

    码通常几秒就回来, 所以「授权正在后台等待确认」经常排在「已授权成功」之后, 用户看到的是
    两条自相矛盾的消息。工具返回值必须把这条讲清楚, 否则模型没有理由不播报。
    """
    _pending_gateway(tmp_path, monkeypatch)
    released = anyio.Event()

    async def _slow_poll(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        await released.wait()
        return {}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _slow_poll)
    result = await _impl.auth_collect_impl("ou_a")
    guidance = result["message"] + result["next_step"]
    assert "不要对用户播报等待状态" in guidance
    assert "零 assistant 文本" in guidance
    released.set()


@pytest.mark.asyncio
async def test_auth_check_defers_to_a_running_collector(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """check 也不该和后台抢码: 抢走一个, 另一边就白等到窗口关闭。"""
    _pending_gateway(tmp_path, monkeypatch)
    polls = 0
    polling = anyio.Event()
    released = anyio.Event()

    async def _counting_poll(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        nonlocal polls
        polls += 1
        polling.set()
        await released.wait()
        return {}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _counting_poll)
    await _impl.auth_collect_impl("ou_a")
    with anyio.fail_after(2):
        await polling.wait()
    result = await _impl.auth_check_impl("ou_a")
    assert result["ok"] is True
    assert result["status"] == _watch.STATUS_WATCHING
    assert polls == 1  # check 没有自己再取一次
    released.set()


@pytest.mark.asyncio
async def test_auth_check_reports_background_grant_not_missing_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """后台成功会删掉 pending 文件; 若不看 watcher, check 会把已成功说成「请重新发起」。"""
    _pending_gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(
        _impl._oauth_rx, "poll_gateway", lambda state, timeout_seconds, interval=1.0: _instant({"code": "C"})
    )
    monkeypatch.setattr(
        _impl, "auth_complete_impl", lambda code, user_key="": _instant({"ok": True, "message": "授权成功"})
    )
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm([]))
    await _impl.auth_collect_impl("ou_a")
    await _settle(_impl, "ou_a")
    (tmp_path / "pending.json").unlink()  # auth_complete 成功后就是这个状态
    result = await _impl.auth_check_impl("ou_a")
    assert result["ok"] is True
    assert result["collected_in_background"] is True
    assert "feishu_auth_request" not in result["message"]


@pytest.mark.asyncio
async def test_reauth_keeps_the_loopback_channel_after_a_watcher_held_the_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """撤 watcher 必须**等它真关掉监听**, 否则重新授权会被静默降级成手工贴码。

    实测过的回归: 只 cancel 不等待时, plan_receiver 的端口探测撞上仍未关闭的回环监听,
    mode 从 loopback 掉到 manual —— 用户白白多了一步手抄 code。
    """
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.delenv("PSI_OAUTH_CALLBACK_BASE", raising=False)
    monkeypatch.delenv("PSI_FEISHU_REDIRECT_URI", raising=False)
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))

    # 真的占住端口才测得到那个 bug, 所以这里包一层真实的 wait_loopback: 它绑上端口后
    # 置位 bound, 我们等这个信号 (而不是轮询端口), 之后才让第二次授权去重新选通道。
    bound = anyio.Event()
    real_wait_loopback = _impl._oauth_rx.wait_loopback

    async def _wait_loopback_signalling(port: int, expected_state: str, timeout_seconds: float) -> dict[str, str]:
        async with anyio.create_task_group() as tg:

            async def _flag() -> None:
                # 让出调度, 让 real_wait_loopback 先跑到 create_tcp_listener 那一步
                await anyio.sleep(0.05)
                bound.set()

            tg.start_soon(_flag)
            return await real_wait_loopback(port, expected_state, timeout_seconds)

    monkeypatch.setattr(_impl._oauth_rx, "wait_loopback", _wait_loopback_signalling)

    first = await _impl.auth_start_impl("docx_write", "ou_a")
    assert first["mode"] == "loopback"
    await _impl.auth_collect_impl("ou_a", 600)
    with anyio.fail_after(2):
        await bound.wait()
    assert not _impl._oauth_rx._port_is_free(_impl._oauth_rx.loopback_port()), "前提不成立: watcher 没占住端口"

    monkeypatch.setattr(_impl._oauth_rx, "wait_loopback", real_wait_loopback)
    second = await _impl.auth_start_impl("docx_write", "ou_a")
    assert second["mode"] == "loopback", "重新授权被降级了 —— 旧 watcher 的监听还没关"
    assert second["auto_receive"] is True
    assert _watch.status("ou_a") is None


@pytest.mark.asyncio
async def test_auth_start_forgets_a_stale_watcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """新一轮授权作废旧 state; 留着旧 watcher 的结果会被当成本次的。"""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    _pending_gateway(tmp_path, monkeypatch)
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    monkeypatch.setattr(
        _impl._oauth_rx,
        "plan_receiver",
        lambda explicit="": _impl._oauth_rx.ReceiverPlan(mode="gateway", redirect_uri="https://gw/x"),
    )
    released = anyio.Event()

    async def _slow_poll(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        await released.wait()
        return {}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _slow_poll)
    await _impl.auth_collect_impl("ou_a")
    assert _watch.is_watching("ou_a") is True
    await _impl.auth_start_impl("docx_write", "ou_a")
    assert _watch.status("ou_a") is None
    released.set()


@pytest.mark.parametrize(
    ("uri", "private"),
    [
        ("http://192.168.60.214:8090/oauth/callback", True),
        ("http://10.0.0.5:8090/oauth/callback", True),
        ("http://172.16.3.9/oauth/callback", True),
        ("http://127.0.0.1:17860/oauth/callback", True),
        ("http://localhost:17860/oauth/callback", True),
        ("https://haitun.example.com/oauth/callback", False),
        # 注意别拿 203.0.113.x (TEST-NET-3) 当公网样本: 文档保留段在 ipaddress 里
        # 也算 is_private, 会把这个用例变成假失败。
        ("https://8.8.8.8/oauth/callback", False),
        ("", False),
    ],
)
def test_is_private_callback_classifies_reachability(uri: str, private: bool) -> None:
    """内网回调地址必须被认出来 —— 外网用户的浏览器跳不到那里, 自动回流不成立。"""
    assert _impl._oauth_rx.is_private_callback(uri) is private


@pytest.mark.asyncio
async def test_auth_start_flags_private_callback(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """回调地址是内网 IP 时, 仍走自动通道, 但要交出「贴整条网址」这条后路。"""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    monkeypatch.setattr(
        _impl._oauth_rx,
        "plan_receiver",
        lambda explicit="": _impl._oauth_rx.ReceiverPlan(
            mode="gateway", redirect_uri="http://192.168.60.214:8090/oauth/callback"
        ),
    )
    result = await _impl.auth_start_impl("", "ou_a")
    assert result["ok"] is True
    # 自动通道不能因为地址是内网就被取消: 内网用户照样免复制。
    assert result["auto_receive"] is True
    assert result["callback_is_private"] is True
    assert "feishu_auth_complete" in result["fallback_hint"]


@pytest.mark.asyncio
async def test_auth_start_public_callback_has_no_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """公网回调地址不该挂那段手工提示 —— 那是内网专属的补丁。"""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    monkeypatch.setattr(
        _impl._oauth_rx,
        "plan_receiver",
        lambda explicit="": _impl._oauth_rx.ReceiverPlan(
            mode="gateway", redirect_uri="https://haitun.example.com/oauth/callback"
        ),
    )
    result = await _impl.auth_start_impl("", "ou_a")
    assert result["ok"] is True
    assert "callback_is_private" not in result
    assert "fallback_hint" not in result


@pytest.mark.asyncio
async def test_collect_timeout_offers_url_paste_when_private(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """内网回调超时不能只叫「再等等」: 外网用户再等也不会有回调, 得给另一条出路。"""
    pending = tmp_path / "pending.json"
    pending.write_text(
        json.dumps(
            {
                "state": "st",
                "mode": "gateway",
                "redirect_uri": "http://192.168.60.214:8090/oauth/callback",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(pending))

    async def _no_code(state: str, timeout_seconds: float, interval: float = 1.0) -> dict[str, str]:
        return {}

    monkeypatch.setattr(_impl._oauth_rx, "poll_gateway", _no_code)
    monkeypatch.setattr(_impl, "send_message_impl", _record_dm([]))
    await _impl.auth_collect_impl("ou_a", 10)
    await _settle(_impl, "ou_a")
    # 结论落在 watcher 的 result 上 —— 后台超时没有「工具返回值」可看, agent 是再调一次
    # collect 才读到它的, 所以这条出路必须写在结果里而不是只写在日志里。
    result = _watch.status("ou_a").result
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["callback_is_private"] is True
    assert "整条网址" in result["message"]
    assert "feishu_auth_complete" in result["message"]


@pytest.mark.asyncio
async def test_auth_complete_accepts_full_callback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户贴回来的是整条网址而不是 code —— 必须能直接用。"""
    assert _impl._extract_code("http://192.168.60.214:8090/oauth/callback?code=abc123&state=st") == "abc123"
    assert _impl._extract_code("  abc123  ") == "abc123"


def _auth_card_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    mode: str = "gateway",
    redirect_uri: str = "",
) -> dict[str, Any]:
    """Configure an app + a receiver channel, and capture what gets sent."""
    monkeypatch.setenv("PSI_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("PSI_FEISHU_APP_SECRET", "sec")
    monkeypatch.setattr(_impl, "_pending_auth_path", lambda user_key="": str(tmp_path / "pending.json"))
    monkeypatch.setattr(_impl, "_granted_scopes_path", lambda: str(tmp_path / "granted.json"))
    default_redirect = "https://gw.example.com/oauth/callback" if mode == "gateway" else "http://localhost/"
    monkeypatch.setattr(
        _impl._oauth_rx,
        "plan_receiver",
        lambda explicit="": _impl._oauth_rx.ReceiverPlan(
            mode=mode,
            redirect_uri=redirect_uri or default_redirect,
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
async def test_auth_card_carries_private_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """卡片按钮 open_url 打开的是同一个内网 redirect, 所以第 1 级也得带后路。"""
    _auth_card_env(monkeypatch, tmp_path, redirect_uri="http://192.168.60.214:8090/oauth/callback")
    result = await _impl.auth_card_impl("ou_a", "docx_write", "写文档", "ou_a")
    assert result["ok"] is True
    assert result["callback_is_private"] is True
    assert "feishu_auth_complete" in result["fallback_hint"]


@pytest.mark.asyncio
async def test_auth_card_public_callback_has_no_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """公网回调时卡片不该多挂那段内网提示。"""
    _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_card_impl("ou_a", "docx_write", "写文档", "ou_a")
    assert result["ok"] is True
    assert "callback_is_private" not in result
    assert "fallback_hint" not in result


@pytest.mark.asyncio
async def test_auth_request_tier_card_mentions_private_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """走到第 1 级时, next_step 也要写明外网用户该怎么办。"""
    _auth_card_env(monkeypatch, tmp_path, redirect_uri="http://192.168.60.214:8090/oauth/callback")
    result = await _impl.auth_request_impl("ou_a", "docx_write", "写文档", "ou_a")
    assert result["tier"] == _impl.TIER_CARD
    assert result["callback_is_private"] is True
    assert "feishu_auth_complete" in result["next_step"]


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
    # 点击那轮走非阻塞的 collect: 收到点击时用户才刚要点「同意」, 在那一轮原地等就又把
    # 会话锁住几分钟 —— 那正是这条链路要消灭的症状。
    assert captured["action_handlers"] == {_impl._AUTH_CARD_ACTION: "feishu_auth_collect"}
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
    assert result["action_handler"] == "feishu_auth_collect"
    msg = result["message"]
    assert "这一轮到此为止" in msg
    assert "feishu_auth_collect" in msg
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
async def test_auth_request_prefers_the_card(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Tier 1 wins whenever it can: one tap is the least the user can be asked to do."""
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_request_impl("ou_a", "bitable_write", "要把台账建在你名下")
    assert result["ok"] is True
    assert result["tier"] == _impl.TIER_CARD
    assert result["message_id"] == "om_auth"  # a card really went out
    assert captured["receive_id"] == "ou_a"
    assert "downgraded_from" not in result  # nothing was given up


@pytest.mark.asyncio
async def test_auth_request_falls_back_to_link_without_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """No private chat to send a card to → tier 2: still no code to copy."""
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_request_impl("ou_a", receive_id="oc_group")
    assert result["ok"] is True
    assert result["tier"] == _impl.TIER_LINK
    assert result["auto_receive"] is True
    assert result["authorize_url"]
    assert result["downgraded_from"] == _impl.TIER_CARD
    assert "ou_" in result["downgrade_reason"]  # says why, so the agent can be honest
    # 第 2 级同样不许在发链接那轮阻塞: 收尾 + 下一轮 check。
    assert "feishu_auth_check" in result["next_step"]
    assert captured == {}  # no card was sent


@pytest.mark.asyncio
async def test_auth_request_falls_back_to_link_with_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """No automatic channel at all → tier 3, the only tier that can keep its promise."""
    captured = _auth_card_env(monkeypatch, tmp_path, mode="manual")
    result = await _impl.auth_request_impl("ou_a")
    assert result["ok"] is True
    assert result["tier"] == _impl.TIER_MANUAL
    assert result["auto_receive"] is False
    assert result["authorize_url"]
    assert result["downgraded_from"] == _impl.TIER_CARD
    assert "feishu_auth_complete" in result["next_step"]
    assert captured == {}


@pytest.mark.asyncio
async def test_auth_request_downgrades_to_link_when_the_card_send_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A card that cannot be delivered must not sink the whole request — the link still works."""
    _auth_card_env(monkeypatch, tmp_path)

    async def _failing_send(*a: Any, **k: Any) -> dict[str, Any]:
        return {"ok": False, "message": "card send failed"}

    monkeypatch.setattr(_impl, "send_card_impl", _failing_send)
    result = await _impl.auth_request_impl("ou_a")
    assert result["ok"] is True
    assert result["tier"] == _impl.TIER_LINK
    assert result["downgraded_from"] == _impl.TIER_CARD
    assert "card send failed" in result["downgrade_reason"]


@pytest.mark.asyncio
async def test_auth_request_requires_a_user_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_request_impl("   ")
    assert result["ok"] is False
    assert "user_key" in result["message"]
    assert captured == {}


@pytest.mark.asyncio
async def test_auth_request_propagates_bad_capabilities(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """A raw scope string is refused at every tier, not quietly downgraded into one."""
    captured = _auth_card_env(monkeypatch, tmp_path)
    result = await _impl.auth_request_impl("ou_a", "docx:document")
    assert result["ok"] is False
    assert "capability_keys" in result
    assert "tier" not in result
    assert captured == {}


@pytest.mark.asyncio
async def test_auth_request_tool_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    auth_mod = importlib.import_module("feishu_auth")
    captured: dict[str, Any] = {}

    async def _fake(user_key: str, capabilities: str = "", reason: str = "", receive_id: str = "") -> dict[str, Any]:
        captured.update(user_key=user_key, capabilities=capabilities, reason=reason, receive_id=receive_id)
        return {"ok": True, "tier": "card"}

    monkeypatch.setattr(auth_mod._f, "auth_request_impl", _fake)
    out = await auth_mod.feishu_auth_request("ou_a", "docx_write", "建周报")
    assert json.loads(out)["tier"] == "card"
    assert captured == {"user_key": "ou_a", "capabilities": "docx_write", "reason": "建周报", "receive_id": ""}


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


def test_auth_prompt_states_the_tier_order_and_keeps_the_manual_fallback() -> None:
    """need_auth guidance must point at the one entry point and spell the ladder out in
    order — and still describe the manual path, since some deployments only have that."""
    prompt = _impl._AUTH_PROMPT
    assert "feishu_auth_request" in prompt
    # the three tiers, named and in descending order of how little the user must do
    assert prompt.index(_impl.TIER_CARD) < prompt.index(_impl.TIER_LINK) < prompt.index(_impl.TIER_MANUAL)
    # 收码一律走非阻塞的 collect, 且提示里不该再留下任何「干等」的工具名可供模型调用。
    assert "feishu_auth_collect" in prompt
    assert "feishu_auth_wait" not in prompt
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


def test_bitable_tools_async_with_docstrings() -> None:
    """The tools that stayed behind after the endpoint table took over the rest."""
    mod = importlib.import_module("feishu_bitable")
    for name in (
        "feishu_bitable_search_records",
        "feishu_bitable_create_records",
        "feishu_bitable_update_record",
        "feishu_bitable_update_records",
        "feishu_bitable_clear_table",
        "feishu_bitable_update_field",
        "feishu_bitable_create_table",
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


# ── Tasks — the create path that assignment_accept still needs ────────────────


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


@pytest.mark.asyncio
async def test_assignment_task_create_disables_rate_limit_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_task")
    captured: dict[str, Any] = {}

    async def create_once(*args: Any, retry_rate_limits: bool = True, **kwargs: Any) -> dict[str, Any]:
        captured["retry_rate_limits"] = retry_rate_limits
        return {"ok": True, "task_guid": "g1", "url": "http://t/g1"}

    monkeypatch.setattr(mod._f, "create_task_impl", create_once)
    result = json.loads(await mod._feishu_task_create_once("任务"))

    assert result["ok"] is True
    assert captured["retry_rate_limits"] is False


def test_due_to_ms_parsing() -> None:
    assert _impl._due_to_ms("") is None
    assert _impl._due_to_ms("not a date") is None
    assert _impl._due_to_ms("2026-07-15").isdigit()
    assert _impl._due_to_ms("2026-07-15 18:00").isdigit()


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


@pytest.mark.asyncio
async def test_download_file_via_drive_file_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``source_type="file"`` reaches the *files* endpoint, not the medias one.

    The two are disjoint — medias serves what lives inside a document, files serves a
    standalone resource file in Drive — and the wrong one is a 404 rather than a redirect.
    """
    captured: dict[str, Any] = {}

    class _Client:
        async def arequest(self, req: Any) -> Any:
            captured["uri"] = req.uri
            captured["token"] = req.paths.get("file_token")
            return _FakeResp(None, "", b"%PDF-1.7 body")

    monkeypatch.setattr(_impl, "_get_client", lambda: _Client())
    dest = tmp_path / "docs" / "handbook.pdf"
    result = await _impl.download_file_impl("boxcn_tok", str(dest), False, "", "file")
    assert result["ok"] is True
    assert captured["uri"].endswith("/drive/v1/files/:file_token/download")
    assert captured["token"] == "boxcn_tok"
    assert dest.read_bytes() == b"%PDF-1.7 body"
    assert result["source_type"] == "file"


@pytest.mark.asyncio
async def test_download_defaults_to_media_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Callers that predate ``source_type`` keep hitting the endpoint they always did."""
    captured: dict[str, Any] = {}

    class _Client:
        async def arequest(self, req: Any) -> Any:
            captured["uri"] = req.uri
            return _FakeResp(None, "", b"data")

    monkeypatch.setattr(_impl, "_get_client", lambda: _Client())
    result = await _impl.download_file_impl("media_tok", str(tmp_path / "a.bin"))
    assert result["ok"] is True
    assert captured["uri"].endswith("/drive/v1/medias/:file_token/download")
    assert result["source_type"] == "media"


@pytest.mark.asyncio
async def test_is_url_still_wins_over_source_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``is_url=True`` is the older spelling of ``source_type="url"`` and must keep working.

    Existing call sites pass it positionally alongside the default ``source_type``, so the
    two have to agree rather than the newer argument quietly overriding the older one.
    """

    async def fake_url_bytes(url: str) -> tuple[bytes | None, str]:
        assert url == "https://f.co/a.jpg"
        return b"JPEGDATA", ""

    monkeypatch.setattr(_impl, "_download_url_bytes", fake_url_bytes)
    dest = tmp_path / "a.jpg"
    result = await _impl.download_file_impl("https://f.co/a.jpg", str(dest), True)
    assert result["ok"] is True
    assert result["source_type"] == "url"
    assert dest.read_bytes() == b"JPEGDATA"


@pytest.mark.asyncio
async def test_download_rejects_an_unknown_source_type(tmp_path: Path) -> None:
    """A typo must not silently fall back to a different endpoint than intended."""
    result = await _impl.download_file_impl("tok", str(tmp_path / "a.bin"), False, "", "drive")
    assert result["ok"] is False
    assert "source_type" in result["message"]
    assert not (tmp_path / "a.bin").exists()


@pytest.mark.asyncio
async def test_download_as_user_keeps_the_chosen_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The tenant→user retry must not drop back to the medias endpoint.

    A user-owned PDF is exactly the case where the bot's token fails and the retry
    matters, so a retry that silently changed endpoint would 404 on the second try and
    read as "the user has no access".
    """
    seen: list[str] = []

    class _TenantClient:
        async def arequest(self, req: Any) -> Any:
            seen.append(req.uri)
            return _FakeResp(None, "", b"")  # no content → falls through to the user retry

    class _UatClient:
        async def arequest(self, req: Any, option: Any = None) -> Any:
            seen.append(req.uri)
            return _FakeResp(None, "", b"%PDF user copy")

    monkeypatch.setattr(_impl, "_get_client", lambda: _TenantClient())
    monkeypatch.setattr(_impl, "_get_uat_client", lambda: _UatClient())

    async def _uat(user_key: str = "") -> Any:
        return _FakeUAT()

    monkeypatch.setattr(_impl, "_get_valid_uat", _uat)
    dest = tmp_path / "u.pdf"
    result = await _impl.download_file_impl("boxcn_tok", str(dest), False, "ou_a", "file")
    assert result["ok"] is True, result
    assert len(seen) == 2, seen
    assert all(uri.endswith("/drive/v1/files/:file_token/download") for uri in seen), seen
    assert dest.read_bytes() == b"%PDF user copy"


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


# ── Embedded spreadsheets / bitables inside a doc (block_type 30 / 18) ─────────


def test_split_embedded_sheet_token_takes_first_underscore() -> None:
    assert _impl.split_embedded_sheet_token("LGCes_pY34sT") == ("LGCes", "pY34sT")
    # Splitting from the right would mangle a container token containing an underscore.
    assert _impl.split_embedded_sheet_token("has_under_child") == ("has", "under_child")


def test_split_embedded_sheet_token_rejects_halfless_input() -> None:
    for bad in ("", "  ", "nounderscore", "_tail", "head_"):
        assert _impl.split_embedded_sheet_token(bad) == ("", ""), bad


def test_column_letter_wraps_past_z() -> None:
    assert (_impl._column_letter(1), _impl._column_letter(26)) == ("A", "Z")
    assert (_impl._column_letter(27), _impl._column_letter(52)) == ("AA", "AZ")
    assert _impl._column_letter(0) == "A"  # never emits an empty range


@pytest.mark.asyncio
async def test_append_doc_sheet_creates_block_and_returns_write_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = _CapturedInvoke(
        {"children": [{"block_id": "doxcnSheet", "block_type": 30, "sheet": {"token": "shtTok_pY34sT"}}]}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_sheet_impl("doc1", rows=4, columns=3)
    assert result["ok"] is True
    req = cap.request
    assert req.uri == "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    assert req.body["children"][0] == {"block_type": 30, "sheet": {"row_size": 4, "column_size": 3}}
    # The split token is the whole point: these are what feishu_sheet_write takes.
    assert result["spreadsheet_token"] == "shtTok"
    assert result["sheet_id"] == "pY34sT"
    assert result["range"] == "pY34sT!A1"
    assert result["block_id"] == "doxcnSheet"


@pytest.mark.asyncio
async def test_append_doc_sheet_clamps_creation_and_grows_by_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Creation is capped at 9x9 by Feishu; the write is what delivers the asked-for size.

    Sending row_size 12 would fail the whole call with 99992402, so the block is created
    clamped and the ranged write (which does grow the worksheet) covers the real grid.
    """
    calls: list[Any] = []

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        calls.append(req)
        return {
            "ok": True,
            "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "shtTok_s1"}}]},
        }

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    rows = json.dumps([[f"r{i}", i, "x"] for i in range(12)])
    result = await _impl.append_doc_sheet_impl("doc1", values_json=rows, header_row=False)
    assert result["ok"] is True
    assert result["values_written"] is True
    created = calls[0].body["children"][0]["sheet"]
    assert created == {"row_size": 9, "column_size": 3}
    # The result reports the size the reader ends up seeing, not the clamped block.
    assert (result["rows"], result["columns"]) == (12, 3)
    # Second call is the values write, aimed at the embedded worksheet.
    assert calls[1].uri == "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values"
    assert calls[1].paths["spreadsheet_token"] == "shtTok"
    # A bare "s1!A1" is accepted by Feishu but writes nothing, so the range spans the grid.
    assert calls[1].body["valueRange"]["range"] == "s1!A1:C12"


@pytest.mark.asyncio
async def test_append_doc_sheet_never_creates_a_block_over_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any requested size, data or not, must leave creation within 9x9."""
    seen: list[dict[str, Any]] = []

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        if "docx" in req.uri:
            seen.append(req.body["children"][0]["sheet"])
        return {
            "ok": True,
            "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "tok_s1"}}]},
        }

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    await _impl.append_doc_sheet_impl("doc1", rows=200, columns=40)
    await _impl.append_doc_sheet_impl("doc1", rows=10, columns=5)
    await _impl.append_doc_sheet_impl("doc1", values_json=json.dumps([[1] * 30] * 60))
    assert seen == [
        {"row_size": 9, "column_size": 9},
        {"row_size": 9, "column_size": 5},
        {"row_size": 9, "column_size": 9},
    ]


@pytest.mark.asyncio
async def test_append_doc_sheet_lets_the_data_decide_the_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4-column table must not be padded out to the default 5 with a stray empty column."""
    calls: list[Any] = []

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        calls.append(req)
        return {
            "ok": True,
            "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "tok_s1"}}]},
        }

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    grid = json.dumps([["a", "b", "c", "d"], [1, 2, 3, 4]])
    result = await _impl.append_doc_sheet_impl("doc1", values_json=grid, header_row=False)
    assert (result["rows"], result["columns"]) == (2, 4)
    assert calls[1].body["valueRange"]["range"] == "s1!A1:D2"


@pytest.mark.asyncio
async def test_append_doc_sheet_grows_an_empty_sheet_with_blanks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for 20 empty rows to type into must not silently yield 9."""
    calls: list[Any] = []

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        calls.append(req)
        return {
            "ok": True,
            "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "tok_s1"}}]},
        }

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    result = await _impl.append_doc_sheet_impl("doc1", rows=20, columns=6)
    assert result["ok"] is True
    assert (result["rows"], result["columns"]) == (20, 6)
    assert "values_written" not in result  # nothing was written *as data*
    grow = calls[1]
    assert grow.body["valueRange"]["range"] == "s1!A1:F20"
    values = grow.body["valueRange"]["values"]
    assert len(values) == 20
    assert values[0] == [None] * 6  # blank cells, not placeholder text


@pytest.mark.asyncio
async def test_append_doc_sheet_within_the_cap_makes_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A small empty sheet needs no growing write — don't spend a request on it."""
    calls: list[Any] = []

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        calls.append(req)
        return {
            "ok": True,
            "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "tok_s1"}}]},
        }

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    result = await _impl.append_doc_sheet_impl("doc1", rows=5, columns=4)
    assert result["ok"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_append_doc_sheet_bolds_header_row(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        seen.append(req)
        return {
            "ok": True,
            "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "tok_s1"}}]},
        }

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    result = await _impl.append_doc_sheet_impl("doc1", values_json='[["姓名","评分"],["张三",95]]', header_row=True)
    assert result["ok"] is True
    assert result["header_styled"] is True
    style_req = seen[-1]
    assert style_req.uri == "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style"
    assert style_req.body["appendStyle"]["range"] == "s1!A1:B1"
    assert style_req.body["appendStyle"]["style"]["font"]["bold"] is True


@pytest.mark.asyncio
async def test_append_doc_sheet_keeps_coordinates_when_the_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The embed exists once created — dropping its token would orphan an empty sheet."""

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        if "docx" in req.uri:
            return {
                "ok": True,
                "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "tok_s1"}}]},
            }
        return {"ok": False, "message": "no permission", "need_auth": True}

    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    result = await _impl.append_doc_sheet_impl("doc1", values_json='[["a"]]')
    assert result["ok"] is False
    assert result["values_written"] is False
    assert result["need_auth"] is True
    assert (result["spreadsheet_token"], result["sheet_id"]) == ("tok", "s1")


@pytest.mark.asyncio
async def test_append_doc_sheet_validates_input() -> None:
    assert (await _impl.append_doc_sheet_impl(""))["ok"] is False
    assert (await _impl.append_doc_sheet_impl("doc1", rows=0))["ok"] is False
    assert (await _impl.append_doc_sheet_impl("doc1", rows=99999))["ok"] is False
    bad_values = await _impl.append_doc_sheet_impl("doc1", values_json="not json")
    assert bad_values["ok"] is False


@pytest.mark.asyncio
async def test_append_doc_sheet_reports_unsplittable_token(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke({"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "nounderscore"}}]})
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_sheet_impl("doc1", values_json='[["a"]]')
    assert result["ok"] is False
    assert "could not be split" in result["message"]


@pytest.mark.asyncio
async def test_append_doc_bitable_returns_app_and_table(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _CapturedInvoke(
        {"children": [{"block_id": "doxcnBt", "block_type": 18, "bitable": {"token": "appTok_tblXYZ"}}]}
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.append_doc_bitable_impl("doc1")
    assert result["ok"] is True
    # An empty bitable object is rejected by Feishu (1770001), so view_type is explicit.
    assert cap.request.body["children"][0] == {"block_type": 18, "bitable": {"view_type": 1}}
    assert result["app_token"] == "appTok"
    assert result["table_id"] == "tblXYZ"


@pytest.mark.asyncio
async def test_append_doc_sheet_writes_caption_above(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_caption(
        document_id: str, caption: str, auto_number: bool, user_key: str, identity: str
    ) -> tuple[str, dict[str, Any]]:
        return f"表 3：{caption}", {"caption_number": 3}  # noqa: RUF001

    order: list[str] = []

    async def fake_append_content(document_id: str, content: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        order.append(content)
        return {"ok": True, "added": 1}

    async def fake_invoke(request: Any, **kwargs: Any) -> dict[str, Any]:
        order.append("sheet-block")
        return {
            "ok": True,
            "data": {"children": [{"block_id": "b1", "block_type": 30, "sheet": {"token": "tok_s1"}}]},
        }

    monkeypatch.setattr(_impl, "_resolve_table_caption", fake_caption)
    monkeypatch.setattr(_impl, "append_doc_content_impl", fake_append_content)
    monkeypatch.setattr(_impl, "_invoke", fake_invoke)
    # Within the 9x9 creation cap, so the only calls are the caption and the block itself.
    result = await _impl.append_doc_sheet_impl("doc1", rows=5, columns=3, caption="客户明细")
    assert result["ok"] is True
    assert result["caption_number"] == 3
    assert order == ["表 3：客户明细", "sheet-block"]  # noqa: RUF001 — caption first, above the sheet


@pytest.mark.asyncio
async def test_list_doc_blocks_surfaces_embedded_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Editing an *existing* embed is only possible if listing gives back its token."""
    cap = _CapturedInvoke(
        {
            "items": [
                {"block_id": "p1", "block_type": 2, "text": {"elements": [{"text_run": {"content": "hi"}}]}},
                {"block_id": "s1", "block_type": 30, "sheet": {"token": "shtTok_wsA"}},
                {"block_id": "b1", "block_type": 18, "bitable": {"token": "appTok_tbl1"}},
            ],
            "page_token": "",
        }
    )
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.list_doc_blocks_impl("doc1", 50)
    assert result["ok"] is True
    by_id = {b["block_id"]: b for b in result["blocks"]}
    assert by_id["s1"]["type_name"] == "sheet"
    assert by_id["s1"]["spreadsheet_token"] == "shtTok"
    assert by_id["s1"]["range"] == "wsA!A1"
    assert by_id["b1"]["type_name"] == "bitable"
    assert (by_id["b1"]["app_token"], by_id["b1"]["table_id"]) == ("appTok", "tbl1")
    # A plain paragraph gains no embed keys.
    assert "spreadsheet_token" not in by_id["p1"]


def test_embedded_sheet_tools_are_async_with_docstrings() -> None:
    mod = importlib.import_module("feishu_doc")
    for name in ("feishu_doc_append_sheet", "feishu_doc_append_bitable"):
        fn = getattr(mod, name)
        assert inspect.iscoroutinefunction(fn), name
        assert (inspect.getdoc(fn) or "").strip(), f"{name} needs a docstring"


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
async def test_invoke_can_disable_rate_limit_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def once(request: Any, user_key: Any = None, prefer: str = "tenant", **_kw: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        return {"ok": False, "code": None, "http_status": 429, "msg": "too many"}

    monkeypatch.setattr(_impl, "_invoke_once", once)
    res = await _impl._invoke(object(), retry_rate_limits=False)

    assert res["ok"] is False
    assert res["http_status"] == 429
    assert attempts == 1


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
        "feishu_bitable": ["feishu_bitable_create_table", "feishu_bitable_create_records"],
        "feishu_sheet": ["feishu_sheet_write", "feishu_sheet_append", "feishu_sheet_format"],
        "feishu_drive": ["feishu_drive_upload"],
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
        ("feishu_bitable", "feishu_bitable_search_records"),
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


# ── Rich media messages — image / file / audio / video / post ───────────────────


class _MediaInvoke:
    """Replace _invoke for a two-call media send: record every request, answer per path."""

    def __init__(self, image_key: str = "img_v3_1", file_key: str = "file_v3_1") -> None:
        self.requests: list[Any] = []
        self._image_key = image_key
        self._file_key = file_key

    async def __call__(self, request: Any, **kwargs: Any) -> dict[str, Any]:
        req = request() if callable(request) else request
        self.requests.append(req)
        uri = req.uri
        if uri.endswith("/im/v1/images"):
            data: dict[str, Any] = {"image_key": self._image_key}
        elif uri.endswith("/im/v1/files"):
            data = {"file_key": self._file_key}
        else:
            data = {"message_id": "om_new", "thread_id": "omt_new", "chat_id": "oc_1"}
        return {"ok": True, "code": 0, "msg": "", "data": data}


@pytest.mark.asyncio
async def test_upload_image_puts_binary_in_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "chart.png"
    f.write_bytes(b"png-bytes")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.upload_image_impl(str(f))
    req = cap.requests[0]
    assert req.http_method.name == "POST"
    assert req.uri == "/open-apis/im/v1/images"
    assert req.body["image_type"] == "message"
    # Same trap as drive uploads: the SDK overwrites req.files from the body right
    # before sending, so the binary must be an io.IOBase *in the body* with a .name —
    # otherwise the request goes out as JSON and Feishu says "boundary not found".
    sent = req.body["image"]
    assert isinstance(sent, io.IOBase)
    assert sent.name == "chart.png"
    assert sent.read() == b"png-bytes"
    assert result == {"ok": True, "image_key": "img_v3_1", "file_name": "chart.png", "size": 9}


@pytest.mark.asyncio
async def test_upload_image_rejects_non_image_and_empty_and_oversize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doc = tmp_path / "notes.txt"
    doc.write_bytes(b"hi")
    assert "not an image" in (await _impl.upload_image_impl(str(doc)))["message"]
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert "empty" in (await _impl.upload_image_impl(str(empty)))["message"]
    assert "file not found" in (await _impl.upload_image_impl(str(tmp_path / "nope.png")))["message"]
    big = tmp_path / "big.png"
    big.write_bytes(b"x")
    monkeypatch.setattr(_impl, "_IMAGE_UPLOAD_MAX_BYTES", 0)
    assert "over the 0MB limit" in (await _impl.upload_image_impl(str(big)))["message"]


@pytest.mark.asyncio
async def test_upload_file_derives_file_type_from_suffix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # file_type is Feishu's enum, not the extension: mapped where a mapping exists,
    # "stream" for everything else (which is how a .zip/.csv attachment is sent).
    for name, expected in (
        ("a.mp4", "mp4"),
        ("b.pdf", "pdf"),
        ("c.docx", "doc"),
        ("d.xlsx", "xls"),
        ("e.zip", "stream"),
    ):
        f = tmp_path / name
        f.write_bytes(b"bytes")
        cap = _MediaInvoke()
        monkeypatch.setattr(_impl, "_invoke", cap)
        result = await _impl.upload_file_impl(str(f))
        req = cap.requests[0]
        assert req.uri == "/open-apis/im/v1/files"
        assert req.body["file_type"] == expected, name
        assert req.body["file_name"] == name
        assert isinstance(req.body["file"], io.IOBase)
        assert "duration" not in req.body  # only sent when a real length is given
        assert result["file_key"] == "file_v3_1"
        assert result["file_type"] == expected


@pytest.mark.asyncio
async def test_upload_file_passes_duration_and_rejects_bad_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "voice.opus"
    f.write_bytes(b"opus")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.upload_file_impl(str(f), duration_ms=3200)
    assert cap.requests[0].body["duration"] == 3200
    bad = await _impl.upload_file_impl(str(f), file_type="mp3")
    assert bad["ok"] is False
    assert "file_type must be one of" in bad["message"]


@pytest.mark.asyncio
async def test_send_image_message_uploads_then_sends(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "shot.png"
    f.write_bytes(b"png")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.send_media_message_impl("oc_1", str(f), "image")
    assert [r.uri for r in cap.requests] == ["/open-apis/im/v1/images", "/open-apis/im/v1/messages"]
    send = cap.requests[1]
    assert send.body["msg_type"] == "image"
    # A picture message carries image_key; using file_key here is Feishu error 230001
    assert json.loads(send.body["content"]) == {"image_key": "img_v3_1"}
    assert result["message_id"] == "om_new"
    assert result["image_key"] == "img_v3_1"


@pytest.mark.asyncio
async def test_send_file_audio_video_use_file_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for kind, name, forced in (
        ("file", "report.pdf", "pdf"),
        ("audio", "v.opus", "opus"),
        ("media", "clip.mp4", "mp4"),
    ):
        f = tmp_path / name
        f.write_bytes(b"bytes")
        cap = _MediaInvoke()
        monkeypatch.setattr(_impl, "_invoke", cap)
        result = await _impl.send_media_message_impl("oc_1", str(f), kind)
        assert [r.uri for r in cap.requests] == ["/open-apis/im/v1/files", "/open-apis/im/v1/messages"]
        # audio/video force the enum Feishu requires rather than trusting the extension
        assert cap.requests[0].body["file_type"] == forced, kind
        send = cap.requests[1]
        assert send.body["msg_type"] == kind
        assert json.loads(send.body["content"]) == {"file_key": "file_v3_1"}
        assert result["msg_type"] == kind


@pytest.mark.asyncio
async def test_send_video_uploads_cover_as_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.send_media_message_impl("oc_1", str(video), "media", cover_image_path=str(cover))
    assert [r.uri for r in cap.requests] == [
        "/open-apis/im/v1/files",
        "/open-apis/im/v1/images",
        "/open-apis/im/v1/messages",
    ]
    assert json.loads(cap.requests[2].body["content"]) == {"file_key": "file_v3_1", "image_key": "img_v3_1"}
    assert result["cover_image_key"] == "img_v3_1"


@pytest.mark.asyncio
async def test_send_video_survives_failed_cover(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    # The video is uploaded and sendable; a cover that can't be read must not lose it
    result = await _impl.send_media_message_impl("oc_1", str(video), "media", cover_image_path=str(tmp_path / "no.png"))
    assert result["ok"] is True
    assert json.loads(cap.requests[-1].body["content"]) == {"file_key": "file_v3_1"}
    assert "cover_image_key" not in result


@pytest.mark.asyncio
async def test_send_media_infers_receive_id_type_and_rejects_bad_msg_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "a.png"
    f.write_bytes(b"png")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    await _impl.send_media_message_impl("ou_person", str(f), "image")
    assert _qdict(cap.requests[1])["receive_id_type"] == "open_id"
    bad = await _impl.send_media_message_impl("oc_1", str(f), "sticker")
    assert bad["ok"] is False
    assert "msg_type must be one of" in bad["message"]


@pytest.mark.asyncio
async def test_send_media_returns_upload_failure_without_sending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    result = await _impl.send_media_message_impl("oc_1", str(tmp_path / "gone.png"), "image")
    assert result["ok"] is False
    assert cap.requests == []  # nothing sent when there is nothing uploaded


@pytest.mark.asyncio
async def test_upload_tools_return_keys_without_sending(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = importlib.import_module("feishu_message")
    img = tmp_path / "chart.png"
    img.write_bytes(b"png")
    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"pdf")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)

    image_out = json.loads(await mod.feishu_message_upload_image(image_path=str(img)))
    assert image_out["image_key"] == "img_v3_1"
    file_out = json.loads(await mod.feishu_message_upload_file(file_path=str(doc)))
    assert file_out["file_key"] == "file_v3_1"
    assert file_out["file_type"] == "pdf"  # derived from the suffix, not Feishu's enum by hand
    # Uploading is deliberately *not* sending: only the two upload endpoints were called.
    assert [r.uri for r in cap.requests] == ["/open-apis/im/v1/images", "/open-apis/im/v1/files"]
    for fn in (mod.feishu_message_upload_image, mod.feishu_message_upload_file):
        assert inspect.iscoroutinefunction(fn)


@pytest.mark.asyncio
async def test_upload_file_tool_passes_type_name_duration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = importlib.import_module("feishu_message")
    captured: dict[str, Any] = {}

    async def _fake(
        file_path: str, file_type: str = "", file_name: str = "", duration_ms: int = 0, user_key: str = ""
    ) -> dict[str, Any]:
        captured.update(
            file_path=file_path, file_type=file_type, file_name=file_name, duration_ms=duration_ms, user_key=user_key
        )
        return {"ok": True, "file_key": "file_v3_9"}

    monkeypatch.setattr(_impl, "upload_file_impl", _fake)
    out = await mod.feishu_message_upload_file(
        file_path="C:/tmp/voice.opus", file_type="opus", file_name="留言.opus", duration_ms=3200, user_key="ou_me"
    )
    assert json.loads(out)["file_key"] == "file_v3_9"
    assert captured == {
        "file_path": "C:/tmp/voice.opus",
        "file_type": "opus",
        "file_name": "留言.opus",
        "duration_ms": 3200,
        "user_key": "ou_me",
    }


@pytest.mark.asyncio
async def test_send_post_builds_paragraphs_and_uploads_images(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    img = tmp_path / "chart.png"
    img.write_bytes(b"png")
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    blocks = [
        {"tag": "text", "text": "本周进展", "style": ["bold"]},
        {"tag": "at", "user_id": "ou_z"},
        {"tag": "a", "text": "看板", "href": "https://example.com"},
        {"tag": "img", "image_path": str(img)},
        {"tag": "md", "text": "1. 一\n2. 二"},
        {"tag": "hr"},
    ]
    result = await _impl.send_post_message_impl("oc_1", json.dumps(blocks), title="周报")
    assert [r.uri for r in cap.requests] == ["/open-apis/im/v1/images", "/open-apis/im/v1/messages"]
    send = cap.requests[1]
    assert send.body["msg_type"] == "post"
    post = json.loads(send.body["content"])["zh_cn"]
    assert post["title"] == "周报"
    # Feishu requires img/hr/md to occupy their own paragraph; adjacent text/link/mention
    # nodes share one. Getting this wrong renders as a broken layout, so it is asserted.
    assert post["content"] == [
        [
            {"tag": "text", "text": "本周进展", "style": ["bold"]},
            {"tag": "at", "user_id": "ou_z"},
            {"tag": "a", "text": "看板", "href": "https://example.com"},
        ],
        [{"tag": "img", "image_key": "img_v3_1"}],
        [{"tag": "md", "text": "1. 一\n2. 二"}],
        [{"tag": "hr"}],
    ]
    assert result["uploaded_image_keys"] == ["img_v3_1"]
    assert result["blocks"] == 6


@pytest.mark.asyncio
async def test_send_post_accepts_existing_image_key_without_uploading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    blocks = [{"tag": "img", "image_key": "img_already"}]
    result = await _impl.send_post_message_impl("oc_1", json.dumps(blocks))
    assert [r.uri for r in cap.requests] == ["/open-apis/im/v1/messages"]
    assert json.loads(cap.requests[0].body["content"])["zh_cn"]["content"] == [
        [{"tag": "img", "image_key": "img_already"}]
    ]
    assert result["uploaded_image_keys"] == []


@pytest.mark.asyncio
async def test_send_post_reports_the_offending_block(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _MediaInvoke()
    monkeypatch.setattr(_impl, "_invoke", cap)
    cases = [
        ("{oops", "not valid JSON"),
        ("[]", "non-empty JSON array"),
        ('[{"tag":"nope","text":"x"}]', "unsupported tag"),
        ('[{"tag":"a","text":"x"}]', "needs href"),
        ('[{"tag":"at"}]', "needs user_id"),
        ('[{"tag":"img"}]', "needs image_path or image_key"),
        ('[{"tag":"text"}]', "needs non-empty text"),
        ("[1]", "not a JSON object"),
    ]
    for payload, needle in cases:
        result = await _impl.send_post_message_impl("oc_1", payload)
        assert result["ok"] is False, payload
        assert needle in result["message"], payload
    assert cap.requests == []  # a malformed block never becomes a half-sent message


@pytest.mark.asyncio
async def test_media_tools_return_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = importlib.import_module("feishu_message")
    png = tmp_path / "a.png"
    png.write_bytes(b"png")
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"mp4")
    opus = tmp_path / "a.opus"
    opus.write_bytes(b"opus")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"pdf")
    for call in (
        mod.feishu_message_send_image("oc_1", str(png)),
        mod.feishu_message_send_file("oc_1", str(pdf)),
        mod.feishu_message_send_audio("oc_1", str(opus), duration_ms=1000),
        mod.feishu_message_send_video("oc_1", str(mp4)),
        mod.feishu_message_send_post("oc_1", '[{"tag":"text","text":"hi"}]'),
    ):
        monkeypatch.setattr(_impl, "_invoke", _MediaInvoke())
        assert json.loads(await call)["message_id"] == "om_new"


# ── Read status (已读 / 未读) ───────────────────────────────────────────────────


class _SequencedInvoke:
    """Replace _invoke; answer each call from a queue, recording every request.

    Read-status makes several *different* calls (read_users pages, then the message,
    then the roster), so a single canned reply can't drive it.
    """

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.requests: list[Any] = []
        self.user_keys: list[Any] = []

    async def __call__(
        self,
        request: Any,
        user_key: str | None = None,
        prefer: str = "tenant",
        identity: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        self.requests.append(request() if callable(request) else request)
        self.user_keys.append(user_key)
        reply = self.replies.pop(0) if self.replies else {"ok": True, "code": 0, "msg": "", "data": {}}
        return {"ok": True, "code": 0, "msg": "", **reply} if "ok" not in reply else reply


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "code": 0, "msg": "", "data": data}


@pytest.mark.asyncio
async def test_read_status_builds_get_request_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    seq = _SequencedInvoke(
        [
            _ok({"items": [{"user_id": "ou_a", "timestamp": "1699"}], "has_more": True, "page_token": "pt2"}),
            _ok({"items": [{"user_id": "ou_b", "timestamp": "1700"}], "has_more": False}),
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.read_status_impl("om_abc", include_unread=False, user_key="ou_me")
    req = seq.requests[0]
    assert req.http_method.name == "GET"
    assert req.uri == "/open-apis/im/v1/messages/:message_id/read_users"
    assert req.paths["message_id"] == "om_abc"
    q = _qdict(req)
    assert q.get("user_id_type") == "open_id"
    assert q.get("page_size") == "100"
    # the second page must carry the token from the first
    assert _qdict(seq.requests[1]).get("page_token") == "pt2"
    assert seq.user_keys[0] == "ou_me"
    assert result["read_count"] == 2
    assert result["read_users"] == [
        {"open_id": "ou_a", "read_time": "1699"},
        {"open_id": "ou_b", "read_time": "1700"},
    ]
    # include_unread=False must not spend calls on the message + roster
    assert len(seq.requests) == 2
    assert "unread_users" not in result


@pytest.mark.asyncio
async def test_read_status_computes_unread_from_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    # Feishu has no unread endpoint: unread = roster - readers - sender.
    seq = _SequencedInvoke(
        [
            _ok({"items": [{"user_id": "ou_a", "timestamp": "1699"}], "has_more": False}),
            _ok({"items": [{"chat_id": "oc_1", "sender": {"id": "ou_bot"}}]}),
            _ok(
                {
                    "items": [
                        {"member_id": "ou_a", "name": "读了的"},
                        {"member_id": "ou_c", "name": "没读的"},
                        {"member_id": "ou_bot", "name": "机器人"},
                    ],
                    "has_more": False,
                }
            ),
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.read_status_impl("om_abc")
    assert result["read_count"] == 1
    assert result["chat_id"] == "oc_1"
    # the sender is excluded from unread, and so is the reader
    assert result["unread_users"] == [{"open_id": "ou_c", "name": "没读的"}]
    assert result["unread_count"] == 1
    assert result["member_count"] == 3


@pytest.mark.asyncio
async def test_read_status_keeps_read_list_when_roster_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # The unread half is best-effort: losing it must not lose the read list.
    seq = _SequencedInvoke(
        [
            _ok({"items": [{"user_id": "ou_a", "timestamp": "1"}], "has_more": False}),
            {"ok": False, "code": 230110, "msg": "deleted", "message": "err"},
        ]
    )
    monkeypatch.setattr(_impl, "_invoke", seq)
    result = await _impl.read_status_impl("om_abc")
    assert result["ok"] is True
    assert result["read_count"] == 1
    assert "unread_users" not in result
    assert "未读名单" in result["note"]


@pytest.mark.asyncio
async def test_read_status_hints_own_message_and_seven_day_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    for code, expect in ((230012, "自己发出的消息"), (230033, "7 天")):

        async def _fail(*a: Any, code: int = code, **k: Any) -> dict[str, Any]:
            return {"ok": False, "code": code, "msg": "nope", "message": "err"}

        monkeypatch.setattr(_impl, "_invoke", _fail)
        result = await _impl.read_status_impl("om_abc")
        assert result["ok"] is False
        assert expect in result["hint"], code


@pytest.mark.asyncio
async def test_read_status_rejects_non_message_id() -> None:
    for bad in ("", "  ", "oc_1", "ou_2"):
        result = await _impl.read_status_impl(bad)
        assert result["ok"] is False
        assert "message_id" in result["message"]


# ── Pin / unpin (置顶) ──────────────────────────────────────────────────────────


# ── Forward / merge-forward (转发) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_pin_forward_tools_return_json(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module("feishu_message")
    monkeypatch.setattr(_impl, "read_status_impl", lambda *a, **k: _async({"ok": True, "read_count": 3}))
    assert json.loads(await mod.feishu_message_read_status("om_1"))["read_count"] == 3


async def _async(value: dict[str, Any]) -> dict[str, Any]:
    return value
