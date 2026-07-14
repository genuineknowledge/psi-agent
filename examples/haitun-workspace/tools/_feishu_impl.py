"""Private helper for the Feishu tools — authenticated client + request execution.

Wraps the ``lark_channel`` SDK (already a project dependency): builds one
authenticated ``Client`` from ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``,
caches it module-level, and runs ``BaseRequest`` objects through the SDK's native
async ``arequest``. Drive-comment requests reuse the SDK's ready-made builders;
docx/doc/sheet raw-content and create-reply requests are hand-built the same way
the SDK's own ``api/drive/comment.py`` does it.
"""

from __future__ import annotations

import json
import os
from typing import Any

from lark_channel.api.drive import comment as _comment
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


async def _invoke(request: Any) -> dict[str, Any]:
    client = _get_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    try:
        resp = await client.arequest(request)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")

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


async def add_comment_impl(file_token: str, file_type: str, content: str) -> dict[str, Any]:
    req = _comment.build_comment_create_request(file_token=file_token, file_type=file_type, content=content)
    return await _invoke(req)


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
    file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str
) -> dict[str, Any]:
    req = _build_reply_create_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        content=content,
        at_user_id=at_user_id,
    )
    return await _invoke(req)


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


def _build_send_message_request(receive_id: str, receive_id_type: str, text: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages"
    req.add_query("receive_id_type", receive_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    return req


async def send_message_impl(receive_id: str, text: str, receive_id_type: str) -> dict[str, Any]:
    """Send a text message to a chat/user. Returns message_id + thread_id (thread_id is the topic root)."""
    res = await _invoke(_build_send_message_request(receive_id, receive_id_type, text))
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


async def get_approval_instance_impl(instance_id: str, user_id_type: str = "open_id") -> dict[str, Any]:
    """Read an approval instance's detail — applicant, status, the submitted form, and task_list."""
    res = await _invoke(_build_instance_get_request(instance_id, user_id_type))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "instance_code": instance_id,
        "approval_code": data.get("approval_code", ""),
        "approval_name": data.get("approval_name", ""),
        "status": data.get("status", ""),
        "applicant": data.get("user_id", "") or data.get("open_id", ""),
        "form": data.get("form", ""),
        "task_list": data.get("task_list", []),
        "timeline": data.get("timeline", []),
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
