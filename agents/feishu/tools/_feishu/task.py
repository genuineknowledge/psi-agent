"""Feishu Tasks (任务) — create a task.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
from typing import Any

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

# ── Tasks (任务 v2) — the create path only ────────────────────────────────────
#
# The task DOMAIN moved into skills/feishu-task/SKILL.md as endpoint-table rows,
# so the five task tools are gone. These three stay because `assignment_accept`
# needs a create that is EXACTLY-ONCE: it holds a Fusion Memory claim token, and
# a rate-limit retry could publish the task twice under one claim. That is
# `retry_rate_limits=False` — a guarantee the endpoint table cannot express, so
# this one caller keeps a Python path while the table serves everything else.


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
    summary: str,
    description: str,
    due: str,
    assignees: str,
    followers: str,
    user_key: str = "",
    identity: str = "",
    *,
    retry_rate_limits: bool = True,
) -> dict[str, Any]:
    """Create a task, optionally with a due date and assignee/follower open_ids."""
    if not summary.strip():
        return _core._error("Task summary is required.")
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
    request = _build_create_task_request(body)
    if retry_rate_limits:
        res = await _core._invoke(request, user_key=user_key, prefer="user", identity=identity)
    else:
        res = await _core._invoke(
            request,
            user_key=user_key,
            prefer="user",
            identity=identity,
            retry_rate_limits=False,
        )
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
