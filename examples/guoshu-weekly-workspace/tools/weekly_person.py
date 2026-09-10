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


async def weekly_person_stats(
    scope: str = "workload",
    role: str = "lead_owner",
    project_group: str = "",
    board: str = "",
    top: int = 200,
) -> str:
    """Aggregate formal tasks by person: workload, cross-group spread, id formats.

    weekly_owner_roles answers "how many does THIS person have"; this answers the
    population-level questions. Every count is computed server-side -- counting
    people by reading rows back is the biggest source of wrong answers here.

    Args:
        scope: workload (tasks per person, ties ordered by name; the reply carries
            tied_at_top and top_task_count) / workload_top (the busiest person WITH
            every tie kept, decided server-side by HAVING = MAX. "谁任务最多" over
            the tech board is four people at 4 tasks each, so workload + top=1 hard-
            cuts to one row while this returns all four. Pick by the question: a
            plain 「谁最多」 wants this one, 「最多的那一个」 wants top=1. The row
            count IS the answer -- do not trim it to one, and do not pad it with
            the runner-up) / workload_summary
            (global average, distinct head-count, max and min) / single_task (people
            holding exactly one) / cross_group (people spanning 2+ 专项组, with the
            group list) / dual_role (people who are both 牵头人 and 项目负责人) /
            id_format (owner_user_id written as 纯数字工号 / u 前缀 / NDG 域账号) /
            id_variants (same name carrying more than one id; 0 rows means none
            exist) / id_longest (one row per DISTINCT identifier ordered by
            character length, with task_count beside it; the question asks which
            identifier is longest, so an id held by 3 tasks still counts once,
            and equal lengths are genuine ties to be stated together) /
            reporters (progress rounds filed per person) / reporter_count (distinct
            filers) / reviewers (progress rows reviewed per person) / self_review
            (rows where filer and reviewer are the same id) / group_roster (the
            people of ONE 专项组, de-duplicated: use this for "标准安全组的牵头人
            都有谁" rather than listing the group's tasks -- 19 tasks there carry
            only 9 distinct 牵头人, so counting task rows over-counts people).
        role: Which person column to group by -- three DIFFERENT populations, not
            three names for one:
            owner (owner_user_id, 任务主责人) -- one person per task, 45 distinct
            on the tech board. This is what a plain 「负责人」 means: "有多少人当
            负责人"、"谁手里任务最多"、"平均每个负责人几个任务" all read off here.
            Because this column holds work ids rather than names, the listing
            scopes add person_name beside person: "10515" is 姚立诚 and "u3208"
            is 余承志. Answer 「是谁」 with person_name -- quoting the id back
            reads as an unanswered question even when the counts are right. The
            two columns are 1:1 across all 47 formal-task owners, so the name is
            the id's name and not one arbitrary pick among several.
            project_owner (project_owner_name) -- also 45 distinct, the named
            counterpart of the same role.
            lead_owner (lead_owner_name, 分管领导/牵头人) -- only 12 distinct on
            the tech board, because one 分管领导 covers many tasks. Answering a
            「负责人」 question off this column reports 12 where the answer is 45.
        project_group: Required by group_roster, ignored by every other scope.
            Matched exactly; weekly_aggregate group_by="project_group" lists the
            11 valid names.
        board: Board code or name (tech / group) to scope every count to one
            board. Needed whenever the question says 「某看板」: the whole-library
            default mixes both boards' people into one head-count.
        top: Row cap, capped at 200. Match it to the question's number: a singular
            question ("任务量最大的牵头人是谁", "最长的标识是哪一个") is top=1 and
            answers off the single row returned; "前 10 位" is top=10. The workload
            and id_longest scopes also return tied_at_top -- three people tie at 14
            tasks and four identifiers tie at the longest length, so quote that
            count if the tie matters, but do NOT expand a singular answer into the
            whole tied set: that answers a different question.
    """
    try:
        bounded = max(1, min(200, int(top)))
    except TypeError, ValueError:
        return _invalid("top must be an integer")
    return await _call(
        "weekly_person_stats",
        {
            "scope": scope,
            "role": role,
            "project_group": project_group,
            "board": board,
            "top": bounded,
        },
    )
