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


async def weekly_aggregate(
    group_by: str,
    board: str = "",
    metric: str = "count",
    top: int = 0,
    order_by: str = "",
    ascending: bool = False,
) -> str:
    """Aggregate formal tasks by one dimension.

    Empty groups are preserved (the service puts the caliber on the JOIN's ON
    clause, per R-02/R-08), so a zero row means genuinely zero tasks -- do not
    treat a missing group as zero without checking here first.

    category and primary_category are two different questions: tasks only attach
    to 二级分类, and 一级分类 is reached through the parent_id hop. Asking for
    分类 with group_by="category" answers with 47 buckets where the question
    wanted 6.

    status and workflow_status are two different vocabularies over two different
    populations. status is the business progress of published tasks (未开始 /
    进行中 / 已完成 / 已停用, 128 tasks); workflow_status is where a task sits in
    the approval flow (published / pending_audit / ... , 150 tasks) and is the
    ONE dimension that drops the publish gate, because gating it would leave the
    single published bucket and hide the 22 tasks the question is about.

    Args:
        group_by: One of board / category / primary_category /
            top_sub_per_primary / status / workflow_status / project_group /
            owner. top_sub_per_primary answers "每个一级分类下任务数最多的二级分类
            是哪个": the ranked unit is the SUBCATEGORY, one row per 一级分类
            (11 rows), ties inside a group settled by category id. That is a
            different question from weekly_rank mode=per_group
            group_by=primary_category, which returns each 一级分类's top TASK.
        board: Optional board code or name to scope the aggregation. For
            primary_category this scopes the category tree's own board, which is
            a different path from the task's board.
        metric: Only "count" is supported.
        top: When > 0, hard-cuts to that many groups in SQL and says in the
            caliber how many groups exist in total. Use it for "前 N 个" so the
            row count is the answer -- ties past the boundary are excluded by
            the question, not missing from the data. Ignored for workflow_status,
            whose reply also carries a totals block with the unpublished count
            and the published share already computed, plus
            active_pending_tasks -- the 18 tasks still awaiting someone's action
            (pending_audit 7 + pending_leader 5 + pending_fill 3 + signing 3).
            "有多少流程需要继续推动 / 当前审批积压怎样" is that number: quote it
            instead of summing buckets, which tends to pull in the 3 rejected
            (already back with the filer) and reach 21, and do not substitute
            unpublished_tasks, which counts rejected and cancelled too.
            The same reply also carries unpublished_task_list: all 22 unpublished
            tasks by id / task_no / task_name / workflow_status, ordered by
            state. "哪些任务在等领导审批" and "会签阶段有哪些任务" are answered by
            filtering that list on workflow_status -- 5 tasks at pending_leader
            (14 / 53 / 58 / 112 / 129) and 3 at signing (70 / 141 / 147). These
            tasks are outside the formal set, so weekly_task_query returns none
            of them under R-01; without this list the only route left is the
            submission table, whose 审批中的填报单 are forms rather than tasks
            and count 15 and 9 instead.
        order_by: "finish_rate" re-orders the project_group and primary_category
            breakdowns by completion rate. Both carry finished and
            finish_rate_pct next to cnt, already rounded on the server -- quote
            the column, do not divide the counts yourself. The default ordering
            is by task count, off which "完成率最低的 3 个组" and "哪类推进最快"
            cannot be read: the biggest bucket is not the fastest one. 关键技术攻关
            leads on task count with 18 yet finishes 27.8%, while 国家数据基础设施
            finishes 45.5% on 11. Fewest finished is likewise not lowest rate:
            治理合规组 2 of 10 = 20.0% sits ABOVE 数据基础设施组 2 of 15 = 13.3%
            though both finished 2.
        ascending: True with order_by="finish_rate" puts the lowest rate first,
            which is what "完成率最低" asks for.
    """
    if not group_by.strip():
        return _invalid("group_by must not be empty")
    try:
        cut = max(0, int(top))
    except TypeError, ValueError:
        return _invalid("top must be an integer")
    return await _call(
        "weekly_aggregate",
        {
            "group_by": group_by,
            "board": board,
            "metric": metric,
            "top": cut,
            "order_by": order_by,
            "ascending": bool(ascending),
        },
    )


