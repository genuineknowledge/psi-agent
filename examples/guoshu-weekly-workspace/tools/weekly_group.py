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


async def weekly_group_detail_query(
    task: str = "",
    fields: str = "",
    contains: str = "",
    field: str = "",
    status: str = "",
    non_empty: str = "",
    order_by: str = "",
    limit: int = 200,
) -> str:
    """Query the group board's own detail table: goals, measures, owners, due text.

    The group board (集团组) keeps 目标成果 / 实施举措 / 进度成效 / 完成时间 and its
    owner columns in a separate table. weekly_task_query does not carry any of
    those columns, so use this tool for group-board questions about them. The
    owner columns are here too: pass fields="lead_owner_names,project_owner_names"
    for "牵头人和项目负责人都有谁", they are not on the shared task row.

    Those multi-value columns are NOT the single-value lead_owner_name /
    project_owner_name that weekly_task_query returns -- on 46 of the group
    board's tasks the two disagree (task 97 reads 秦怀瑾 on the task row and
    胡建国,方永康,邓少华 here), so a group-board owner question answered from the
    task row is answered off the wrong column. Requesting either column also
    returns lead_owner_count / project_owner_count, the head-count already
    computed over both delimiters; quote it instead of counting commas.

    completion_time is display text like "2026年内" or "2026Q4", not a date. Filter
    it with contains, and never compute date arithmetic on it.

    Args:
        task: Task id or name; empty covers the whole board.
        fields: Comma-separated columns to return; empty returns the common set.
        contains: Substring filter applied to the column named by field.
        field: Which column contains filters on; required when contains is set.
        status: Task business status 0/1/2/3. The status lives on the task and the
            effect text lives here, so "状态与成效描述矛盾" needs both sides at
            once: status="0" plus non_empty="progress_effect".
        non_empty: Comma-separated columns that must be non-empty. Without it the
            contradiction question also collects tasks that are 未开始 and blank,
            which are not contradictory at all.
        order_by: "progress_time" orders by the task's latest progress, newest
            first, and returns latest_progress_time alongside. Reach for it only
            when the question is about recency itself -- "最近报的"、"按上报时间
            排" -- NOT merely because it says 当期/当前 or asks for the first N
            rows. Those listings are answered in the default task-id order: the
            board's own numbering IS the reading order, and re-sorting by progress
            time returns a different 8 tasks (103/128/113/149... instead of
            97-104), which is a different question's answer.
        limit: Max rows, capped at 200. The reply carries total_count; when it
            exceeds the rows returned, the listing is short, not the answer.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    if contains.strip() and not field.strip():
        return _invalid("field is required when contains is set")
    return await _call(
        "weekly_group_detail_query",
        {
            "task": task,
            "fields": fields,
            "contains": contains,
            "field": field,
            "status": status,
            "non_empty": non_empty,
            "order_by": order_by,
            "limit": bounded,
        },
    )


async def weekly_group_owner_query(person: str = "", role: str = "lead", limit: int = 200) -> str:
    """Find group-board tasks by owner, matching multi-value owner columns exactly.

    The owner columns hold comma-separated values, so substring matching collides
    across people. The server matches per element instead.

    Lead (牵头人) and project owner (项目负责人) are different roles on different
    columns. Pick the one the question asks about; do not merge them.

    Args:
        person: Person id or name; empty lists every task's owners for the role.
        role: lead or project.
        limit: Max rows, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_group_owner_query",
        {"person": person, "role": role, "limit": bounded},
    )


