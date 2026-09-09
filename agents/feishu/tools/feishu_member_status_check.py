"""Feishu member status check — classify board names into 在职/疑似离职/解析失败.

The TODO board lists people by display name; some have left the company (their
open_id can no longer be resolved) while still appearing on the board. Reminding
or counting a resigned person as 「没写 todo」 is wrong — but silently dropping
them hides data too. This tool resolves the whole name list against the org
directory in ONE call and returns three buckets:

    active    — name found in the directory (with open_id)
    resigned  — name not found → 疑似离职
    unresolved— name matched multiple entries or looks ambiguous → 解析失败,需人工

Deterministic: pure directory comparison, no model judgment.
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_member_status_check(names_json: str, user_key: str = "") -> str:
    """Classify a list of display names into 在职 / 疑似离职 / 解析失败.

    Args:
        names_json: JSON array of display names (from the board's 人名列), e.g.
            ``["张三", "李四"]``.
        user_key: Identity for the directory read (usual convention; omitted
            uses the bot's tenant token, which needs 通讯录权限范围 coverage).

    Returns JSON: ``{"ok": true, "active": [{"name", "open_id"}], "resigned": ["..."],
    "unresolved": ["..."]}`` — every input name lands in exactly one bucket.
    """
    try:
        names = json.loads(names_json)
    except json.JSONDecodeError as exc:
        return _f.dumps_result(_f._error(f"names_json must be valid JSON: {exc}"))
    if not isinstance(names, list) or not names:
        return _f.dumps_result(_f._error("names_json must be a non-empty JSON array of names."))

    outcome = await _f.member_status_check_impl([str(n).strip() for n in names if str(n).strip()], user_key)
    return json.dumps(outcome, ensure_ascii=False, default=str)
