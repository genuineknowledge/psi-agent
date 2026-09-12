"""Feishu batch private-message send — 一次工具调用发完全部私聊.

15:00 检测的逐人私聊是 O(人数) 的工具调用,模型一轮发不完会自己收尾、后半
的人收不到提示。本工具把发送循环下沉成代码:一次调用循环发完全部,失败有账。
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_pm_batch_send(
    items_json: str,
    pending_file: str = "",
    user_key: str = "",
) -> str:
    """Send a batch of private messages; the code loops through all of them.

    Args:
        items_json: JSON array of items, e.g.
            ``[{"name": "张三", "open_id": "ou_xxx", "text": "违规项..."}, ...]``.
            open_id must come from ``feishu_member_status_check``'s active list
            (never hand-filled from conversation context).
        pending_file: Optional workspace-root-relative state file (e.g.
            ``pending-pm.txt``, lines ``姓名|摘要|状态``); when given, sent rows
            are marked 已发 and failed rows 失败:<原因>.
        user_key: Identity convention (unused for sending; kept for symmetry).

    Returns JSON: ``{"ok": true, "total": N, "sent": [names], "sent_count": n,
    "failed": [{"name", "reason"}], "failed_count": n}`` — the loop never stops
    partway; every item is attempted.
    """
    try:
        items = json.loads(items_json)
    except json.JSONDecodeError as exc:
        return _f.dumps_result(_f._error(f"items_json must be valid JSON: {exc}"))
    if not isinstance(items, list) or not items:
        return _f.dumps_result(_f._error("items_json must be a non-empty JSON array of items."))
    if not all(isinstance(it, dict) for it in items):
        return _f.dumps_result(_f._error("every item must be an object with name/open_id/text."))

    outcome = await _f.pm_batch_send_impl(items, pending_file.strip(), user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
