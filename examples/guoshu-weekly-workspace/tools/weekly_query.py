from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

_MCP_PATH = Path(__file__).resolve().parent / "_weekly_mcp.py"
_MODULE_NAME = f"guoshu_weekly_tool__weekly_mcp_{hashlib.sha256(str(_MCP_PATH).encode()).hexdigest()[:12]}"
_module = sys.modules.get(_MODULE_NAME)
if _module is None:
    _module = types.ModuleType(_MODULE_NAME)
    _module.__file__ = str(_MCP_PATH)
    sys.modules[_MODULE_NAME] = _module
    exec(compile(_MCP_PATH.read_text(encoding="utf-8"), str(_MCP_PATH), "exec"), _module.__dict__)
_call = _module.__dict__["call"]
_invalid = _module.__dict__["invalid_argument"]


async def weekly_task_query(
    board: str = "",
    category: str = "",
    status: str = "",
    owner: str = "",
    keyword: str = "",
    project_group: str = "",
    limit: int = 200,
) -> str:
    """Query formal weekly-report tasks with optional filters.

    The formal-task caliber (is_deleted = 0 AND workflow_status = 'published') is
    applied by the service, and echoed back in the caliber field -- cite it when
    reporting counts. total_count is the unfiltered-by-limit total; has_more says
    whether rows were truncated.

    Args:
        board: Board code (tech/group) or name.
        category: Category name, matched loosely.
        status: Business status 0未开始/1进行中/2已完成/3已停用, empty for all.
        owner: Owner or lead name; multi-value columns are space-stripped first.
        keyword: Substring of the task name.
        project_group: 专项组 name, matched exactly -- it is its own column, not a
            category and not a board. For "这个组的人都有谁" prefer
            weekly_person_stats scope="group_roster", which de-duplicates the
            people server-side: 标准安全组 has 19 tasks but only 9 牵头人.
        limit: Max rows to return, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_task_query",
        {
            "board": board,
            "category": category,
            "status": status,
            "owner": owner,
            "keyword": keyword,
            "project_group": project_group,
            "limit": bounded,
        },
    )


async def weekly_task_detail(task: str) -> str:
    """Fetch one formal task with its group detail, recent progress and year goals.

    Use this for "tell me about task X" questions. completion_time in the group
    detail is free text -- never compute dates from it (R-12).

    The reply carries two sets of owner columns: single-value lead_owner_name /
    project_owner_name on the task row, and multi-value lead_owner_names /
    project_owner_names inside group_detail. For a group-board task they disagree
    on all 46 rows, so quote the group_detail pair -- the caliber says so
    explicitly whenever they differ. Task 101 reads 陈志远 on the task row and
    刘海涛,韩雪峰 in the group detail.

    Args:
        task: Task id or task name (fuzzy match on name).
    """
    if not task.strip():
        return _invalid("task must not be empty")
    return await _call("weekly_task_detail", {"task": task})
