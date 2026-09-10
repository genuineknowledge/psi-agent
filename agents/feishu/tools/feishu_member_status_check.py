"""Feishu member status check — classify board names into 在职/疑似离职/解析失败.

The TODO board lists people by display name; some have left the company (their
open_id can no longer be resolved) while still appearing on the board. Reminding
or counting a resigned person as 「没写 todo」 is wrong — but silently dropping
them hides data too. This tool resolves the whole name list against the org directory in ONE call:

    active    — 在职 (name found, status 无离职/冻结标记)
    unresolved— 重名歧义 → 解析失败,需人工
    resigned_count — 已离职/冻结人数 (通讯录已移除或 status is_resigned/is_exited/is_frozen)

Deterministic: directory comparison + Feishu status flags, no model judgment.
**已离职/冻结人员的姓名不返回**——只有人数,调用方拿不到名字,任何面向
mentor/上级的输出(表格/文字/报告)都无法体现他们。
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_member_status_check(names_json: str, user_key: str = "") -> str:
    """Classify a list of display names into 在职 / 已离职或冻结 / 解析失败.

    Args:
        names_json: JSON array of display names (from the board's 人名列), e.g.
            ``["张三", "李四"]``.
        user_key: Identity for the directory read (usual convention; omitted
            uses the bot's tenant token, which needs 通讯录权限范围 coverage).

    Returns JSON: ``{"ok": true, "active": [{"name", "open_id"}], "unresolved": ["..."],
    "resigned_count": N}`` — every input name lands in exactly one bucket;
    resigned names are withheld on purpose (count only).
    """
    try:
        names = json.loads(names_json)
    except json.JSONDecodeError as exc:
        return _f.dumps_result(_f._error(f"names_json must be valid JSON: {exc}"))
    if not isinstance(names, list) or not names:
        return _f.dumps_result(_f._error("names_json must be a non-empty JSON array of names."))

    outcome = await _f.member_status_check_impl([str(n).strip() for n in names if str(n).strip()], user_key)
    if outcome.get("ok"):
        # 已离职/冻结人员的姓名不返回(只给人数):调用方拿不到名字,
        # 面向 mentor 的输出(表格/文字/报告)就无从体现。
        outcome["resigned_count"] = len(outcome.get("resigned", []))
        outcome.pop("resigned", None)
    return json.dumps(outcome, ensure_ascii=False, default=str)
