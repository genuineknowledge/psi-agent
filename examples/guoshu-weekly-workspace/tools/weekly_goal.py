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


async def weekly_year_goal_query(task: str = "", year: int = 0, board: str = "", limit: int = 200) -> str:
    """List annual goals and milestone summaries. One row per task per year.

    Use this for "what is task X's 2026 goal" and for board-wide goal listings.
    Read total_count / total_tasks for counting questions -- rows may be truncated
    at 200 while the totals stay exact.

    Args:
        task: Task id or name; empty covers every formal task.
        year: Four-digit year; 0 covers all years.
        board: Board code or name (group / tech) to keep only that board's goals.
            Ask for the board here rather than looping this tool over its tasks:
            the board lives on task, not on the goal row, so a per-task loop is
            both 46 calls and unable to give the board's own totals (109 goal rows
            over 46 tasks, against 313 over 128 board-wide).
        limit: Max rows, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
        yr = max(0, int(year))
    except TypeError, ValueError:
        return _invalid("year and limit must be integers")
    return await _call(
        "weekly_year_goal_query",
        {"task": task, "year": yr, "board": board, "limit": bounded},
    )


async def weekly_year_goal_stats(
    scope: str = "by_year",
    year: int = 0,
    year_to: int = 0,
    min_years: int = 3,
    in_progress_only: bool = False,
    board: str = "",
    top: int = 8,
    include_informal: bool = False,
) -> str:
    """Aggregate annual-goal coverage: which years are set, and who lacks a goal.

    Use this instead of listing goals and counting by hand. Coverage counts tasks
    that have no goal row at all, which listing goals cannot show.

    Args:
        scope: by_year (goals and tasks per year) / coverage (share of formal tasks
            holding a goal for year) / missing (tasks without one) /
            missing_by_group (missing counts per 专项组) / span (average years per
            task, plus tasks reaching min_years) / multi_year (tasks holding goals
            in both year and year_to).
        year: Primary year. Required except for by_year and span.
        year_to: Second year, for multi_year.
        min_years: Threshold for span; inclusive.
        in_progress_only: For missing, keep only 在办任务 (status 0 未开始 and
            1 进行中). "在办任务还没定目标" asks about that subset -- 已完成 or
            已暂停 tasks without a goal are not a gap and inflate the row set.
        board: Optional board code or name; scopes every scope to one board.
            Use it for "某看板哪些任务没设目标" rather than filtering the
            whole-library rows by eye, which silently drops the total_count.
        top: Row cap for the listing scopes.
        include_informal: True makes by_year and span count the whole goal table
            (387 rows) instead of only goals on formal tasks (313). Use it for
            "目标表里一共多少条年度目标"; keep the default for "正式任务设了多少
            目标", which is the reporting caliber. coverage / missing /
            missing_by_group ignore it -- they measure a gap against the formal
            task set, and widening the denominator to deleted and unpublished
            tasks would make that gap meaningless.
    """
    try:
        bounded = max(1, min(200, int(top)))
        yr = max(0, int(year))
        yr2 = max(0, int(year_to))
        span = max(1, int(min_years))
    except TypeError, ValueError:
        return _invalid("year, year_to, min_years and top must be integers")
    return await _call(
        "weekly_year_goal_stats",
        {
            "scope": scope,
            "year": yr,
            "year_to": yr2,
            "min_years": span,
            "in_progress_only": bool(in_progress_only),
            "board": board,
            "top": bounded,
            "include_informal": bool(include_informal),
        },
    )


async def weekly_milestone_stats(
    scope: str = "summary",
    by: str = "category",
    year: int = 0,
    category: str = "",
    min_total: int = 0,
    kind: str = "task_done_milestones_open",
    top: int = 8,
) -> str:
    """Aggregate milestone completion. weekly_milestone_query only lists rows.

    Milestone status is a two-value code: 1 已完成, 0 未完成. Completion rates come
    from the server; do not derive them by counting listed rows.

    Args:
        scope: summary (totals and finish rate) / by_dimension (grouped by `by`) /
            deleted (soft-delete audit: 566 active, 36 deleted, table-wide) /
            fully_deleted (the 3 tasks whose milestones were ALL soft-deleted.
            Required for "有没有任务的里程碑被全部删掉了": deleted gives only the
            table totals, and every listing scope filters deleted rows out, so
            without this the question has no route at all. Judged by NOT EXISTS on
            the surviving rows -- "has a deleted milestone" spans 23 tasks, an
            order of magnitude more) / per_task (counts per task, zero-milestone
            tasks kept, plus top_tie_count: the top bucket is a 23-way tie at 6
            milestones, so "里程碑最多的任务是哪条" is the first row alone; the
            summary also carries tasks_with_milestone and coverage_pct, which is
            how "多少任务配置了 N 年里程碑" gets answered -- see `year`) /
            mismatch (task status vs milestone status disagreements; read the
            `year` note below before picking one -- year changes the question here,
            not just the row count, and it moves the two kinds in opposite
            directions).
        by: Dimension for by_dimension: year / category / group_name /
            project_group / status / task_status / primary_category / reporter_id /
            owner_id.
            reporter_id answers "里程碑都是谁报的 / 各几条" -- 47 filers, counted
            server-side; owner_id is the responsible party, a different column
            answering a different question. Do not read either off the milestone
            listing: it caps at 200 rows against 602 milestones.
            project_group is the task's 项目组 and is NOT group_name, which is the
            milestone row's own short label -- the two axes do not even share a
            value set (project_group has 11 buckets: 关键技术攻关组/算力网络组/
            国家工程办 ...; group_name has 6: 区域组/安全组/技术组 ...).
            "哪些项目组的里程碑记录完成比例较高" and "需要重点核实" are project_group;
            answering it off group_name reports 安全组 62.8% instead of
            关键技术攻关组 81.8%. Sorted by finish_rate_pct, so the first rows are
            the "较高" answer and the last rows the "较低" one. These rates are
            filing status, not project-group performance -- say so.
            primary_category groups by the task's
            top-level category (t.category_id's parent -- task categories only go
            two levels deep) and is NOT the same axis as category, which is the
            milestone row's own label. "哪个一级分类的里程碑完成率最高" is 改革与治理
            at 67.5% under primary_category; by=category answers a different
            question and reads 国家任务 58.9%. This axis sorts by finish_rate_pct,
            so the first row is the answer; pass min_total to keep small buckets
            from topping the list.
        year: Restrict to one milestone year; 0 covers all. Milestones exist for
            2025 and 2026 only. Under scope=per_task the year is applied to the
            LEFT JOIN, so tasks with none of that year's milestones stay in the
            denominator as zeros -- that is what makes coverage answerable:
            "多少任务配置了 2026 年里程碑" is summary.tasks_with_milestone 112 of
            tasks 128, coverage_pct 87.5. Read coverage_pct off the summary rather
            than dividing two numbers yourself. Under scope=mismatch this is not a
            narrowing filter -- it changes the question, and the two kinds move
            opposite ways. task_done_milestones_open is an EXISTS test, so restricting the
            year only hides contradictions: all years gives 6 tasks, year=2026
            gives 3, and the 3 dropped (50/111/126) have their open milestones in
            2025 -- a task marked done with a 2025 milestone still open is the
            harder contradiction, so leave year at 0 for "哪些已完成任务仍有未完成
            里程碑". milestones_done_task_open is an ALL test, so restricting the
            year RELAXES it: all years gives 8 tasks, year=2026 gives 22, and
            those extra ones still have unfinished 2025 milestones. Either number
            is defensible; state which span you used.
        category: Restrict to one milestone category.
        min_total: For by_dimension, drop buckets below this count; inclusive.
            Rates over small buckets swing wildly -- set this before calling any
            bucket the highest or lowest.
        kind: For mismatch: task_done_milestones_open or milestones_done_task_open.
        top: Row cap for the listing scopes.
    """
    try:
        bounded = max(1, min(200, int(top)))
        yr = max(0, int(year))
        floor = max(0, int(min_total))
    except TypeError, ValueError:
        return _invalid("year, min_total and top must be integers")
    return await _call(
        "weekly_milestone_stats",
        {
            "scope": scope,
            "by": by,
            "year": yr,
            "category": category,
            "min_total": floor,
            "kind": kind,
            "top": bounded,
        },
    )
