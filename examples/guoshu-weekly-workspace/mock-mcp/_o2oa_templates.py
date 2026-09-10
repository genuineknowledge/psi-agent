"""PostgreSQL query templates for the formal ChatBI source.

The demo mock answers from weekly_mock over MySQL; the formal source is O2OA's
PostgreSQL (``O2OA-DB`` / ``public``).  Templates follow the example queries in
the 2026-09-08 oa-weekly field note and always carry the hard guards from
``_admission``.  Only the highest-frequency reads are migrated here; the rest of
the 31 tools follow the same pattern in later batches (see
``CHATBI_o2oa_接入说明.md``).

Every builder returns ``(sql, params)`` where ``params`` are ``%s`` values in
statement order (psycopg 3 style).  Builders raise:

  * ``ValueError``        -- an argument violates a documented rule (bad board
                             code, missing explicit year, ...);
  * ``PermissionError``   -- the query needs one of the four *optional* tables
                             that this grant does not include; the caller turns
                             ``str(exc)`` into the answer instead of running SQL.
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
        adm.sql_task_admission("pg", "t"),
        "b.code = %s",
    ]
    params: list[object] = [code]
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
JOIN task_board    b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
JOIN task_category c ON c.id = t.category_id AND {adm.sql_soft_delete("c")}
WHERE {where_sql}
ORDER BY t.sort_order, t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def latest_formal_progress(board_code: str, limit: int = 200) -> tuple[str, tuple]:
    """5.2 已发布任务的最新正式进展(rule 2: is_published = 1 的展示版本)。

    取每个任务 ``is_published = 1`` 中 ``version_no`` 最大的一行。用
    ``DISTINCT ON`` 而不是逐行相关子查询:前者在 PG 上是一次扫描加排序,
    后者对每个任务再跑一次 ``MAX`` 子查询(任务数与版本数一上来就是成倍的
    往返开销)。外层再按任务名排序,保持清单可读。
    """
    code, hint = adm.check_board_code(board_code)
    if hint:
        raise ValueError(hint)
    sql = f"""
SELECT s.task_id, s.task_name, s.latest_progress, s.next_work,
       s.progress_date, s.reporter_id, s.report_time, s.version_no
FROM (
    SELECT DISTINCT ON (t.id)
           t.id AS task_id, t.task_name,
           p.latest_progress, p.next_work, p.progress_date, p.reporter_id,
           {adm.normalize_ts_sql("p.report_time")} AS report_time,
           p.version_no
    FROM task_progress p
    JOIN task t     ON t.id = p.task_id
    JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
    WHERE {adm.sql_task_admission("pg", "t")}
      AND b.code = %s
      AND {adm.sql_published_progress("pg", "p")}
    ORDER BY t.id, p.version_no DESC
) s
ORDER BY s.task_name
LIMIT %s
"""
    return sql, (code, int(limit))


def historical_progress_versions(
    task_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> tuple[str, tuple]:
    """Rule 2 exception: 历史版本进展,须任务已发布且版本已通过(``status = 3``)。

    ``date_from`` / ``date_to`` 按 ``progress_date`` 过滤,格式 ``YYYY-MM-DD``
    (该列类型是 date,直接比较即可,不需要文本时间归一化)。
    """
    conditions = [
        ("t.id = %s", int(task_id)),
        (adm.sql_task_admission("pg", "t"), None),
        (adm.sql_historical_progress("pg", "p"), None),
    ]
    if date_from:
        conditions.append(("p.progress_date >= %s", date_from))
    if date_to:
        conditions.append(("p.progress_date <= %s", date_to))
    where_sql = "\n  AND ".join(clause for clause, _ in conditions)
    params = tuple(value for _, value in conditions if value is not None)
    sql = f"""
SELECT t.task_name, p.version_no, p.latest_progress, p.next_work,
       p.progress_date, p.status,
       {adm.normalize_ts_sql("p.report_time")} AS report_time,
       {adm.normalize_ts_sql("p.review_time")} AS review_time
FROM task_progress p
JOIN task t ON t.id = p.task_id
WHERE {where_sql}
ORDER BY p.version_no DESC
LIMIT %s
"""
    return sql, (*params, int(limit))


def category_path(board_code: str) -> tuple[str, tuple]:
    """Rule 4: 分类路径由 ``task_category.parent_id`` 自关联向上拼,不臆造。

    ``WITH RECURSIVE`` 从一级分类(NULL parent)递归到二级分类,路径形如
    ``三、科技创新 / 技术攻关``,与 oa-weekly 详情页展示的 categoryPath 一致。
    """
    code, hint = adm.check_board_code(board_code)
    if hint:
        raise ValueError(hint)
    sql = f"""
WITH RECURSIVE cat AS (
    SELECT c.id, c.parent_id, c.name::text AS path
    FROM task_category c
    JOIN task_board b ON b.id = c.board_id AND {adm.sql_soft_delete("b")}
    WHERE c.parent_id IS NULL
      AND {adm.sql_soft_delete("c")}
      AND b.code = %s
    UNION ALL
    SELECT c.id, c.parent_id, (cat.path || ' / ' || c.name) AS path
    FROM task_category c
    JOIN cat ON cat.id = c.parent_id
    WHERE {adm.sql_soft_delete("c")}
)
SELECT id, path FROM cat ORDER BY path
"""
    return sql, (code,)


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
    sql = f"""
SELECT t.id, t.task_name, t.overall_goal, t.annual_goals,
       y.year, y.current_year_goal, y.milestone_summary,
       m.content AS milestone,
       g.target_result, g.progress_effect,
       g.lead_owner_names, g.project_owner_names
FROM task t
LEFT JOIN task_year_goal  y ON y.task_id = t.id AND y.year = %s
LEFT JOIN task_milestone  m ON m.task_id = t.id AND {adm.sql_soft_delete("m")}
LEFT JOIN task_group_detail g ON g.task_id = t.id
WHERE t.id = %s
  AND {adm.sql_task_admission("pg", "t")}
"""
    return sql, (y, int(task_id))


def attachment_metadata(
    task_id: int,
    granted: bool,
    limit: int = 50,
) -> tuple[str, tuple]:
    """六-1: 附件只答元数据("存在附件《文件名》"),文件体 SQL 读不到。

    ``task_attachment`` 属四张可选表之一:未授权时抛 ``PermissionError``,
    调用方把该文案作为回答给出,而不是让整轮报错。

    注:该表不在 PDF 的字段清单里(四张可选表只给了用途),列名沿用演示库结构
    (``file_name`` / ``file_size`` / ``upload_time`` / ``uploader_id`` / ``is_deleted``),
    联调时按真实库核对;``storage_path`` 只用于定位对象存储,回答里不出现。
    """
    hint = adm.require_optional_table("task_attachment", granted)
    if hint:
        raise PermissionError(hint)
    sql = f"""
SELECT a.id, a.file_name, a.file_size,
       {adm.normalize_ts_sql("a.upload_time")} AS upload_time,
       a.uploader_id
FROM task_attachment a
JOIN task t ON t.id = a.task_id
WHERE a.task_id = %s
  AND {adm.sql_task_admission("pg", "t")}
  AND {adm.sql_soft_delete("a")}
ORDER BY a.upload_time DESC
LIMIT %s
"""
    return sql, (int(task_id), int(limit))