async def weekly_freshness() -> str:
    """Report each board's latest progress time, how far it lags, and task counts.

    Call this before answering any relative-time question ("this week", "recently").
    Anchor to these snapshot times, never to the machine wall clock.

    "数据更新到什么时候了" wants BOTH numbers, not just the timestamp: quote
    overall.newest together with overall.days_behind (the snapshot date minus that
    timestamp). The per-board rows carry their own days_behind; overall is the
    whole-library pair, which is what a question about "整个看板" asks for --
    picking either board row answers a narrower question.

    Asked about ONE board's data currency ("技术组数据更新到什么时候"), answer the
    formal caliber, not the board row: latest_progress comes from
    task.latest_progress_time, which counts unpublished rows and reads 2026-08-09
    for the tech board while its newest published progress is 2026-07-31.
    Use published_progress[].newest_published_progress, and for the tech board say
    tech_import.newest_finished_batch (2026-07-31) -- adding that the 08-15 batch
    (tech_import.newest_unfinished_batch) is still processing, so it cannot count
    as "updated to".
    """
    return await _call("weekly_freshness", {})


async def weekly_import_audit(
    limit: int = 200,
    reconcile_rows: bool = False,
    orphans: bool = False,
    latest_finished: bool = False,
) -> str:
    """Reconcile Excel import batches against distinct snapshot dates (R-09/R-10).

    Compare batch_count with distinct_dates and distinct_import_times to tell a
    single import from repeated ones.

    Args:
        limit: Max batch rows to return, capped at 200.
        latest_finished: When True, return the tasks the newest FINISHED batch
            touched, batch pick included. Use it for "最近一批跑完的导入影响了哪些
            任务": 跑完 means status = 1, and the newest batch by date is id 20,
            which is still status 0 with 0 rows landed -- answering off the plain
            listing's first row describes a batch that never ran. The finished one
            is id 19 with 17 tasks. Do not pick the batch yourself and then query
            it: the pick is the part that goes wrong.
        reconcile_rows: When True, also look up what each batch actually landed
            (via task_progress.import_id) and compare it against the batch's own
            changed_tasks. Required for "声明与实际对不上" questions -- the
            default path reports the declared number only and cannot tell you
            whether it is true.
        orphans: When True, check the reverse direction: progress rows whose
            import_id points at a batch that does not exist. Answer with
            orphan_rows as returned -- zero means referential integrity holds,
            which is the finding, not an empty result to work around. Note
            rows_without_import beside it counts rows that never came from an
            import at all; those are not orphans.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_import_audit",
        {
            "limit": bounded,
            "reconcile_rows": bool(reconcile_rows),
            "orphans": bool(orphans),
            "latest_finished": bool(latest_finished),
        },
    )


async def weekly_scale(by: str = "board", mode: str = "totals", year: int = 2026) -> str:
    """Cross-section formal tasks over several child tables at once, de-duplicated.

    Use this instead of calling weekly_aggregate once per dimension: joining the
    child tables separately and pasting the numbers together is where fan-out
    creeps in. Every child count here is COUNT(DISTINCT ...), so per-group
    milestones sum to the whole-library milestone total -- if your numbers sum to
    more than that, they were multiplied by another JOIN.

    totals and completeness answer different questions: totals gives child-row
    counts (how many milestones), completeness gives task counts (how many tasks
    have at least one milestone). Do not use one to answer the other.

    Args:
        by: Grouping axis: board / project_group / primary_category.
        mode: "totals" tasks plus milestone / attachment / annual-goal counts.
            "completeness" how many tasks have a goal / milestone / progress.
            "intensity" published progress rows and rows per task, with
            zero-period tasks kept in the denominator.
        year: Which year the annual-goal column looks at. Ignored by intensity.
    """
    try:
        yr = int(year)
    except TypeError, ValueError:
        return _invalid("year must be an integer")
    return await _call("weekly_scale", {"by": by, "mode": mode, "year": yr})
