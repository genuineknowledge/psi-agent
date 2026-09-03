"""Read the TPMF work-tree mindnote and list per-person @-assigned items.

Feishu has exactly one API entry for reading a mindnote's nodes
(``GET /open-apis/mindnote/v1/mindnotes/{token}/nodes``) and it requires a
**user-level** token with ``mindnote:node:read`` — the tenant token alone
cannot read mindnotes. The work tree is the company's task-assignment map:
nodes carry ``element_type: user`` mentions, and the 「重要 todo 全覆盖」
check compares each person's mentions against what they filed (大目标/小目标
/TODO) that cycle.

Why a tool rather than a skill paragraph: the node data is a shape problem —
flattened nodes rebuilt into a tree by ``parent_id``, mentions collected per
person, contact lookups batched — and redoing it in the model's hands every
cycle is slow and error-prone.
"""

from __future__ import annotations

from typing import Any

import _feishu_impl as _core
from lark_channel.core.model import AccessTokenType, BaseRequest, HttpMethod


def _build_mindnote_nodes_request(mindnote_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/mindnote/v1/mindnotes/:mindnote_token/nodes"
    req.paths["mindnote_token"] = mindnote_token
    req.token_types = {AccessTokenType.USER}
    return req


def _build_user_get_request(user_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = (
        "/open-apis/contact/v3/users/:user_id"
        "?user_id_type=open_id&department_id_type=open_department_id"
    )
    req.paths["user_id"] = user_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _node_text(node: dict[str, Any], names: dict[str, str]) -> str:
    """Concatenate a node's text runs; mentions render as @姓名 once resolved."""
    parts: list[str] = []
    for t in node.get("texts", []) or []:
        et = t.get("element_type")
        if et == "text":
            parts.append(t.get("text", {}).get("content", ""))
        elif et == "user":
            uid = t.get("mention_user", {}).get("user", "")
            parts.append("@" + (names.get(uid) or uid))
    return "".join(parts).replace("​", "").strip()


def build_people(nodes: list[dict[str, Any]], names: dict[str, str]) -> tuple[list[dict[str, Any]], set[str]]:
    """Rebuild the flattened node list into per-person @-assignments.

    Pure data transform (no IO) so tests exercise it directly: every node
    carrying an ``element_type: user`` mention counts for that user; each entry
    is the full path from the root down to the mentioning node, de-duplicated.
    Returns ``(people, mentioned_user_ids)`` — people is sorted by count desc.
    """
    by_id = {n.get("node_id"): n for n in nodes if isinstance(n, dict)}
    mentioned: set[str] = set()
    for n in nodes:
        for t in n.get("texts", []) or []:
            if t.get("element_type") == "user":
                uid = t.get("mention_user", {}).get("user", "")
                if uid:
                    mentioned.add(uid)

    def path_of(n: dict[str, Any]) -> str:
        chain: list[str] = []
        cur = n
        while cur:
            text = _node_text(cur, names)
            if text:
                chain.append(text)
            pid = cur.get("parent_id")
            cur = by_id.get(pid) if pid else None
        return " / ".join(reversed(chain))

    people: dict[str, dict[str, Any]] = {}
    for n in nodes:
        for t in n.get("texts", []) or []:
            if t.get("element_type") != "user":
                continue
            uid = t.get("mention_user", {}).get("user", "")
            if not uid:
                continue
            entry = people.setdefault(uid, {"name": names.get(uid, uid), "open_id": uid, "items": []})
            p = path_of(n)
            if p and p not in entry["items"]:
                entry["items"].append(p)

    out_people = [
        {**v, "count": len(v["items"])} for v in sorted(people.values(), key=lambda x: -len(x["items"]))
    ]
    return out_people, mentioned


async def _resolve_names(user_ids: set[str]) -> dict[str, str]:
    """open_id → 姓名 via the contact book (tenant token is enough here)."""
    names: dict[str, str] = {}
    for uid in user_ids:
        res = await _core._invoke(_build_user_get_request(uid))
        if res.get("ok"):
            user = res.get("data", {}).get("user", {}) if isinstance(res.get("data"), dict) else {}
            name = user.get("name", "")
            if name:
                names[uid] = name
        # 查不到就留着 open_id 前缀, 不静默丢弃 —— 检查方需要知道谁没解析出来。
        if uid not in names:
            names[uid] = f"{uid[:8]}…(查不到姓名)"
    return names


async def read_worktree_impl(mindnote_token: str, user_key: str = "") -> dict[str, Any]:
    """Read the work-tree mindnote and return per-person @-assigned item lists.

    Requires the user's authorization carrying ``mindnote:node:read`` — when the
    cached UAT is missing or lacks it, the result is a ``need_auth`` error telling
    the caller to run ``feishu_auth_request(capabilities="mindnote_read")``.
    """
    if not mindnote_token.strip():
        return _core._error("mindnote_token is required.")
    sent = await _core._send_as_user(_build_mindnote_nodes_request(mindnote_token), user_key)
    if sent is None:
        return _core._error(
            "需要授权: 读思维笔记节点要用户级授权(mindnote:node:read)。"
            "先调 feishu_auth_request(user_key=<sender_open_id>, capabilities='mindnote_read')"
            "发授权卡片, 用户同意后重试本工具。"
        )
    if not sent.get("ok"):
        return sent
    data = sent.get("data", {}) if isinstance(sent.get("data"), dict) else {}
    nodes = data.get("nodes", []) or []
    if not nodes:
        return _core._error("工作树没有读到节点 —— 确认 mindnote_token 正确、文档不为空。")

    people, mentioned = build_people(nodes, {})
    names = await _resolve_names(mentioned)
    people, _ = build_people(nodes, names)

    return {
        "ok": True,
        "mindnote_token": mindnote_token,
        "node_count": len(nodes),
        "people_count": len(people),
        "people": people,
    }