async def weekly_group_history(
    task: str = "",
    version_no: int = 0,
    by: str = "",
    latest_only: bool = False,
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    last_months: int = 0,
    limit: int = 200,
) -> str:
    """Query the group board's progress history -- it lives in its own table.

    weekly_progress_history and weekly_progress_range return nothing for group
    tasks: the group board's progress is not in task_progress at all. This is the
    only entry point for it.

    Read total_count / total_tasks for counting questions -- rows may be truncated
    at 200 while the totals stay exact. Relative windows are anchored to the data
    snapshot date on the server, so do not compute dates yourself.

    "最近三个月" is last_months=3, not last_days=90. Calendar months land on
    2026-05-15 and 90 days on 2026-05-17, and three May rows sit in between, so
    substituting one for the other changes the May bucket from 16 to 13. Passing
    both is an error rather than a silently merged third window.

    Args:
        task: Task id or name; empty covers the whole board.
        version_no: Return one specific period (larger is newer, per task).
        by: Empty lists rows; year / month / quarter / task / reporter counts them.
            task returns task_id alongside the name and breaks ties by that id,
            not by task name. This matters for "报得最多的前 5 条": eight tasks tie
            at 11 periods, so name order answers 127/105/133/120/104 while id order
            answers 104/105/115/120/127 -- a different set, not a reshuffle. Every
            other ranking here breaks ties by id too, so quote task_id when the
            question asks which tasks.
            lag ranks tasks by days since their last report -- use it for "哪些
            任务最久没报", and read lag_days rather than deriving it from dates.
            Tasks that never reported are absent from that ranking by definition;
            total_tasks counts the ones on it, not the whole board.
            linkage counts how many history rows carry a workflow_submission_id.
            It deliberately drops the row-level publish gate: a fill rate needs all
            404 rows as its denominator, and the 362 published ones would hide how
            the 42 drafts are linked. linked_rows comes back as 0, which means this
            table simply has no link to the submission forms -- report that, do not
            read it as a lookup failure or retry with other parameters.
        latest_only: Keep only each task's newest published period. This is the
            answer to "集团看板各任务最新一期的进度成效"; the current-effect column
            on weekly_group_detail_query is a different thing -- it is the
            denormalised 当前 value, not the newest history period, and the two
            disagree on some tasks. Ask "最新一期" and you want this flag.
        date_from: Inclusive start on report time, YYYY-MM-DD.
        date_to: Inclusive end on report time, YYYY-MM-DD.
        last_days: Window of N days ending at the snapshot date; 0 disables it.
        last_months: Window of N calendar months ending at the snapshot date;
            0 disables it. Mutually exclusive with last_days.
        limit: Max rows, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
        version = max(0, int(version_no))
        days = max(0, int(last_days))
        months = max(0, int(last_months))
    except TypeError, ValueError:
        return _invalid("version_no, last_days, last_months and limit must be integers")
    if days and months:
        return _invalid("pass either last_days or last_months, not both: their boundaries differ")
    return await _call(
        "weekly_group_history",
        {
            "task": task,
            "version_no": version,
            "by": by,
            "latest_only": bool(latest_only),
            "date_from": date_from,
            "date_to": date_to,
            "last_days": days,
            "last_months": months,
            "limit": bounded,
        },
    )


async def weekly_group_stats(scope: str = "owners", top: int = 8, min_rounds: int = 0) -> str:
    """Aggregate stats over the group board that listing rows cannot answer.

    Use this instead of fetching rows and counting by hand: the counts here are
    exact and cost one call.

    Args:
        scope: project_group_raw (the detail TABLE grouped by 专项组, ungated -- 11
            groups summing to 55 rows. Use it for "集团明细按专项组分 / 各有多少条",
            which asks how many detail rows exist, not how many tasks are in flight.
            rows_ is the answer; formal_rows sits alongside as the gated tier (46) for
            comparison only. Do NOT use this for "各专项组有多少任务" -- that is a task
            count and belongs to weekly_aggregate group_by="project_group") /
            owners (multi vs single lead, distinct leads) / separators (how the
            multi-value owner column is delimited -- comma, ideographic comma,
            both, or a single person with no delimiter at all) / owner_widths
            (how many people share one owner cell, widest first) /
            completion_time (ISO vs free text vs blank) / completion_time_values
            (the distinct strings actually stored -- report them verbatim rather
            than folding them into categories of your own) /
            completion_time_formats (those strings grouped into 6 写法 buckets;
            "各种写法各有多少条" wants this, and answering it with the 28 distinct
            values of completion_time_values is off by a whole magnitude) /
            overdue (tasks past their planned completion date and still open.
            completion_time is display text that nothing else may date-arithmetic,
            so this scope normalises the two parseable 写法 on the server -- ISO
            dates as-is, YYYYQn to that quarter's last day -- and returns
            unparsable_count for the 34 free-text rows, which are UNJUDGEABLE
            rather than on time. Looking at ISO dates alone finds nothing; the
            quarter rows surface task 123, 2026Q2 = 2026-06-30, 46 days past the
            snapshot) /
            field_lengths
            (target_result char stats) / attachments (per-task counts,
            zero-attachment tasks kept -- a listing, so top cuts it; pass top=46
            for the whole board) / attachment_distribution (how many tasks hold
            0/1/2/... attachments, counted on the server. Use it for "how many
            tasks have exactly one attachment": counting that off the default
            8-row listing gives 21/4/4 where the truth is 17/3/5) /
            history_rounds (published periods
            per task) / status_effect_conflict (the 6 rows contradicting
            themselves: status 0 未开始 while progress_effect describes work
            already delivered) / effect_consistency (each task's current
            progress_effect against its newest published history row; same = 0
            rows come first, so check whether any exist before saying they all
            agree. Not the same question as status_effect_conflict -- two copies
            of the same sentence agree with each other and still contradict the
            status).
        top: Row cap for the listing scopes.
        min_rounds: For history_rounds, also count tasks with at least this many
            periods. Inclusive: "at least 5" means 5 or more.
    """
    try:
        bounded = max(1, min(200, int(top)))
        threshold = max(0, int(min_rounds))
    except TypeError, ValueError:
        return _invalid("top and min_rounds must be integers")
    return await _call(
        "weekly_group_stats",
        {"scope": scope, "top": bounded, "min_rounds": threshold},
    )
