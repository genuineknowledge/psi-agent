"""PostgreSQL query templates for the formal ChatBI source (first batch).

The demo mock answers from weekly_mock over MySQL; the formal source is
O2OA's PostgreSQL (``O2OA-DB`` / ``public``).  Templates follow the example
queries in the 2026-09-08 oa-weekly field note and always carry the hard
guards from ``_admission``.  Only the highest-frequency reads are migrated
here; the rest of the 31 tools follow the same pattern in later batches
(see ``CHATBI_o2oa_接入说明.md``).

Every builder returns ``(sql, params)`` where ``params`` are ``%s`` values in
statement order (psycopg 3 style).
"""

from __future__ import annotations

import _admission as adm


def published_task_list(
    board_code: str,
    category_name: str | None = None,
    keyword: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """5.1 已发布任务清单(含分类/负责人), board=tech|group 必带。

    category_name 命中一级或二级分类名;keyword 命中任务名。清单封顶由
    caller 决定,返回行数超过 limit 时应如实说明(has_more 语义在上层)。
    """
    code, hint = adm.check_board_code(board_code)
    if hint:
        raise ValueError(hint)
    where = [
        "t.is_deleted = 0",
        "t.workflow_status = %s",
        "b.code = %s",
    ]
    params: list[object] = [adm.PUBLISHED, code]
    if category_name:
        where.append("c.name = %s")
        params.append(category_name)
    if keyword:
        where.append("t.task_name ILIKE %s")
        params.append(f"%{keyword}%")
    where_sql = "\n  AND ".join(where)
    sql = f"""
SELECT t.id, t.task_no, t.task_name,
       t.overall_goal, t.annual_goals,
       c.name            AS category,
       t.project_owner_name AS reporter_name,
       t.lead_owner_name  AS lead_owner,
       {adm.normalize_ts_sql("t.latest_progress_time")} AS latest_progress_time,
       {adm.normalize_ts_sql("t.published_at")}         AS published_at
FROM task t
JOIN task_board    b ON b.id = t.board_id
JOIN task_category c ON c.id = t.category_id
WHERE {where_sql}
ORDER BY t.sort_order, t.id
LIMIT {int(limit)}
"""
    return sql, tuple(params)


def latest_formal_progress(board_code: str, limit: int = 200) -> tuple[str, tuple]:
    """5.2 已发布任务的最新正式进展(rule 2: is_published = 1 的展示版本)。"""
    code, hint = adm.check_board_code(board_code)
    if hint:
        raise ValueError(hint)
    sql = f"""
SELECT t.task_name,
       p.latest_progress, p.next_work,
       p.progress_date,
       p.reporter_id,
       {adm.normalize_ts_sql("p.report_time")} AS report_time
FROM task_progress p
JOIN task t ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id
WHERE t.is_deleted = 0
  AND t.workflow_status = %s
  AND b.code = %s
  AND {adm.sql_published_progress("pg", "p")}
  AND p.version_no = (
        SELECT MAX(p2.version_no) FROM task_progress p2
        WHERE p2.task_id = p.task_id AND p2.is_published = 1)
ORDER BY t.task_name
LIMIT {int(limit)}
"""
    return sql, (adm.PUBLISHED, code)


def task_detail_with_goals(
    task_id: int,
    year: int | str,
) -> tuple[str, tuple]:
    """5.3 某任务 年度目标 + 里程碑 + 集团板扩展(须先命中已发布任务)。

    仅集团看板任务在 task_group_detail 有行(1:1);技术组任务该段为 NULL,
    上层需按 board.code 决定是否读取扩展列(rule 4)。
    """
    y, hint = adm.check_year(year)
    if hint:
        raise ValueError(hint)
    sql = """
SELECT t.id, t.task_name, t.overall_goal, t.annual_goals,
       y.year, y.current_year_goal, y.milestone_summary,
       m.content AS milestone,
       g.target_result, g.progress_effect,
       g.lead_owner_names, g.project_owner_names
FROM task t
LEFT JOIN task_year_goal  y ON y.task_id = t.id AND y.year = %s
LEFT JOIN task_milestone  m ON m.task_id = t.id AND m.is_deleted = 0
LEFT JOIN task_group_detail g ON g.task_id = t.id
WHERE t.id = %s
  AND t.is_deleted = 0
  AND t.workflow_status = %s
"""
    return sql, (y, int(task_id), adm.PUBLISHED)
