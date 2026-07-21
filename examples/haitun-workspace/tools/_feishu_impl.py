"""Private helper for the Feishu tools — authenticated client + request execution.

Wraps the ``lark_channel`` SDK (already a project dependency): builds one
authenticated ``Client`` from ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``,
caches it module-level, and runs ``BaseRequest`` objects through the SDK's native
async ``arequest``. Drive-comment requests reuse the SDK's ready-made builders;
docx/doc/sheet raw-content and create-reply requests are hand-built the same way
the SDK's own ``api/drive/comment.py`` does it.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
from typing import Any

import anyio
from lark_channel.api.drive import comment as _comment
from lark_channel.api.wiki import node as _wiki_node
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

_client: Any = None


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def _config() -> tuple[str, str] | None:
    app_id = os.environ.get("PSI_FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("PSI_FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None
    return app_id, app_secret


def _reset_client() -> None:
    global _client
    _client = None


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    creds = _config()
    if creds is None:
        return None
    from lark_channel.client import Client  # noqa: PLC0415

    app_id, app_secret = creds
    _client = Client.builder().app_id(app_id).app_secret(app_secret).build()
    return _client


async def _invoke(request: Any, user_key: str | None = None) -> dict[str, Any]:
    """Send a BaseRequest. If a user identity is available (``user_key`` passed, or a
    single cached user as fallback), send it as that user (user_access_token) instead
    of the bot's tenant token — needed for APIs that act on behalf of a user (reading
    a wiki the user owns, listing the user's knowledge bases, etc.). If no user
    identity is available, use the bot's tenant token.
    """
    eff = _effective_user_key(user_key)
    if eff:
        return await _invoke_as_user(request, eff)
    client = _get_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    try:
        resp = await client.arequest(request)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")
    return _resp_to_result(resp)


async def _invoke_as_user(request: Any, user_key: str) -> dict[str, Any]:
    """Send a BaseRequest with the user's UAT (resolved by user_key)."""
    client = _get_uat_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _error("Not authorized. Call feishu_auth_start then feishu_auth_complete first.", need_auth=True)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(request, option)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")
    return _resp_to_result(resp)


def _resp_to_result(resp: Any) -> dict[str, Any]:
    code = getattr(resp, "code", None)
    msg = getattr(resp, "msg", "") or ""
    data: dict[str, Any] = {}
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        try:
            body = json.loads(bytes(content).decode("utf-8"))
            if isinstance(body, dict):
                data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
                if code is None:
                    code = body.get("code")
                if not msg:
                    msg = body.get("msg", "") or ""
        except ValueError, UnicodeDecodeError:
            pass

    ok = code == 0
    if not ok:
        return {
            "ok": False,
            "code": code,
            "msg": msg,
            "data": data,
            "message": f"Feishu API error {code}: {msg}",
        }
    return {"ok": True, "code": 0, "msg": msg, "data": data}


async def add_comment_impl(file_token: str, file_type: str, content: str, user_key: str = "") -> dict[str, Any]:
    req = _comment.build_comment_create_request(file_token=file_token, file_type=file_type, content=content)
    return await _invoke(req, user_key=user_key)


async def list_comments_impl(file_token: str, file_type: str, page_size: int, page_token: str) -> dict[str, Any]:
    req = _comment.build_comment_list_request(
        file_token=file_token,
        file_type=file_type,
        page_size=page_size,
        page_token=page_token or None,
        is_whole="true",
    )
    return await _invoke(req)


async def list_comment_replies_impl(
    file_token: str, file_type: str, comment_id: str, page_size: int, page_token: str
) -> dict[str, Any]:
    req = _comment.build_comment_reply_list_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        page_size=page_size,
        page_token=page_token or None,
    )
    return await _invoke(req)


