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


# ── Wiki — resolve a wiki node token to its underlying document ───────────────
#
# A Feishu wiki URL (.../wiki/<node_token>) is a shell; the real content lives in
# an underlying docx/sheet/bitable/... This resolves the node token to obj_token
# + obj_type so the agent can then read it (docx/doc/sheet via read_doc_impl).


async def get_wiki_node_impl(token: str) -> dict[str, Any]:
    """Resolve a wiki node token to its underlying document (obj_token + obj_type)."""
    res = await _invoke(_wiki_node.build_wiki_node_get_request(token=token))
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

_UAT_USER_KEY = "default"  # single local user; not multi-tenant
_token_store: Any = None
_uat_client: Any = None
_DEFAULT_SCOPES = "docs:doc:readonly drive:drive:readonly offline_access"


def _uat_store_path() -> str:
    workspace = os.environ.get("WORKSPACE_DIR", "")
    base = pathlib.Path(workspace) if workspace else pathlib.Path(__file__).resolve().parents[1]
    d = base / ".psi" / "feishu"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "uat.json")


def _pending_auth_path() -> str:
    return str(pathlib.Path(_uat_store_path()).parent / "pending_auth.json")


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


async def auth_start_impl(scopes: str = "") -> dict[str, Any]:
    """Build the browser authorize URL for the authorization-code flow."""
    creds = _config()
    if creds is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    from urllib.parse import urlencode  # noqa: PLC0415

    app_id, _ = creds
    scope_str = scopes or _DEFAULT_SCOPES
    state = os.urandom(8).hex()
    await anyio.Path(_pending_auth_path()).write_text(json.dumps({"state": state}), encoding="utf-8")
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


async def auth_complete_impl(code: str) -> dict[str, Any]:
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
    await _get_token_store().set(_UAT_USER_KEY, uat)
    with contextlib.suppress(OSError):
        await anyio.Path(_pending_auth_path()).unlink()
    return {
        "ok": True,
        "open_id": uat.open_id or "",
        "scopes": uat.scopes,
        "message": "授权成功, 已缓存 user_access_token.",
    }


async def _get_valid_uat() -> Any:
    """Return a non-expired UAT (refreshing via refresh_token if needed), or None."""
    from lark_channel.channel.auth.device_flow import uat_needs_refresh  # noqa: PLC0415

    store = _get_token_store()
    uat = await store.get(_UAT_USER_KEY)
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
                await store.set(_UAT_USER_KEY, uat)
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


async def search_docs_impl(search_key: str, count: int, offset: int, docs_types: str) -> dict[str, Any]:
    """Search cloud docs by keyword (needs a user_access_token). Returns matched docs."""
    client = _get_uat_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _get_valid_uat()
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


async def create_bitable_record_impl(app_token: str, table_id: str, fields_json: str) -> dict[str, Any]:
    """Create one record in a bitable table. fields_json is a JSON object of {column: value}."""
    try:
        fields = json.loads(fields_json)
    except ValueError as exc:
        return _error(f"fields_json is not valid JSON: {exc}")
    if not isinstance(fields, dict):
        return _error("fields_json must be a JSON object mapping column names to values.")
    res = await _invoke(_build_create_record_request(app_token, table_id, fields))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    record = data.get("record", {}) if isinstance(data.get("record"), dict) else {}
    return {"ok": True, "record_id": record.get("record_id", ""), "fields": record.get("fields", {})}