def _build_reply_create_request(
    *, file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies"
    req.paths["file_token"] = file_token
    req.paths["comment_id"] = comment_id
    req.add_query("file_type", file_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    elements: list[dict[str, Any]] = []
    if at_user_id:
        elements.append({"type": "person", "person": {"user_id": at_user_id}})
    elements.append({"type": "text_run", "text_run": {"text": content}})
    req.body = {"content": {"elements": elements}}
    return req


async def reply_comment_impl(
    file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str, user_key: str = ""
) -> dict[str, Any]:
    req = _build_reply_create_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        content=content,
        at_user_id=at_user_id,
    )
    return await _invoke(req, user_key=user_key)


def _raw_get(uri: str, path_name: str, path_value: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = uri
    req.paths[path_name] = path_value
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_docx_raw_request(document_id: str) -> BaseRequest:
    return _raw_get("/open-apis/docx/v1/documents/:document_id/raw_content", "document_id", document_id)


def _build_doc_raw_request(doc_token: str) -> BaseRequest:
    return _raw_get("/open-apis/doc/v2/:doc_token/raw_content", "doc_token", doc_token)


def _build_sheet_meta_request(spreadsheet_token: str) -> BaseRequest:
    return _raw_get(
        "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "spreadsheet_token",
        spreadsheet_token,
    )


def _build_sheet_values_request(spreadsheet_token: str, range_: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.paths["range"] = range_
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _sheet_values_to_text(data: dict[str, Any]) -> str:
    grid = data.get("valueRange", {}).get("values", []) if isinstance(data, dict) else []
    lines: list[str] = []
    for row in grid if isinstance(grid, list) else []:
        cells = [("" if c is None else str(c)) for c in (row if isinstance(row, list) else [])]
        lines.append("\t".join(cells))
    return "\n".join(lines)


async def _read_sheet(token: str) -> dict[str, Any]:
    meta = await _invoke(_build_sheet_meta_request(token))
    if not meta["ok"]:
        return meta
    sheets = meta["data"].get("sheets", [])
    parts: list[str] = []
    for sh in sheets if isinstance(sheets, list) else []:
        sheet_id = sh.get("sheet_id") or sh.get("sheetId")
        title = sh.get("title", "")
        if not sheet_id:
            continue
        values = await _invoke(_build_sheet_values_request(token, str(sheet_id)))
        if not values["ok"]:
            return values
        parts.append(f"# {title}\n{_sheet_values_to_text(values['data'])}")
    return {"ok": True, "content": "\n\n".join(parts)}


async def read_doc_impl(file_type: str, token: str, max_chars: int) -> dict[str, Any]:
    ft = file_type.strip().lower()
    if ft == "docx":
        res = await _invoke(_build_docx_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "doc":
        res = await _invoke(_build_doc_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "sheet":
        res = await _read_sheet(token)
        content = res.get("content", "") if res["ok"] else ""
    else:
        return _error(f"Unsupported file_type {file_type!r}. Use one of: docx, doc, sheet.")

    if not res["ok"]:
        return res

    truncated = False
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    return {
        "ok": True,
        "file_type": ft,
        "token": token,
        "content": content,
        "truncated": truncated,
    }


# ── IM (messaging) — find chat, send, reply-in-thread, list messages ──────────
#
# These power the "daily todo topic" schedules: find the main group by name,
# post a topic root message, reply-in-thread to form a native Feishu thread, and
# read the thread's replies. All use bot/tenant credentials (no user token).


def _build_chat_search_request(query: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats/search"
    req.add_query("query", query)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def find_chat_impl(name: str, exact: bool, page_size: int = 50, page_token: str = "") -> dict[str, Any]:
    """Search groups the bot is in by name. Returns candidates [{chat_id, name, description}]."""
    res = await _invoke(_build_chat_search_request(name, page_size, page_token))
    if not res["ok"]:
        return res
    items = res["data"].get("items", []) if isinstance(res["data"], dict) else []
    matches = [
        {"chat_id": it.get("chat_id", ""), "name": it.get("name", ""), "description": it.get("description", "")}
        for it in (items if isinstance(items, list) else [])
    ]
    if exact:
        matches = [m for m in matches if m["name"] == name]
    return {
        "ok": True,
        "query": name,
        "exact": exact,
        "matches": matches,
        "count": len(matches),
        "has_more": bool(res["data"].get("has_more")) if isinstance(res["data"], dict) else False,
        "page_token": res["data"].get("page_token", "") if isinstance(res["data"], dict) else "",
    }


def _build_send_message_request(receive_id: str, receive_id_type: str, msg_type: str, content: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages"
    req.add_query("receive_id_type", receive_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": content,
    }
    return req


async def send_message_impl(receive_id: str, text: str, receive_id_type: str) -> dict[str, Any]:
    """Send a text message to a chat/user. Returns message_id + thread_id (thread_id is the topic root)."""
    content = json.dumps({"text": text}, ensure_ascii=False)
    res = await _invoke(_build_send_message_request(receive_id, receive_id_type, "text", content))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
    }


def _build_reply_message_request(message_id: str, text: str, reply_in_thread: bool) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages/:message_id/reply"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "msg_type": "text",
        "reply_in_thread": reply_in_thread,
    }
    return req


async def reply_message_impl(message_id: str, text: str, reply_in_thread: bool) -> dict[str, Any]:
    """Reply to a message. reply_in_thread=True forms/continues a native Feishu thread (topic)."""
    res = await _invoke(_build_reply_message_request(message_id, text, reply_in_thread))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
    }


def _build_list_messages_request(
    container_id: str, container_id_type: str, sort_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages"
    req.add_query("container_id_type", container_id_type)
    req.add_query("container_id", container_id)
    req.add_query("sort_type", sort_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_messages_impl(
    container_id: str,
    container_id_type: str,
    sort_type: str,
    page_size: int,
    page_token: str,
) -> dict[str, Any]:
    """List messages in a chat or thread. Use container_id_type='thread' + a thread_id to read a topic's replies."""
    res = await _invoke(_build_list_messages_request(container_id, container_id_type, sort_type, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "items": data.get("items", []),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


def _extract_post_text(node: Any) -> str:
    """Recursively collect all 'text' values from a post rich-text content tree."""
    parts: list[str] = []
    if isinstance(node, dict):
        if node.get("tag") == "text" and isinstance(node.get("text"), str):
            parts.append(node["text"])
        for v in node.values():
            if isinstance(v, (dict, list)):
                parts.append(_extract_post_text(v))
    elif isinstance(node, list):
        for v in node:
            parts.append(_extract_post_text(v))
    return " ".join(p for p in parts if p)


def _message_plain_text(item: dict[str, Any]) -> str:
    """Best-effort plain text of a message item (handles text and post; others -> '')."""
    if item.get("deleted"):
        return ""
    body = item.get("body", {}) if isinstance(item.get("body"), dict) else {}
    raw = body.get("content", "")
    if not raw:
        return ""
    try:
        content = json.loads(raw)
    except ValueError, TypeError:
        return raw if isinstance(raw, str) else ""
    if not isinstance(content, dict):
        return ""
    if "text" in content and isinstance(content["text"], str):
        return content["text"]
    return _extract_post_text(content)  # post / rich text


async def read_thread_impl(thread_id: str, page_size: int = 50) -> dict[str, Any]:
    """Read a topic thread and return cleaned messages: [{message_id, sender_open_id, name?, text}]."""
    messages: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _invoke(_build_list_messages_request(thread_id, "thread", "ByCreateTimeAsc", page_size, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            sender = it.get("sender", {}) if isinstance(it.get("sender"), dict) else {}
            is_user = sender.get("sender_type") == "user"
            messages.append(
                {
                    "message_id": it.get("message_id", ""),
                    "sender_open_id": sender.get("id", "") if is_user else "",
                    "sender_type": sender.get("sender_type", ""),
                    "create_time": it.get("create_time", ""),
                    "text": _message_plain_text(it),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {"ok": True, "thread_id": thread_id, "messages": messages, "count": len(messages)}


# ── Contact — resolve a member's user id (open_id) by name via chat roster ────
#
# Feishu tenant tokens cannot search all users by name; the supported path is to
# list a group's members (each item has name + member_id) and match by name.
# This resolves the "@ a specific person" need — the target is a group member.


def _build_chat_members_request(chat_id: str, member_id_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats/:chat_id/members"
    req.paths["chat_id"] = chat_id
    req.add_query("member_id_type", member_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def find_member_id_impl(
    chat_id: str,
    name: str,
    exact: bool,
    member_id_type: str = "open_id",
) -> dict[str, Any]:
    """Resolve a group member's id by name. Pages through the full roster and matches by name.

    Returns matches [{name, id, member_id_type}]. ``name`` empty returns the whole roster.
    """
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _invoke(_build_chat_members_request(chat_id, member_id_type, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "name": it.get("name", ""),
                    "id": it.get("member_id", ""),
                    "member_id_type": it.get("member_id_type", member_id_type),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break

    if not name:
        matches = members
    elif exact:
        matches = [m for m in members if m["name"] == name]
    else:
        matches = [m for m in members if name in m["name"]]
    return {
        "ok": True,
        "chat_id": chat_id,
        "query": name,
        "exact": exact,
        "matches": matches,
        "count": len(matches),
        "member_total": len(members),
    }


# ── Approval (审批) — list pending tasks, read instance, approve/reject ────────
#
# Lets the agent read an approval application's form content and decide whether
# to approve or reject it. Feishu requires approve/reject to carry the APPROVER's
# own user_id — the bot acts on behalf of a real approver (the action is recorded
# under that person). All endpoints work with bot/tenant credentials.

_APPROVAL_TASK_STATUS = {1: "待办", 2: "已办", 17: "未读", 18: "已读", 33: "处理中", 34: "撤回"}
_APPROVAL_INSTANCE_STATUS = {0: "none", 1: "running", 2: "approved", 3: "rejected", 4: "revoked", 5: "terminated"}


def _build_task_query_request(
    user_id: str, topic: str, user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/tasks/query"
    req.add_query("user_id", user_id)
    req.add_query("topic", topic)
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_approval_tasks_impl(
    user_id: str,
    topic: str = "1",
    user_id_type: str = "open_id",
    page_size: int = 100,
    page_token: str = "",
) -> dict[str, Any]:
    """List a user's approval tasks. topic '1' = pending (待办). Returns task summaries + pagination."""
    res = await _invoke(_build_task_query_request(user_id, topic, user_id_type, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    tasks = [
        {
            "task_id": t.get("task_id", ""),
            "instance_code": t.get("process_id", ""),
            "approval_code": t.get("definition_code", "") or t.get("process_code", ""),
            "title": t.get("title", ""),
            "status": _APPROVAL_TASK_STATUS.get(t.get("status"), t.get("status")),
            "process_status": _APPROVAL_INSTANCE_STATUS.get(t.get("process_status"), t.get("process_status")),
            "initiators": t.get("initiator_names", []),
        }
        for t in (data.get("tasks", []) if isinstance(data.get("tasks"), list) else [])
    ]
    return {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


def _build_instance_get_request(instance_id: str, user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/instances/:instance_id"
    req.paths["instance_id"] = instance_id
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _parse_approval_attachments(form: Any) -> list[dict[str, Any]]:
    """Pull downloadable attachments out of an approval form.

    The ``form`` field is a JSON string of widget objects. File/image widgets
    (attachmentV2/image/imageV2/…) carry **direct URLs** in their ``value`` —
    these are valid only ~12 hours, so download them promptly. Only ``document``
    widgets return a drive token instead of a URL.
    """
    widgets: Any = form
    if isinstance(form, str):
        with contextlib.suppress(ValueError):
            widgets = json.loads(form)
    if not isinstance(widgets, list):
        return []
    attachments: list[dict[str, Any]] = []
    for w in widgets:
        if not isinstance(w, dict):
            continue
        wtype = str(w.get("type", "")).lower()
        name = w.get("name", "") or w.get("id", "")
        value = w.get("value")
        if "document" in wtype:
            for tok in value if isinstance(value, list) else [value]:
                if tok:
                    attachments.append({"name": name, "type": w.get("type", ""), "kind": "drive", "value": tok})
        elif any(k in wtype for k in ("attachment", "image", "file")):
            for v in value if isinstance(value, list) else [value]:
                if v:
                    attachments.append({"name": name, "type": w.get("type", ""), "kind": "url", "value": v})
    return attachments


async def get_approval_instance_impl(instance_id: str, user_id_type: str = "open_id") -> dict[str, Any]:
    """Read an approval instance's detail — applicant, status, the submitted form, and task_list."""
    res = await _invoke(_build_instance_get_request(instance_id, user_id_type))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    form = data.get("form", "")
    return {
        "ok": True,
        "instance_code": instance_id,
        "approval_code": data.get("approval_code", ""),
        "approval_name": data.get("approval_name", ""),
        "status": data.get("status", ""),
        "applicant": data.get("user_id", "") or data.get("open_id", ""),
        "form": form,
        "attachments": _parse_approval_attachments(form),
        "task_list": data.get("task_list", []),
        "timeline": data.get("timeline", []),
    }


def _build_list_instances_request(
    approval_code: str, start_time: str, end_time: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/instances"
    req.add_query("approval_code", approval_code)
    if start_time:
        req.add_query("start_time", start_time)
    if end_time:
        req.add_query("end_time", end_time)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_approval_instances_impl(approval_code: str, start_time: str = "", end_time: str = "") -> dict[str, Any]:
    """List all instance codes for an approval definition in a time window (Unix ms strings).

    Defaults to the last 30 days when start/end omitted. Pages through everything and
    returns ``instance_codes`` to feed into ``get_approval_instance_impl`` one by one.
    """
    if not approval_code:
        return _error("approval_code is required (the approval definition code).")
    if not start_time or not end_time:
        import time  # noqa: PLC0415

        now_ms = int(time.time() * 1000)
        end_time = end_time or str(now_ms)
        start_time = start_time or str(now_ms - 30 * 24 * 3600 * 1000)
    codes: list[str] = []
    page_token = ""
    while True:
        res = await _invoke(_build_list_instances_request(approval_code, start_time, end_time, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        chunk = data.get("instance_code_list", [])
        if isinstance(chunk, list):
            codes.extend(str(c) for c in chunk)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {
        "ok": True,
        "approval_code": approval_code,
        "start_time": start_time,
        "end_time": end_time,
        "instance_codes": codes,
        "count": len(codes),
    }


def _build_task_action_request(
    action: str, approval_code: str, instance_code: str, user_id: str, task_id: str, comment: str, user_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = f"/open-apis/approval/v4/tasks/{action}"
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {
        "approval_code": approval_code,
        "instance_code": instance_code,
        "user_id": user_id,
        "task_id": task_id,
    }
    if comment:
        body["comment"] = comment
    req.body = body
    return req


async def decide_approval_task_impl(
    approve: bool,
    approval_code: str,
    instance_code: str,
    approver_user_id: str,
    task_id: str,
    comment: str = "",
    user_id_type: str = "open_id",
) -> dict[str, Any]:
    """Approve or reject a task on behalf of ``approver_user_id``. approve=True -> approve, else reject."""
    action = "approve" if approve else "reject"
    res = await _invoke(
        _build_task_action_request(
            action, approval_code, instance_code, approver_user_id, task_id, comment, user_id_type
        )
    )
    if not res["ok"]:
        return res
    return {"ok": True, "action": action, "instance_code": instance_code, "task_id": task_id}


# ── Wiki — resolve a wiki node token to its underlying document ───────────────
#
# A Feishu wiki URL (.../wiki/<node_token>) is a shell; the real content lives in
# an underlying docx/sheet/bitable/... This resolves the node token to obj_token
# + obj_type so the agent can then read it (docx/doc/sheet via read_doc_impl).


async def get_wiki_node_impl(token: str, user_key: str = "") -> dict[str, Any]:
    """Resolve a wiki node token to its underlying document (obj_token + obj_type).

    Pass ``user_key`` to resolve as that user (needed when the wiki is user-owned and
    the bot isn't a member); empty uses the bot's tenant token.
    """
    res = await _invoke(_wiki_node.build_wiki_node_get_request(token=token), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    node = data.get("node", {}) if isinstance(data.get("node"), dict) else {}
    return {
        "ok": True,
        "node_token": node.get("node_token", ""),
        "obj_token": node.get("obj_token", ""),
        "obj_type": node.get("obj_type", ""),
        "title": node.get("title", ""),
        "space_id": node.get("space_id", ""),
        "has_child": bool(node.get("has_child")),
    }


# ── Start a group topic with @-mentions ──────────────────────────────────────
#
# Text messages' <at> tags do NOT render as real mentions for bots (Feishu shows
# the raw tag). Real mentions require the "post" rich-text message type, whose
# `at` element ({"tag":"at","user_id":...}) does render. So when mentions are
# requested we send a post; with no mentions we keep a plain text message.


def _build_post_at_content(text: str, at_open_ids: list[str], at_all: bool) -> str:
    """Build a post rich-text content JSON string: leading @ elements, then the text run."""
    line: list[dict[str, Any]] = []
    if at_all:
        line.append({"tag": "at", "user_id": "all"})
    line.extend({"tag": "at", "user_id": oid} for oid in at_open_ids if oid)
    # separate mentions from the message with a space, then the text
    line.append({"tag": "text", "text": f" {text}" if line else text})
    return json.dumps({"zh_cn": {"title": "", "content": [line]}}, ensure_ascii=False)


async def start_topic_impl(
    chat_id: str,
    text: str,
    at_open_ids: list[str] | None = None,
    at_all: bool = False,
) -> dict[str, Any]:
    """Post a topic root message to a group, @-mentioning the given open_ids (and/or everyone).

    Uses a post rich-text message when mentions are requested (so @ renders), a
    plain text message otherwise. Returns message_id + thread_id (the topic root).
    """
    ids = at_open_ids or []
    if ids or at_all:
        content = _build_post_at_content(text, ids, at_all)
        req = _build_send_message_request(chat_id, "chat_id", "post", content)
    else:
        content = json.dumps({"text": text}, ensure_ascii=False)
        req = _build_send_message_request(chat_id, "chat_id", "text", content)
    res = await _invoke(req)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", "") or chat_id,
    }


# ── Document search (needs user_access_token) ────────────────────────────────
#
# Feishu's doc search (/suite/docs-api/search/object) only accepts a
# user_access_token (UAT), not the bot's tenant token — it returns docs the
# authorizing USER can see. We use the SDK's device-flow OAuth to obtain/refresh
# a UAT, cache it in <workspace>/.psi/feishu/uat.json (plaintext — dev use), and
# call the search endpoint with a hand-built BaseRequest carrying the UAT.

_UAT_USER_KEY = "default"  # fallback key when a caller does not pass user_key
_token_store: Any = None
_uat_client: Any = None
_DEFAULT_SCOPES = "docs:doc:readonly drive:drive:readonly offline_access"


def _norm_user_key(user_key: str = "") -> str:
    """Normalize a per-user UAT key. Empty falls back to the shared 'default'.

    Callers pass the message sender's ``open_id`` (from the injected
    ``<feishu_context>``) so each user's authorization is isolated in the token
    store. Single-user / local dev can leave it empty and share ``default``.
    """
    return user_key.strip() or _UAT_USER_KEY


def _uat_store_path() -> str:
    workspace = os.environ.get("WORKSPACE_DIR", "")
    base = pathlib.Path(workspace) if workspace else pathlib.Path(__file__).resolve().parents[1]
    d = base / ".psi" / "feishu"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "uat.json")


def _sole_cached_user_key() -> str:
    """If exactly one real user (an open_id, not the shared 'default') has a cached
    UAT, return its key; otherwise "". Enables a single-user fallback so read/write
    tools act as the authorized user even when the LLM forgets to pass user_key.
    """
    try:
        path = pathlib.Path(_uat_store_path())
        if not path.exists():
            return ""
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return ""
    if not isinstance(raw, dict):
        return ""
    keys = [k for k in raw if k and k != _UAT_USER_KEY]
    return keys[0] if len(keys) == 1 else ""


def _effective_user_key(user_key: str | None = "") -> str:
    """The user_key to actually use: the caller's value if given, else the single
    cached user (single-user fallback). Empty means 'no user identity → tenant token'.
    """
    if user_key and user_key.strip():
        return user_key.strip()
    return _sole_cached_user_key()


def _pending_auth_path(user_key: str = "") -> str:
    """Per-user pending-auth file so concurrent authorizations don't clobber each other."""
    key = _norm_user_key(user_key)
    # Keep filenames filesystem-safe: only allow word chars + dash, replace the
    # rest (incl. path separators and dots, so a crafted open_id can't traverse).
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", key)
    return str(pathlib.Path(_uat_store_path()).parent / f"pending_auth_{safe}.json")


def _get_token_store() -> Any:
    global _token_store
    if _token_store is None:
        from lark_channel.channel.auth.token_store import FileTokenStore  # noqa: PLC0415

        _token_store = FileTokenStore(_uat_store_path())
    return _token_store


def _get_uat_client() -> Any:
    """A client built with enable_set_token(True) so we can attach a UAT per request."""
    global _uat_client
    if _uat_client is not None:
        return _uat_client
    creds = _config()
    if creds is None:
        return None
    from lark_channel.client import Client  # noqa: PLC0415

    app_id, app_secret = creds
    _uat_client = Client.builder().app_id(app_id).app_secret(app_secret).enable_set_token(True).build()
    return _uat_client


def _reset_uat_state() -> None:
    global _token_store, _uat_client
    _token_store = None
    _uat_client = None


# Authorization-code flow endpoints (China/feishu.cn — the device flow's v2
# endpoint 404s here). Browser authorize on accounts.feishu.cn; token exchange
# and refresh on open.feishu.cn/authen/v1.
_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"
_REFRESH_URL = "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token"
_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"


def _redirect_uri() -> str:
    return os.environ.get("PSI_FEISHU_REDIRECT_URI", "").strip() or "http://localhost/"


def _extract_code(code_or_url: str) -> str:
    """Accept either a bare code or a full callback URL and return the code."""
    s = code_or_url.strip()
    if "code=" in s:
        from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

        qs = parse_qs(urlparse(s).query)
        if qs.get("code"):
            return qs["code"][0]
    return s


async def _post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=headers or {})
    with contextlib.suppress(ValueError):
        data = resp.json()
        if isinstance(data, dict):
            return data
    return {"code": resp.status_code, "msg": f"non-JSON response ({resp.status_code})"}


async def _get_app_access_token() -> str | None:
    creds = _config()
    if creds is None:
        return None
    app_id, app_secret = creds
    data = await _post_json(_APP_TOKEN_URL, {"app_id": app_id, "app_secret": app_secret})
    return data.get("app_access_token") if data.get("code") == 0 else None


def _uat_from_token_response(payload: dict[str, Any]) -> Any:
    import time  # noqa: PLC0415

    from lark_channel.channel.types import UAT  # noqa: PLC0415

    now = time.time()
    inner = payload.get("data")
    data: dict[str, Any] = inner if isinstance(inner, dict) else payload
    expires_in = int(data.get("expires_in") or 0)
    refresh_expires_in = int(data.get("refresh_expires_in") or 0)
    scope_str = data.get("scope") or ""
    return UAT(
        access_token=data.get("access_token") or "",
        refresh_token=data.get("refresh_token"),
        expires_at=now + expires_in if expires_in else None,
        refresh_expires_at=now + refresh_expires_in if refresh_expires_in else None,
        scopes=scope_str.split() if scope_str else [],
        open_id=data.get("open_id"),
        raw=data if isinstance(data, dict) else {},
    )


async def auth_start_impl(scopes: str = "", user_key: str = "") -> dict[str, Any]:
    """Build the browser authorize URL for the authorization-code flow."""
    creds = _config()
    if creds is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    from urllib.parse import urlencode  # noqa: PLC0415

    app_id, _ = creds
    scope_str = scopes or _DEFAULT_SCOPES
    state = os.urandom(8).hex()
    await anyio.Path(_pending_auth_path(user_key)).write_text(json.dumps({"state": state}), encoding="utf-8")
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": _redirect_uri(),
            "response_type": "code",
            "scope": scope_str,
            "state": state,
        }
    )
    return {
        "ok": True,
        "authorize_url": f"{_AUTHORIZE_URL}?{query}",
        "message": (
            "打开 authorize_url 并同意授权. 浏览器会跳转到 redirect_uri, 地址栏里带 ?code=XXX; "
            "把那个 code (或整段跳转后的网址) 交给 feishu_auth_complete."
        ),
    }


async def auth_complete_impl(code: str, user_key: str = "") -> dict[str, Any]:
    """Exchange the authorization code for a user_access_token and cache it."""
    if not code.strip():
        return _error("No code provided.")
    app_token = await _get_app_access_token()
    if app_token is None:
        return _error("Feishu app not configured or app_access_token fetch failed.")
    payload = await _post_json(
        _TOKEN_URL,
        {"grant_type": "authorization_code", "code": _extract_code(code)},
        headers={"Authorization": f"Bearer {app_token}"},
    )
    if payload.get("code") not in (0, None):
        return {
            "ok": False,
            "code": payload.get("code"),
            "msg": payload.get("msg", ""),
            "message": f"Token exchange failed: {payload.get('msg', '')}",
        }
    uat = _uat_from_token_response(payload)
    if not uat.access_token:
        return _error("Token exchange returned no access_token.")
    await _get_token_store().set(_norm_user_key(user_key), uat)
    with contextlib.suppress(OSError):
        await anyio.Path(_pending_auth_path(user_key)).unlink()
    return {
        "ok": True,
        "open_id": uat.open_id or "",
        "scopes": uat.scopes,
        "message": "授权成功, 已缓存 user_access_token.",
    }


async def _get_valid_uat(user_key: str = "") -> Any:
    """Return a non-expired UAT for ``user_key`` (refreshing if needed), or None.

    Empty ``user_key`` falls back to the single cached user (single-user setups),
    then to the shared 'default' slot.
    """
    from lark_channel.channel.auth.device_flow import uat_needs_refresh  # noqa: PLC0415

    key = _effective_user_key(user_key) or _norm_user_key(user_key)
    store = _get_token_store()
    uat = await store.get(key)
    if uat is None:
        return None
    if uat_needs_refresh(uat) and uat.refresh_token:
        app_token = await _get_app_access_token()
        if app_token is not None:
            payload = await _post_json(
                _REFRESH_URL,
                {"grant_type": "refresh_token", "refresh_token": uat.refresh_token},
                headers={"Authorization": f"Bearer {app_token}"},
            )
            if payload.get("code") in (0, None) and (payload.get("data") or payload).get("access_token"):
                uat = _uat_from_token_response(payload)
                await store.set(key, uat)
    return uat


def _build_doc_search_request(search_key: str, count: int, offset: int, docs_types: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/suite/docs-api/search/object"
    req.token_types = {AccessTokenType.USER}
    body: dict[str, Any] = {"search_key": search_key, "count": count, "offset": offset}
    if docs_types:
        body["docs_types"] = docs_types
    req.body = body
    return req


async def search_docs_impl(
    search_key: str, count: int, offset: int, docs_types: str, user_key: str = ""
) -> dict[str, Any]:
    """Search cloud docs by keyword (needs a user_access_token). Returns matched docs."""
    client = _get_uat_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _error("Not authorized. Call feishu_auth_start then feishu_auth_complete first.", need_auth=True)

    types_list = [t.strip() for t in docs_types.split(",") if t.strip()]
    req = _build_doc_search_request(search_key, count, offset, types_list)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(req, option)
    except Exception as exc:
        return _error(f"Feishu search failed: {type(exc).__name__}: {exc}")

    body = _parse_resp_body(resp)
    if body.get("code") not in (0, None):
        return {
            "ok": False,
            "code": body.get("code"),
            "msg": body.get("msg", ""),
            "message": f"Feishu API error {body.get('code')}: {body.get('msg', '')}",
        }
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    docs = [
        {
            "title": e.get("title", ""),
            "token": e.get("docs_token", ""),
            "obj_type": e.get("docs_type", ""),
            "owner_id": e.get("owner_id", ""),
        }
        for e in (data.get("docs_entities", []) if isinstance(data.get("docs_entities"), list) else [])
    ]
    return {
        "ok": True,
        "docs": docs,
        "count": len(docs),
        "has_more": bool(data.get("has_more")),
        "total": data.get("total", 0),
    }


def _build_wiki_space_create_request(name: str, description: str, open_sharing: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/wiki/v2/spaces"
    req.token_types = {AccessTokenType.USER}
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if description:
        body["description"] = description
    if open_sharing:
        body["open_sharing"] = open_sharing
    req.body = body
    return req


async def create_wiki_space_impl(
    name: str, description: str = "", open_sharing: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Create a new Feishu wiki space (knowledge base). Needs a user_access_token.

    Feishu's create-space API only accepts a UAT (not the bot's tenant token); the
    new space is owned by the authorizing user. Returns the new space_id + name.
    """
    client = _get_uat_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _error("Not authorized. Call feishu_auth_start then feishu_auth_complete first.", need_auth=True)

    sharing = open_sharing.strip()
    if sharing and sharing not in ("open", "closed"):
        return _error("open_sharing must be 'open' or 'closed' (or empty).")
    req = _build_wiki_space_create_request(name.strip(), description.strip(), sharing)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(req, option)
    except Exception as exc:
        return _error(f"Feishu create wiki space failed: {type(exc).__name__}: {exc}")

    body = _parse_resp_body(resp)
    if body.get("code") not in (0, None):
        return {
            "ok": False,
            "code": body.get("code"),
            "msg": body.get("msg", ""),
            "message": f"Feishu API error {body.get('code')}: {body.get('msg', '')}",
        }
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    space = data.get("space", {}) if isinstance(data.get("space"), dict) else {}
    space_id = space.get("space_id", "")
    return {
        "ok": True,
        "space_id": space_id,
        "name": space.get("name", name),
        "description": space.get("description", description),
        "url": f"{_DOC_BASE_URL}/wiki/settings/{space_id}" if space_id else "",
    }


def _parse_resp_body(resp: Any) -> dict[str, Any]:
    """Extract the JSON body dict from an SDK BaseResponse (raw.content bytes)."""
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            parsed = json.loads(bytes(content).decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
    code = getattr(resp, "code", None)
    return {"code": code, "msg": getattr(resp, "msg", "") or ""}


# ── Bitable (多维表格) — list tables, list/create records ─────────────────────
#
# Generic read/write for Feishu bases; the bot's tenant token can read+write
# records provided the app is a collaborator on the base (scope bitable:app).
# app_token is the segment in a feishu.cn/base/<app_token> URL (for wiki links,
# resolve via feishu_wiki_get_node — obj_token is the app_token when obj_type=bitable).


def _build_list_tables_request(app_token: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_tables_impl(app_token: str, page_size: int = 100, page_token: str = "") -> dict[str, Any]:
    """List the data tables in a bitable app. Returns [{table_id, name}]."""
    res = await _invoke(_build_list_tables_request(app_token, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    tables = [
        {"table_id": t.get("table_id", ""), "name": t.get("name", "")}
        for t in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "tables": tables,
        "count": len(tables),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


def _build_list_records_request(
    app_token: str, table_id: str, page_size: int, page_token: str, filter_: str, sort: str, field_names: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    if filter_:
        req.add_query("filter", filter_)
    if sort:
        req.add_query("sort", sort)
    if field_names:
        req.add_query("field_names", field_names)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_records_impl(
    app_token: str,
    table_id: str,
    page_size: int = 100,
    page_token: str = "",
    filter_: str = "",
    sort: str = "",
    field_names: str = "",
) -> dict[str, Any]:
    """List records in a bitable table. Returns [{record_id, fields}] + pagination."""
    res = await _invoke(
        _build_list_records_request(app_token, table_id, page_size, page_token, filter_, sort, field_names)
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    records = [
        {"record_id": r.get("record_id", ""), "fields": r.get("fields", {})}
        for r in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "records": records,
        "count": len(records),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
        "total": data.get("total", 0),
    }


def _build_create_record_request(app_token: str, table_id: str, fields: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"fields": fields}
    return req


async def create_bitable_record_impl(
    app_token: str, table_id: str, fields_json: str, user_key: str = ""
) -> dict[str, Any]:
    """Create one record in a bitable table. fields_json is a JSON object of {column: value}."""
    try:
        fields = json.loads(fields_json)
    except ValueError as exc:
        return _error(f"fields_json is not valid JSON: {exc}")
    if not isinstance(fields, dict):
        return _error("fields_json must be a JSON object mapping column names to values.")
    res = await _invoke(_build_create_record_request(app_token, table_id, fields), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    record = data.get("record", {}) if isinstance(data.get("record"), dict) else {}
    return {"ok": True, "record_id": record.get("record_id", ""), "fields": record.get("fields", {})}


def _build_batch_delete_records_request(app_token: str, table_id: str, record_ids: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_delete"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"records": record_ids}
    return req


async def delete_bitable_records_impl(
    app_token: str, table_id: str, record_ids: str, user_key: str = ""
) -> dict[str, Any]:
    """Delete records (rows) by id. record_ids is comma-separated; batches of 500."""
    ids = [r.strip() for r in record_ids.split(",") if r.strip()]
    if not ids:
        return _error("No record_ids provided (comma-separated record ids).")
    deleted = 0
    for i in range(0, len(ids), 500):
        batch = ids[i : i + 500]
        res = await _invoke(_build_batch_delete_records_request(app_token, table_id, batch), user_key=user_key)
        if not res["ok"]:
            return {**res, "deleted": deleted}
        deleted += len(batch)
    return {"ok": True, "deleted": deleted, "record_ids": ids}


async def clear_bitable_table_impl(app_token: str, table_id: str, user_key: str = "") -> dict[str, Any]:
    """Delete ALL records (rows) in a table — pages through every record, then batch-deletes."""
    ids: list[str] = []
    page_token = ""
    while True:
        res = await _invoke(
            _build_list_records_request(app_token, table_id, 500, page_token, "", "", ""), user_key=user_key
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for r in data.get("items", []) if isinstance(data.get("items"), list) else []:
            rid = r.get("record_id", "")
            if rid:
                ids.append(rid)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    if not ids:
        return {"ok": True, "deleted": 0, "note": "table already has no records"}
    deleted = 0
    for i in range(0, len(ids), 500):
        batch = ids[i : i + 500]
        res = await _invoke(_build_batch_delete_records_request(app_token, table_id, batch), user_key=user_key)
        if not res["ok"]:
            return {**res, "deleted": deleted}
        deleted += len(batch)
    return {"ok": True, "deleted": deleted}


_BITABLE_FIELD_TYPES = {
    1: "文本",
    2: "数字",
    3: "单选",
    4: "多选",
    5: "日期",
    7: "复选框",
    11: "人员",
    13: "电话",
    15: "超链接",
    17: "附件",
    18: "单向关联",
    20: "公式",
    21: "双向关联",
    22: "地理位置",
    23: "群组",
    1001: "创建时间",
    1002: "最后更新时间",
    1003: "创建人",
    1004: "修改人",
    1005: "自动编号",
}


def _build_list_fields_request(app_token: str, table_id: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_fields_impl(app_token: str, table_id: str) -> dict[str, Any]:
    """List a table's fields (columns). Returns [{field_id, name, type, is_primary}] for all fields."""
    fields: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _invoke(_build_list_fields_request(app_token, table_id, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for f in data.get("items", []) if isinstance(data.get("items"), list) else []:
            ftype = f.get("type")
            fields.append(
                {
                    "field_id": f.get("field_id", ""),
                    "name": f.get("field_name", ""),
                    "type": _BITABLE_FIELD_TYPES.get(ftype, ftype),
                    "is_primary": bool(f.get("is_primary")),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {"ok": True, "fields": fields, "count": len(fields)}


def _build_delete_field_request(app_token: str, table_id: str, field_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.paths["field_id"] = field_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def delete_bitable_fields_impl(
    app_token: str, table_id: str, field_ids: str, user_key: str = ""
) -> dict[str, Any]:
    """Delete fields (columns) by id. field_ids is comma-separated. Primary field cannot be deleted."""
    ids = [f.strip() for f in field_ids.split(",") if f.strip()]
    if not ids:
        return _error("No field_ids provided (comma-separated field ids from feishu_bitable_list_fields).")
    deleted: list[str] = []
    for fid in ids:
        res = await _invoke(_build_delete_field_request(app_token, table_id, fid), user_key=user_key)
        if not res["ok"]:
            return {**res, "deleted": deleted, "failed_field_id": fid}
        deleted.append(fid)
    return {"ok": True, "deleted": deleted, "count": len(deleted)}


# ── Attendance (考勤) — read clock-in/out results (read-only) ─────────────────
#
# Query attendance task results (who clocked in/out, when, where, and whether
# late/early/missing). Read-only — no proxy clock-in. Bot/tenant token works with
# the attendance:task:readonly scope; the app must be a Custom App and be granted
# a data-permission scope in the attendance admin console.


def _fmt_check_time(rec: Any) -> str:
    """Format a check record's check_time (epoch seconds string) to 'YYYY-MM-DD HH:MM:SS'."""
    if not isinstance(rec, dict):
        return ""
    ts = rec.get("check_time")
    if not ts:
        return ""
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError, OSError, OverflowError):
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def _build_user_tasks_query_request(
    user_ids: list[str], check_date_from: int, check_date_to: int, employee_type: str, need_overtime: bool
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/attendance/v1/user_tasks/query"
    req.add_query("employee_type", employee_type)
    req.add_query("ignore_invalid_users", "true")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "user_ids": user_ids,
        "check_date_from": check_date_from,
        "check_date_to": check_date_to,
        "need_overtime_result": need_overtime,
    }
    return req


async def query_attendance_impl(
    user_ids: str,
    date_from: str,
    date_to: str,
    employee_type: str = "employee_id",
    need_overtime: bool = False,
) -> dict[str, Any]:
    """Query attendance clock results for users over a date range (read-only)."""
    ids = [u.strip() for u in user_ids.split(",") if u.strip()]
    if not ids:
        return _error("No user_ids provided (comma-separated, max 50).")
    try:
        df = int(date_from.strip())
        dt = int(date_to.strip())
    except ValueError:
        return _error("date_from / date_to must be yyyyMMdd integers, e.g. 20260714.")
    res = await _invoke(_build_user_tasks_query_request(ids, df, dt, employee_type, need_overtime))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    results = []
    for r in data.get("user_task_results", []) if isinstance(data.get("user_task_results"), list) else []:
        # Each user_task_result has a "records" array with per-shift check-in/out
        records = r.get("records", []) if isinstance(r.get("records"), list) else []
        for rec in records:
            cin = rec.get("check_in_record", {}) if isinstance(rec.get("check_in_record"), dict) else {}
            cout = rec.get("check_out_record", {}) if isinstance(rec.get("check_out_record"), dict) else {}
            results.append(
                {
                    "user_id": r.get("user_id", ""),
                    "name": r.get("employee_name", ""),
                    "day": r.get("day", ""),
                    "check_in_time": _fmt_check_time(cin),
                    "check_in_result": rec.get("check_in_result", ""),
                    "check_in_location": cin.get("location_name", ""),
                    "check_out_time": _fmt_check_time(cout),
                    "check_out_result": rec.get("check_out_result", ""),
                    "check_out_location": cout.get("location_name", ""),
                }
            )
    return {
        "ok": True,
        "results": results,
        "count": len(results),
        "invalid_user_ids": data.get("invalid_user_ids", []),
        "unauthorized_user_ids": data.get("unauthorized_user_ids", []),
    }


# ── Tasks (任务 v2) — create/assign, list, update, complete ───────────────────
#
# Feishu native tasks: assign work to people with a due date, list, and mark
# done. Bot/tenant token works (task:task:write). Note: list returns "my_tasks"
# = tasks the CALLING identity (the bot) is responsible for — not an arbitrary
# person's tasks (that would need that user's OAuth).


def _due_to_ms(due: str) -> str | None:
    """Parse 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' to a ms-epoch string, or None if empty/invalid."""
    s = due.strip()
    if not s:
        return None
    import datetime  # noqa: PLC0415

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            dt = datetime.datetime.strptime(s, fmt)
            return str(int(dt.timestamp() * 1000))
    return None


def _build_create_task_request(body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/task/v2/tasks"
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


async def create_task_impl(
    summary: str, description: str, due: str, assignees: str, followers: str, user_key: str = ""
) -> dict[str, Any]:
    """Create a task, optionally with a due date and assignee/follower open_ids."""
    if not summary.strip():
        return _error("Task summary is required.")
    # Feishu member object: type is the member KIND ("user"/"app"), id_type is the
    # ID form (open_id/user_id). (Not type="open_id" — that's rejected as 1470400.)
    members: list[dict[str, str]] = []
    for oid in (a.strip() for a in assignees.split(",")):
        if oid:
            members.append({"id": oid, "type": "user", "id_type": "open_id", "role": "assignee"})
    for oid in (f.strip() for f in followers.split(",")):
        if oid:
            members.append({"id": oid, "type": "user", "id_type": "open_id", "role": "follower"})
    body: dict[str, Any] = {"summary": summary}
    if description.strip():
        body["description"] = description
    due_ms = _due_to_ms(due)
    if due_ms:
        body["due"] = {"timestamp": due_ms, "is_all_day": False}
    if members:
        body["members"] = members
    res = await _invoke(_build_create_task_request(body), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    task = data.get("task", {}) if isinstance(data.get("task"), dict) else {}
    return {
        "ok": True,
        "task_guid": task.get("guid", ""),
        "summary": task.get("summary", ""),
        "url": task.get("url", ""),
    }


def _build_list_tasks_request(completed: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/task/v2/tasks"
    req.add_query("page_size", page_size)
    req.add_query("type", "my_tasks")
    req.add_query("user_id_type", "open_id")
    if completed in ("true", "false"):
        req.add_query("completed", completed)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_tasks_impl(completed: str = "", page_size: int = 50, page_token: str = "") -> dict[str, Any]:
    """List the calling identity's (bot's) tasks. completed '' = all, 'true'/'false' to filter."""
    res = await _invoke(_build_list_tasks_request(completed, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    tasks = [
        {
            "guid": t.get("guid", ""),
            "summary": t.get("summary", ""),
            "status": t.get("status", ""),
            "due": (t.get("due") or {}).get("timestamp", "") if isinstance(t.get("due"), dict) else "",
            "url": t.get("url", ""),
        }
        for t in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


def _build_patch_task_request(task_guid: str, task_fields: dict[str, Any], update_fields: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/task/v2/tasks/:task_guid"
    req.paths["task_guid"] = task_guid
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"task": task_fields, "update_fields": update_fields}
    return req


async def update_task_impl(
    task_guid: str, summary: str, description: str, due: str, user_key: str = ""
) -> dict[str, Any]:
    """Update only the provided (non-empty) fields of a task."""
    task_fields: dict[str, Any] = {}
    update_fields: list[str] = []
    if summary.strip():
        task_fields["summary"] = summary
        update_fields.append("summary")
    if description.strip():
        task_fields["description"] = description
        update_fields.append("description")
    due_ms = _due_to_ms(due)
    if due_ms:
        task_fields["due"] = {"timestamp": due_ms, "is_all_day": False}
        update_fields.append("due")
    if not update_fields:
        return _error("Nothing to update: provide summary, description, or due.")
    res = await _invoke(_build_patch_task_request(task_guid, task_fields, update_fields), user_key=user_key)
    if not res["ok"]:
        return res
    return {"ok": True, "task_guid": task_guid, "updated": update_fields}


async def complete_task_impl(task_guid: str, completed: bool, user_key: str = "") -> dict[str, Any]:
    """Mark a task complete (completed=True) or reopen it (False)."""
    import time  # noqa: PLC0415

    ts = str(int(time.time() * 1000)) if completed else "0"
    res = await _invoke(_build_patch_task_request(task_guid, {"completed_at": ts}, ["completed_at"]), user_key=user_key)
    if not res["ok"]:
        return res
    return {"ok": True, "task_guid": task_guid, "completed": completed}


def _build_get_task_request(task_guid: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/task/v2/tasks/:task_guid"
    req.paths["task_guid"] = task_guid
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_task_impl(task_guid: str) -> dict[str, Any]:
    """Get a task's detail incl. completion status and per-assignee completion.

    Works for any task the calling identity (bot) can read — e.g. a task the bot
    created and assigned to someone; lets you check whether that person finished it.
    """
    res = await _invoke(_build_get_task_request(task_guid))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    task = data.get("task", {}) if isinstance(data.get("task"), dict) else {}
    members = [
        {"id": m.get("id", ""), "name": m.get("name", ""), "role": m.get("role", "")}
        for m in (task.get("members", []) if isinstance(task.get("members"), list) else [])
    ]
    per_assignee = [
        {"id": a.get("id", ""), "completed_at": _fmt_ms(a.get("completed_at"))}
        for a in (task.get("assignee_related", []) if isinstance(task.get("assignee_related"), list) else [])
    ]
    return {
        "ok": True,
        "task_guid": task.get("guid", task_guid),
        "summary": task.get("summary", ""),
        "status": task.get("status", ""),
        "completed": task.get("status") == "done" or bool(task.get("completed_at")),
        "completed_at": _fmt_ms(task.get("completed_at")),
        "members": members,
        "assignee_completion": per_assignee,
        "url": task.get("url", ""),
    }


def _fmt_ms(ms: Any) -> str:
    """Format a ms-epoch value (str/int) to 'YYYY-MM-DD HH:MM:SS', or '' if empty/0."""
    if not ms or str(ms) == "0":
        return ""
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError, OSError, OverflowError):
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    return str(ms)


# ── Calendar (日历) — create an event on the bot's primary calendar ───────────
#
# The bot creates events on its own primary calendar (auto-resolved). Bot/tenant
# token works (calendar:calendar), but the app must have bot ability enabled
# (else 190007). Attendees are added via a second call.

_primary_calendar_id: str | None = None


async def _get_primary_calendar_id() -> str | None:
    """Resolve (and cache) the bot's primary calendar_id, or None on failure."""
    global _primary_calendar_id
    if _primary_calendar_id:
        return _primary_calendar_id
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/calendar/v4/calendars/primary"
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    res = await _invoke(req)
    if not res["ok"]:
        return None
    data = res["data"] if isinstance(res["data"], dict) else {}
    for item in data.get("calendars", []) if isinstance(data.get("calendars"), list) else []:
        cal = item.get("calendar", {}) if isinstance(item, dict) else {}
        cid = cal.get("calendar_id", "")
        if cid:
            _primary_calendar_id = cid
            return cid
    return None


def _time_to_info(t: str, timezone: str) -> dict[str, str] | None:
    """Parse 'YYYY-MM-DD HH:MM' -> timed {timestamp, timezone}; 'YYYY-MM-DD' -> all-day {date, timezone}."""
    s = t.strip()
    if not s:
        return None
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError):
        dt = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
        return {"timestamp": str(int(dt.timestamp())), "timezone": timezone}
    with contextlib.suppress(ValueError):
        datetime.datetime.strptime(s, "%Y-%m-%d")
        return {"date": s, "timezone": timezone}
    return None


def _build_create_event_request(calendar_id: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/calendar/v4/calendars/:calendar_id/events"
    req.paths["calendar_id"] = calendar_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


def _build_add_attendees_request(calendar_id: str, event_id: str, open_ids: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees"
    req.paths["calendar_id"] = calendar_id
    req.paths["event_id"] = event_id
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"attendees": [{"type": "user", "user_id": oid} for oid in open_ids]}
    return req


async def create_event_impl(
    summary: str, start: str, end: str, description: str = "", attendees: str = "", timezone: str = "Asia/Shanghai"
) -> dict[str, Any]:
    """Create a calendar event on the bot's primary calendar, optionally adding attendees."""
    start_info = _time_to_info(start, timezone)
    end_info = _time_to_info(end, timezone)
    if start_info is None or end_info is None:
        return _error("start/end must be 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'.")
    calendar_id = await _get_primary_calendar_id()
    if not calendar_id:
        return _error(
            "Could not resolve the bot's primary calendar. Ensure bot ability is enabled and calendar scope granted."
        )
    body: dict[str, Any] = {"summary": summary, "start_time": start_info, "end_time": end_info}
    if description.strip():
        body["description"] = description
    res = await _invoke(_build_create_event_request(calendar_id, body))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    event = data.get("event", {}) if isinstance(data.get("event"), dict) else {}
    event_id = event.get("event_id", "")
    result: dict[str, Any] = {
        "ok": True,
        "event_id": event_id,
        "calendar_id": calendar_id,
        "summary": event.get("summary", summary),
        "start": start,
        "end": end,
    }
    open_ids = [a.strip() for a in attendees.split(",") if a.strip()]
    if open_ids and event_id:
        att_res = await _invoke(_build_add_attendees_request(calendar_id, event_id, open_ids))
        if att_res["ok"]:
            result["attendees_added"] = open_ids
        else:
            result["attendee_warning"] = att_res.get("message", "failed to add attendees")
    return result


# ── Calendar (日历) — list events on a calendar over a time range ─────────────
#
# Read the schedule of a calendar (the bot's primary one by default) between two
# instants. Reading someone else's calendar needs the identity to have reader
# access to it; scope calendar:calendar or calendar:calendar.event:read.


def _ts_of(t: str, timezone: str) -> str | None:
    """Parse 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' (00:00 that day) to a Unix-second string."""
    info = _time_to_info(t, timezone)
    if info is None:
        return None
    if "timestamp" in info:
        return info["timestamp"]
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError):
        dt = datetime.datetime.strptime(info["date"], "%Y-%m-%d")
        return str(int(dt.timestamp()))
    return None


def _build_list_events_request(
    calendar_id: str, start_ts: str, end_ts: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/calendar/v4/calendars/:calendar_id/events"
    req.paths["calendar_id"] = calendar_id
    req.add_query("start_time", start_ts)
    req.add_query("end_time", end_ts)
    req.add_query("page_size", page_size)
    req.add_query("user_id_type", "open_id")
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _event_time_str(t: Any) -> str:
    """Normalize a calendar event start/end object to a readable string."""
    if not isinstance(t, dict):
        return ""
    if t.get("timestamp"):
        return _fmt_ms(str(int(t["timestamp"]) * 1000)) if str(t["timestamp"]).isdigit() else str(t["timestamp"])
    return str(t.get("date", ""))


def _normalize_event(ev: dict[str, Any]) -> dict[str, Any]:
    organizer = ev.get("organizer_calendar_id", "") or ev.get("event_organizer", {}).get("display_name", "")
    attendee_ability = ev.get("attendee_ability", "")
    start = ev.get("start_time", {})
    return {
        "event_id": ev.get("event_id", ""),
        "summary": ev.get("summary", ""),
        "description": ev.get("description", ""),
        "start": _event_time_str(ev.get("start_time", {})),
        "end": _event_time_str(ev.get("end_time", {})),
        "status": ev.get("status", ""),
        "is_all_day": isinstance(start, dict) and "date" in start and "timestamp" not in start,
        "organizer": organizer,
        "attendee_ability": attendee_ability,
    }


async def list_events_impl(
    start: str, end: str, calendar_id: str = "", timezone: str = "Asia/Shanghai", max_events: int = 50
) -> dict[str, Any]:
    """List events on a calendar between start and end. Blank calendar_id uses the bot's primary calendar."""
    start_ts = _ts_of(start, timezone)
    end_ts = _ts_of(end, timezone)
    if start_ts is None or end_ts is None:
        return _error("start/end must be 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'.")
    cal_id = calendar_id.strip() or await _get_primary_calendar_id()
    if not cal_id:
        return _error("Could not resolve a calendar_id. Pass one, or ensure the bot's primary calendar is available.")
    events: list[dict[str, Any]] = []
    page_token = ""
    while len(events) < max_events:
        page_size = min(1000, max(50, max_events - len(events)))
        res = await _invoke(_build_list_events_request(cal_id, start_ts, end_ts, page_size, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for ev in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if isinstance(ev, dict):
                events.append(_normalize_event(ev))
            if len(events) >= max_events:
                break
        page_token = data.get("page_token", "") if data.get("has_more") else ""
        if not page_token:
            break
    return {"ok": True, "calendar_id": cal_id, "count": len(events), "events": events}


# ── Calendar (日历) — create a separate event for each person ─────────────────
#
# For "give each person their own schedule": create one independent event per
# attendee on the bot's primary calendar, each inviting only that one person.
# Partial failures are reported per person rather than crashing the batch.


async def create_events_per_person_impl(
    summary: str,
    start: str,
    end: str,
    attendees: str,
    description: str = "",
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Create one independent event per open_id, each inviting only that person."""
    open_ids = [a.strip() for a in attendees.split(",") if a.strip()]
    if not open_ids:
        return _error("attendees must contain at least one comma-separated open_id.")
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for oid in open_ids:
        res = await create_event_impl(summary, start, end, description, oid, timezone)
        if res.get("ok") and not res.get("attendee_warning"):
            created.append({"open_id": oid, "event_id": res.get("event_id", "")})
        else:
            failed.append({"open_id": oid, "error": res.get("attendee_warning") or res.get("message", "failed")})
    return {"ok": not failed, "count": len(created), "created": created, "failed": failed}


# ── Contact (通讯录) — list department members ────────────────────────────────
#
# Get the roster for a department (or the whole org from root id "0"), so the
# agent has the user_id list needed to batch-query attendance/payroll. Tenant
# token works; the app's 通讯录权限范围 must cover the members you want to see.


def _build_dept_children_request(
    department_id: str, department_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/departments/:department_id/children"
    req.paths["department_id"] = department_id
    req.add_query("department_id_type", department_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_find_by_department_request(
    department_id: str, department_id_type: str, user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/users/find_by_department"
    req.add_query("department_id", department_id)
    req.add_query("department_id_type", department_id_type)
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _members_of_department(
    department_id: str, department_id_type: str, user_id_type: str
) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """All members directly in one department (paged). Returns (members, error_or_None)."""
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _invoke(
            _build_find_by_department_request(department_id, department_id_type, user_id_type, 50, page_token)
        )
        if not res["ok"]:
            return members, res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "user_id": it.get("user_id", ""),
                    "open_id": it.get("open_id", ""),
                    "name": it.get("name", ""),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return members, None


async def _child_department_ids(department_id: str, department_id_type: str) -> list[str]:
    """Direct child department ids of a department (one level, paged)."""
    ids: list[str] = []
    page_token = ""
    while True:
        res = await _invoke(_build_dept_children_request(department_id, department_id_type, 50, page_token))
        if not res["ok"]:
            return ids
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            did = (
                it.get("department_id", "")
                if department_id_type == "department_id"
                else it.get("open_department_id", "")
            )
            if did:
                ids.append(did)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return ids


async def list_department_members_impl(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    recursive: bool = False,
) -> dict[str, Any]:
    """List members of a department. recursive=True walks sub-departments too.

    department_id "0" is the org root. Returns de-duplicated [{user_id, open_id, name}].
    """
    seen: set[str] = set()
    all_members: list[dict[str, str]] = []
    to_visit = [department_id]
    visited: set[str] = set()
    while to_visit:
        did = to_visit.pop()
        if did in visited:
            continue
        visited.add(did)
        members, err = await _members_of_department(did, department_id_type, user_id_type)
        if err is not None:
            return err
        for m in members:
            key = m.get("open_id") or m.get("user_id") or m.get("name")
            if key and key not in seen:
                seen.add(key)
                all_members.append(m)
        if recursive:
            child_type = "department_id" if department_id_type == "department_id" else "open_department_id"
            to_visit.extend(await _child_department_ids(did, child_type))
    return {
        "ok": True,
        "department_id": department_id,
        "recursive": recursive,
        "members": all_members,
        "count": len(all_members),
    }


# ── Drive — download a file/attachment to disk ────────────────────────────────
#
# Two sources: a drive media file_token (goes through the medias endpoint), or a
# direct URL (approval-form attachments are direct URLs valid only ~12h — download
# them straight, NOT via medias).


def _build_media_download_request(file_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/drive/v1/medias/:file_token/download"
    req.paths["file_token"] = file_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _download_url_bytes(url: str) -> tuple[bytes | None, str]:
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:  # transport failure
        return None, f"{type(exc).__name__}: {exc}"
    if resp.status_code in (403, 404):
        return None, (
            f"HTTP {resp.status_code} — the attachment link may have expired "
            "(approval-form URLs are valid ~12h). Re-read the instance detail for a fresh URL."
        )
    if resp.status_code >= 400:
        return None, f"HTTP {resp.status_code}"
    return resp.content, ""


async def _download_media_bytes(file_token: str) -> tuple[bytes | None, str]:
    client = _get_client()
    if client is None:
        return None, "Feishu app not configured."
    try:
        resp = await client.arequest(_build_media_download_request(file_token))
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if not content:
        code = getattr(resp, "code", None)
        return None, f"no file content returned (code={code})"
    data = bytes(content)
    # A JSON error body (not a binary file) means the token was rejected.
    if data[:1] in (b"{", b"["):
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            body = json.loads(data.decode("utf-8"))
            if isinstance(body, dict) and body.get("code") not in (0, None):
                return None, f"Feishu API error {body.get('code')}: {body.get('msg', '')}"
    return data, ""


async def download_file_impl(source: str, save_path: str, is_url: bool = False) -> dict[str, Any]:
    """Download a Feishu file to disk. is_url=True treats source as a direct URL, else a media file_token."""
    if not source or not save_path:
        return _error("source and save_path are required.")
    data, err = await (_download_url_bytes(source) if is_url else _download_media_bytes(source))
    if data is None:
        return _error(err or "download failed", source=source)
    path = pathlib.Path(save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).write_bytes(data)
    except OSError as exc:
        return _error(f"could not write file: {exc}", path=str(path))
    return {"ok": True, "path": str(path), "bytes": len(data)}


# ── Delete a cloud file / document (to trash) ─────────────────────────────────
#
# DELETE /drive/v1/files/:file_token?type=... moves the file to the recycle bin
# (recoverable). Works with tenant OR user token; deleting inside a user-owned
# wiki needs the user's UAT (pass user_key). To delete a *wiki* doc: resolve the
# node with get_wiki_node_impl → obj_token/obj_type, then delete that.

_DELETABLE_FILE_TYPES = {"file", "docx", "doc", "sheet", "bitable", "mindnote", "slides", "folder", "shortcut"}


def _build_delete_file_request(file_token: str, file_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/drive/v1/files/:file_token"
    req.paths["file_token"] = file_token
    req.add_query("type", file_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def delete_file_impl(file_token: str, file_type: str, user_key: str = "") -> dict[str, Any]:
    """Delete a cloud file/document (moves it to the recycle bin — recoverable).

    Pass ``user_key`` to delete as that user (required when the file/wiki is owned by
    the user and the bot isn't a collaborator); empty uses the bot's tenant token.
    """
    token = file_token.strip()
    if not token:
        return _error("file_token is required.")
    ftype = file_type.strip()
    if ftype not in _DELETABLE_FILE_TYPES:
        return _error(f"file_type must be one of {sorted(_DELETABLE_FILE_TYPES)}, got {ftype!r}.")
    res = await _invoke(_build_delete_file_request(token, ftype), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    out: dict[str, Any] = {"ok": True, "file_token": token, "type": ftype}
    # Folder deletion is async and returns a task_id — surface it for status polling.
    if data.get("task_id"):
        out["task_id"] = data["task_id"]
    return out


# ── Create documents: standalone docx + wiki (knowledge base) nodes ───────────
#
# Read tools above only *fetch* content; these create new documents. A wiki doc
# is a two-layer thing: the wiki *node* (the entry in a knowledge space) wraps an
# underlying docx whose token is `obj_token` — that token is the docx document_id
# you pass to `append_doc_content_impl` to fill in the body. So the full flow is
# list_wiki_spaces → create_wiki_node → append_doc_content.

_DOC_BASE_URL = "https://feishu.cn"


def _build_docx_create_request(title: str, folder_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/documents"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {}
    if title:
        body["title"] = title
    if folder_token:
        body["folder_token"] = folder_token
    req.body = body
    return req


async def create_docx_impl(title: str, folder_token: str = "", user_key: str = "") -> dict[str, Any]:
    """Create a new standalone docx cloud document. Returns its document_id + URL.

    Pass ``user_key`` to create as that user (doc owned by them); empty uses tenant token.
    """
    res = await _invoke(_build_docx_create_request(title.strip(), folder_token.strip()), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    doc = data.get("document", {}) if isinstance(data.get("document"), dict) else {}
    document_id = doc.get("document_id", "")
    return {
        "ok": True,
        "document_id": document_id,
        "title": doc.get("title", title),
        "revision_id": doc.get("revision_id"),
        "url": f"{_DOC_BASE_URL}/docx/{document_id}" if document_id else "",
    }


def _build_wiki_node_create_request(
    *, space_id: str, obj_type: str, node_type: str, parent_node_token: str, title: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/wiki/v2/spaces/:space_id/nodes"
    req.paths["space_id"] = space_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"obj_type": obj_type, "node_type": node_type}
    if parent_node_token:
        body["parent_node_token"] = parent_node_token
    if title:
        body["title"] = title
    req.body = body
    return req


async def create_wiki_node_impl(
    space_id: str, title: str, obj_type: str = "docx", parent_node_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Create a node (default: a docx doc) in a wiki space. Returns node_token + obj_token(=document_id).

    Pass ``user_key`` to act as that user (needed when the wiki space is owned by the
    user, so the bot isn't a collaborator); empty uses the bot's tenant token.
    """
    if not space_id.strip():
        return _error("space_id is required. Use feishu_wiki_list_spaces to find it.")
    # Feishu deprecated `doc`; the API rejects it with error 131010.
    obj_type = (obj_type or "docx").strip()
    if obj_type == "doc":
        obj_type = "docx"
    res = await _invoke(
        _build_wiki_node_create_request(
            space_id=space_id.strip(),
            obj_type=obj_type,
            node_type="origin",
            parent_node_token=parent_node_token.strip(),
            title=title.strip(),
        ),
        user_key=user_key,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    node = data.get("node", {}) if isinstance(data.get("node"), dict) else {}
    obj_token = node.get("obj_token", "")
    return {
        "ok": True,
        "node_token": node.get("node_token", ""),
        "obj_token": obj_token,
        "obj_type": node.get("obj_type", obj_type),
        "space_id": node.get("space_id", space_id),
        "title": node.get("title", title),
        # For a docx node, obj_token is the document_id — write the body with
        # feishu_doc_append_content(document_id=obj_token, ...).
        "url": f"{_DOC_BASE_URL}/wiki/{node.get('node_token', '')}",
    }


async def create_wiki_doc_with_content_impl(
    space_id: str, title: str, content: str, parent_node_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Create a wiki docx node AND write its body in one call (atomic-ish).

    Avoids the "empty node" failure of doing create + append as two separate LLM
    tool calls: creates the node, then appends the body. If the body write fails,
    the node_token/obj_token are returned alongside the error (so the half-created
    node can be found or retried), rather than leaving a silent empty page.
    """
    node = await create_wiki_node_impl(space_id, title, "docx", parent_node_token, user_key)
    if not node["ok"]:
        return node
    obj_token = node.get("obj_token", "")
    # No body requested (or only blank lines): return the node as-is, not an error.
    if not _content_to_blocks(content or ""):
        return {**node, "added": 0, "note": "no body content — created an empty doc"}
    if not obj_token:
        return {**node, "ok": False, "message": "node created but obj_token missing — cannot write body"}
    written = await append_doc_content_impl(obj_token, content, user_key)
    if not written["ok"]:
        # Surface the node so the caller knows a doc exists and can retry the body.
        return {
            **node,
            "ok": False,
            "body_written": False,
            "added": written.get("added", 0),
            "message": f"Node created but writing body failed: {written.get('message', '')}",
            **({"need_auth": True} if written.get("need_auth") else {}),
        }
    return {**node, "body_written": True, "added": written.get("added", 0)}


def _build_list_spaces_request(page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    return req


async def list_wiki_spaces_impl(page_size: int = 20, page_token: str = "", user_key: str = "") -> dict[str, Any]:
    """List the wiki (knowledge base) spaces the app/user can access. Returns space_id + name.

    Pass ``user_key`` to list the spaces THAT USER can see (the bot's own tenant token
    only sees spaces the bot was added to — usually none); empty uses the bot token.
    """
    page_size = max(1, min(int(page_size or 20), 50))
    res = await _invoke(_build_list_spaces_request(page_size, page_token.strip()), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    spaces = [
        {"space_id": it.get("space_id", ""), "name": it.get("name", ""), "space_type": it.get("space_type", "")}
        for it in items
        if isinstance(it, dict)
    ]
    return {
        "ok": True,
        "spaces": spaces,
        "page_token": data.get("page_token", ""),
        "has_more": bool(data.get("has_more")),
    }


def _build_list_wiki_nodes_request(
    space_id: str, page_size: int, page_token: str, parent_node_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces/:space_id/nodes"
    req.paths["space_id"] = space_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    if parent_node_token:
        req.add_query("parent_node_token", parent_node_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_wiki_nodes_impl(
    space_id: str, page_size: int = 50, page_token: str = "", parent_node_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """List the child nodes (documents/pages) of a wiki space (or under a parent node).

    Pass ``user_key`` to browse as that user (the bot's tenant token only sees spaces
    it was added to); empty uses the bot token. ``parent_node_token`` empty lists the
    space's top level; set it to drill into a node's children.
    """
    if not space_id.strip():
        return _error("space_id is required. Use feishu_wiki_list_spaces to find it.")
    page_size = max(1, min(int(page_size or 50), 50))
    res = await _invoke(
        _build_list_wiki_nodes_request(space_id.strip(), page_size, page_token.strip(), parent_node_token.strip()),
        user_key=user_key,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    nodes = [
        {
            "node_token": it.get("node_token", ""),
            "obj_token": it.get("obj_token", ""),
            "obj_type": it.get("obj_type", ""),
            "title": it.get("title", ""),
            "has_child": bool(it.get("has_child")),
        }
        for it in items
        if isinstance(it, dict)
    ]
    return {
        "ok": True,
        "nodes": nodes,
        "page_token": data.get("page_token", ""),
        "has_more": bool(data.get("has_more")),
    }


# ── Write body content into a docx ────────────────────────────────────────────
#
# The docx block API is rich (tables/images/code/…). We map plain text / light
# Markdown to the two blocks that cover "write a knowledge-base doc": headings
# (`# ` → h1 … up to `###### ` → h6, block_type 3..8) and paragraphs (block_type
# 2). Blank lines are skipped. Children are appended to the document root
# (block_id == document_id) in batches of <=50 (the API cap).

_HEADING_KEYS = {3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4", 7: "heading5", 8: "heading6"}
_BLOCKS_BATCH = 50


def _line_to_block(line: str) -> dict[str, Any] | None:
    text = line.rstrip()
    if not text.strip():
        return None
    stripped = text.lstrip()
    level = 0
    while level < len(stripped) and stripped[level] == "#":
        level += 1
    # "# " .. "###### " → heading blocks (block_type 3..8)
    if 1 <= level <= 6 and level < len(stripped) and stripped[level] == " ":
        block_type = 2 + level
        content = stripped[level + 1 :].strip()
        key = _HEADING_KEYS[block_type]
        return {"block_type": block_type, key: {"elements": [{"text_run": {"content": content}}]}}
    # Everything else → a plain text paragraph (block_type 2)
    return {"block_type": 2, "text": {"elements": [{"text_run": {"content": text.strip()}}]}}


def _content_to_blocks(content: str) -> list[dict[str, Any]]:
    blocks = [b for b in (_line_to_block(ln) for ln in content.splitlines()) if b is not None]
    return blocks


def _build_blocks_append_request(document_id: str, children: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    # Root block: the document_id doubles as the root block_id.
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"children": children}
    return req


async def append_doc_content_impl(document_id: str, content: str, user_key: str = "") -> dict[str, Any]:
    """Append text/heading blocks (from plain text or light Markdown) to a docx body.

    Pass ``user_key`` to write as that user (e.g. into a doc inside a user-owned wiki);
    empty uses the bot's tenant token.
    """
    if not document_id.strip():
        return _error("document_id is required.")
    blocks = _content_to_blocks(content or "")
    if not blocks:
        return _error("content is empty — nothing to write.")
    added = 0
    for start in range(0, len(blocks), _BLOCKS_BATCH):
        batch = blocks[start : start + _BLOCKS_BATCH]
        res = await _invoke(_build_blocks_append_request(document_id.strip(), batch), user_key=user_key)
        if not res["ok"]:
            res["added"] = added
            return res
        added += len(batch)
    return {"ok": True, "document_id": document_id.strip(), "added": added}
