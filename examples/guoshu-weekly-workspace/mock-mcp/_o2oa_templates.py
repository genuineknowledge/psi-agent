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
           p.latest_progress, p.next_work,
           {adm.normalize_date_sql("p.progress_date")} AS progress_date, p.reporter_id,
           {adm.normalize_ts_sql("p.report_time")} AS report_time,
           p.version_no
    FROM task_progress p
    JOIN task t     ON t.id = p.task_id
    JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
    WHERE {adm.sql_task_admission("pg", "t")}
      AND b.code = %s
      AND {adm.sql_published_progress("pg", "p")}
    -- 定序键两级:期号大者为新,同期再按 id 兜底(只按一个键会取错行)
    ORDER BY t.id, p.version_no DESC, p.id DESC
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
        conditions.append((f"{adm.parse_ts_sql('p.progress_date')} >= %s", date_from))
    if date_to:
        conditions.append((f"{adm.parse_ts_sql('p.progress_date')} <= %s", date_to))
    where_sql = "\n  AND ".join(clause for clause, _ in conditions)
    params = tuple(value for _, value in conditions if value is not None)
    sql = f"""
SELECT t.task_name, p.version_no, p.latest_progress, p.next_work,
       {adm.normalize_date_sql("p.progress_date")} AS progress_date, p.status,
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


# ---- batch 2: 任务检索 / 进展窗口 / 覆盖率 / 年度目标 / 里程碑 / 新鲜度 -------


def task_search(
    board_code: str | None,
    keyword: str | None = None,
    category_name: str | None = None,
    person: str | None = None,
    status: int | None = None,
    project_group: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """已发布任务检索:按任务名关键词、分类名、责任人(负责人或牵头领导)过滤。

    ``person`` 同时匹配 ``project_owner_name`` / ``lead_owner_name``(多值以"、"连接,
    因此用 ILIKE 子串)或精确命中 ``owner_user_id``;三者都为 "谁负责什么" 这类问法服务,
    且一律锚在已发布主集上(rule 1)。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = f"JOIN task_board    b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
    if code:
        where.append("b.code = %s")
        params.append(code)
    elif board_code is None:
        # 未指定看板:仍要连看板表以排除软删看板,但不加 code 过滤
        where.append("TRUE")
    if status is not None:
        if int(status) not in (0, 1, 2, 3):
            raise ValueError("status 只能是 0/1/2/3")
        where.append("t.status = %s")
        params.append(int(status))
    if project_group:
        where.append("trim(t.project_group) = %s")
        params.append(project_group.strip())
    if keyword:
        where.append("t.task_name ILIKE %s")
        params.append(f"%{keyword}%")
    if category_name:
        where.append("c.name = %s")
        params.append(category_name)
    if person:
        where.append("(t.project_owner_name ILIKE %s OR t.lead_owner_name ILIKE %s OR t.owner_user_id = %s)")
        params.extend([f"%{person}%", f"%{person}%", person])
    sql = f"""
SELECT t.id, t.task_no, t.task_name,
       c.name            AS category,
       t.project_owner_name, t.lead_owner_name, t.project_group,
       {adm.normalize_ts_sql("t.latest_progress_time")} AS latest_progress_time,
       {adm.normalize_ts_sql("t.published_at")}         AS published_at
FROM task t
{board_join}
JOIN task_category c ON c.id = t.category_id AND {adm.sql_soft_delete("c")}
WHERE {"\n  AND ".join(where)}
ORDER BY t.sort_order, t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def progress_range(
    board_code: str,
    date_from: str,
    date_to: str,
    limit: int = 200,
) -> tuple[str, tuple]:
    """某时间窗内的正式进展(rule 2:只取展示版本),按进展日期倒序。

    ``progress_date`` 是 date 类型,窗口参数直接比较;这里的价值在于**窗口过滤发生在
    正式版本上**,而不是拿到历史/草稿版本再筛 —— 后者会把没进公示的进展答给用户。
    """
    code, hint = adm.check_board_code(board_code)
    if hint:
        raise ValueError(hint)
    sql = f"""
SELECT t.id AS task_id, t.task_name, p.version_no,
       p.latest_progress, p.next_work,
       {adm.normalize_date_sql("p.progress_date")} AS progress_date, p.reporter_id,
       {adm.normalize_ts_sql("p.report_time")} AS report_time
FROM task_progress p
JOIN task t     ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  AND b.code = %s
  AND {adm.sql_published_progress("pg", "p")}
  AND {adm.parse_ts_sql("p.progress_date")} >= %s
  AND {adm.parse_ts_sql("p.progress_date")} <= %s
ORDER BY p.progress_date DESC, t.sort_order
LIMIT %s
"""
    return sql, (code, date_from, date_to, int(limit))


COVERAGE_SCOPES = (
    "summary",
    "publish_split",
    "import_split",
    "unpublished",
    "unpublished_by_task",
    "pending_review",
    "never_reported",
    "version_gaps",
)

# 相对时间窗的基准日必须由调用方显式给出,绝不使用 now()。
# 演示包的数据停在 2026-08-01、快照日是 2026-08-15;用真实时钟算"最近 30 天"会把窗口
# 滑出数据、给出偏小的数(mock 把这个陷阱记作 now_instead_of_as_of,396 题里有专项)。
AS_OF_TRAP_NOTE = "相对时间窗以数据快照日为准,不用系统当前时间"

# 「最新一期已发布进展」的定序:期号大者为新,同期再按 id 兜底。
# 原工具强调两级都要 —— 只按 version_no 或只按 id 都会取错行(补报的老期号可能日期更晚,
# 所以也不能按 progress_date 取最新)。
LATEST_ROUND_CTE = """(
    SELECT p.*, row_number() OVER (PARTITION BY p.task_id ORDER BY p.version_no DESC, p.id DESC) AS rn
    FROM task_progress p
    WHERE p.is_published = 1
)"""


def _check_as_of(as_of: str | None) -> None:
    """``as_of`` 必须是显式日期:绝不拿系统时间当基准。"""
    if not as_of:
        raise ValueError(f"{AS_OF_TRAP_NOTE};请显式传入 as_of(演示包用 2026-08-15)")


def coverage_stats(
    scope: str,
    board_code: str | None = None,
    project_group: str | None = None,
    in_flight_only: bool = False,
    limit: int = 200,
) -> tuple[str, tuple]:
    """进展覆盖率与分发口径,对齐 mock 的 ``weekly_progress_coverage`` 各 scope。

    口径要点(每条都在 mock 的 docstring / 实现注释里被强调,直接决定数字对不对):

    * ``publish_split`` 数的是 **task_progress 行**而不是任务:带正式任务门是
      943 / 123 / 1066,不带门是 945 / 1068 —— 手工相加时最容易丢掉任务门;
    * ``import_split`` 只用 ``import_id IS NULL`` 区分手工录入与批量导入(943 / 943 / 0);
    * ``unpublished`` 按进展**自己的** status 码值分档(0 草稿 / 1 待审核 / 2 驳回 /
      3 通过),与任务的 ``workflow_status`` 是两套词表,因此同时给行数与去重任务数
      (演示数据里待审核 58 行 / 47 任务,驳回 39 行 / 33 任务);
    * ``never_reported`` 必须用 ``NOT EXISTS`` 判"有没有已发布进展行",不能用
      ``latest_progress_time IS NULL`` —— 后者只能找出 55 条里的 9 条;
    * ``unpublished_by_task`` 只加 ``t.is_deleted = 0``(**不加** workflow_status 门):
      "提交单已发布、进展还挂着未发布"是它的筛选条件,不是任务的发布闸门;
      期数按 ``version_no`` 去重,数的是"几期"不是"几行";
    * ``pending_review`` 两个条件各判一次(``is_published = 0`` 且 ``status = 1``),
      并把对外可见的 ``public_version`` 一并带出 —— "对外还是上一期"这半句要靠它;
    * ``version_gaps`` 的缺号 = ``max(version_no) - count(*)``,判据是聚合结果故用 HAVING。
    """
    if scope not in COVERAGE_SCOPES:
        raise ValueError(f"未知 scope:{scope};可选 {', '.join(COVERAGE_SCOPES)}")
    board = None
    if board_code:
        board, hint = adm.check_board_code(board_code)
        if hint:
            raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if project_group:
        where.append("trim(t.project_group) = %s")
        params.append(project_group.strip())
    where_sql = "\n  AND ".join(where)

    if scope == "publish_split":
        sql = f"""
SELECT count(*) FILTER (WHERE p.is_published = 1) AS published,
       count(*) FILTER (WHERE p.is_published = 0) AS unpublished,
       count(*)                                   AS total
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
"""
        return sql, tuple(params)

    if scope == "import_split":
        sql = f"""
SELECT count(*) FILTER (WHERE p.import_id IS NOT NULL) AS imported,
       count(*) FILTER (WHERE p.import_id IS NULL)     AS manual,
       count(*)                                        AS total
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
  AND p.is_published = 1
"""
        return sql, tuple(params)

    if scope == "unpublished":
        sql = f"""
SELECT p.status,
       count(*)                  AS progress_rows,
       count(DISTINCT p.task_id) AS tasks
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
  AND p.is_published = 0
GROUP BY p.status
ORDER BY p.status
"""
        return sql, tuple(params)

    if scope == "summary":
        date_from = adm.normalize_date_sql("min(p.progress_date)")
        date_to = adm.normalize_date_sql("max(p.progress_date)")
        sql = f"""
SELECT count(*)                                        AS progress_rows,
       count(DISTINCT p.task_id)                       AS tasks_covered,
       round(count(*)::numeric / NULLIF(count(DISTINCT p.task_id), 0), 2) AS avg_rounds_per_task,
       {date_from}                                     AS earliest_progress,
       {date_to}                                       AS latest_progress,
       max(p.version_no)                               AS max_version_no
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
  AND p.is_published = 1
"""
        return sql, tuple(params)

    if scope == "unpublished_by_task":
        # 注意:这里只有 t.is_deleted = 0,没有 workflow_status 门
        group_join = ""
        group_where = ["t.is_deleted = 0"]
        group_params: list[object] = []
        if board:
            group_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
            group_where.append("b.code = %s")
            group_params.append(board)
        if project_group:
            group_where.append("trim(t.project_group) = %s")
            group_params.append(project_group.strip())
        sql = f"""
SELECT t.id AS task_id, t.task_name,
       count(DISTINCT p.version_no) AS unpublished_rounds
FROM task t
{group_join}
JOIN task_workflow_submission s ON s.task_id = t.id AND s.status = 'published'
JOIN task_progress p ON p.task_id = t.id AND p.is_published = 0
WHERE {"\n  AND ".join(group_where)}
GROUP BY t.id, t.task_name
ORDER BY unpublished_rounds DESC, t.id
LIMIT %s
"""
        return sql, (*group_params, int(limit))

    if scope == "pending_review":
        report_time = adm.normalize_ts_sql("p.report_time")
        sql = f"""
SELECT t.id AS task_id, t.task_name,
       p.version_no                AS pending_version,
       {report_time}               AS report_time,
       (SELECT max(q.version_no) FROM task_progress q
         WHERE q.task_id = t.id AND q.is_published = 1) AS public_version
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
  AND p.is_published = 0
  AND p.status = 1
ORDER BY p.report_time DESC, t.id
LIMIT %s
"""
        return sql, (*params, int(limit))

    if scope == "version_gaps":
        sql = f"""
SELECT t.id AS task_id, t.task_name,
       count(*)                        AS rounds,
       max(p.version_no)               AS max_version,
       max(p.version_no) - count(*)    AS missing_count
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
  AND p.is_published = 1
GROUP BY t.id, t.task_name
HAVING max(p.version_no) - count(*) <> 0
ORDER BY missing_count DESC, t.id
LIMIT %s
"""
        return sql, (*params, int(limit))

    # never_reported:存在性判定,不是 NULL 判定
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    sql = f"""
SELECT t.id, t.task_no, t.task_name, t.status, t.project_owner_name
FROM task t
{board_join}
WHERE {where_sql}
  AND NOT EXISTS (
      SELECT 1 FROM task_progress p
      WHERE p.task_id = t.id AND p.is_published = 1
  )
ORDER BY t.sort_order, t.id
LIMIT %s
"""
    return sql, (*params, int(limit))


def formal_coverage(group_history_granted: bool) -> tuple[str, tuple]:
    """正式周报覆盖率:两张正式进展表的**并集**(技术组 + 集团组)。

    原工具记的数是:正式任务 128、有正式进展 119、覆盖率 93.0%;只看
    ``task_progress`` 会得到 73(summary 的 tasks_covered 只含技术组),
    集团组那 46 条成效写在 ``task_group_progress_history`` 里。

    ``task_group_progress_history`` 属四张可选表之一:未授权时退化为"仅技术组"口径,
    SQL 里显式去掉那半支,并在注释里说明差别 —— 宁可少答一半也不能把并集算错。
    """
    gate = adm.sql_task_admission("pg", "t")
    group_branch = (
        "OR EXISTS (SELECT 1 FROM task_group_progress_history h WHERE h.task_id = t.id AND h.is_published = 1)"
        if group_history_granted
        else ""
    )
    sql = f"""
SELECT (SELECT count(*) FROM task t WHERE {gate})      AS formal_task_count,
       (SELECT count(*) FROM task t
         WHERE {gate}
           AND (EXISTS (SELECT 1 FROM task_progress p
                         WHERE p.task_id = t.id AND p.is_published = 1)
                {group_branch}))                        AS tasks_with_progress,
       (SELECT round(count(*)::numeric * 100 / NULLIF((SELECT count(*) FROM task t2
                WHERE t2.is_deleted = 0 AND t2.workflow_status = 'published'), 0), 1)
          FROM task t
         WHERE {gate}
           AND (EXISTS (SELECT 1 FROM task_progress p
                         WHERE p.task_id = t.id AND p.is_published = 1)
                {group_branch}))                        AS coverage_pct
"""
    return sql, ()


def latest_round(
    board_code: str | None = None,
    project_group: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """每任务的**最新一期**已发布进展(含正文与下一步),一任务一行。

    用于"下一步打算做什么"这类问法:绝不能返回全部历史 —— 一个任务有 19 期,
    全history 会给 19 行,而且最老的那条"计划"会被读成现在的计划。
    同时只列 ``next_work`` 非空的任务(空的那批由 ``missing_next`` 单独计数)。
    """
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t"), "p.next_work IS NOT NULL", "p.next_work <> ''"]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if project_group:
        where.append("trim(t.project_group) = %s")
        params.append(project_group.strip())
    sql = f"""
SELECT t.id AS task_id, t.task_name, t.project_group,
       p.version_no, p.latest_progress, p.next_work,
       {adm.normalize_date_sql("p.progress_date")} AS progress_date
FROM task t
{board_join}
JOIN {LATEST_ROUND_CTE} p ON p.task_id = t.id AND p.rn = 1
WHERE {"\n  AND ".join(where)}
ORDER BY t.id
LIMIT %s
"""
    return sql, (*params, int(limit))


def missing_next(
    board_code: str | None = None,
    project_group: str | None = None,
) -> tuple[str, tuple]:
    """最新一期把"下一步"留空的任务数(只看最新一期;中间某期空着不算)。"""
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t"), "(p.next_work IS NULL OR p.next_work = '')"]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if project_group:
        where.append("trim(t.project_group) = %s")
        params.append(project_group.strip())
    sql = f"""
SELECT count(*) AS tasks_missing_next
FROM task t
{board_join}
JOIN {LATEST_ROUND_CTE} p ON p.task_id = t.id AND p.rn = 1
WHERE {"\n  AND ".join(where)}
"""
    return sql, tuple(params)


def year_goal_list(
    board_code: str,
    year: int | str,
    limit: int = 200,
) -> tuple[str, tuple]:
    """年度目标清单(rule 5:必须显式给 year)。

    用 LEFT JOIN 保留"已发布但当年目标未填写"的任务 —— 这类要如实答"未填写",
    不能因为 INNER JOIN 把它们从清单里悄悄抹掉。
    """
    y, hint = adm.check_year(year)
    if hint:
        raise ValueError(hint)
    code, hint = adm.check_board_code(board_code)
    if hint:
        raise ValueError(hint)
    sql = f"""
SELECT t.id, t.task_no, t.task_name, c.name AS category,
       y.year, y.current_year_goal, y.milestone_summary,
       (y.task_id IS NOT NULL) AS goal_filled
FROM task t
JOIN task_board    b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
JOIN task_category c ON c.id = t.category_id AND {adm.sql_soft_delete("c")}
LEFT JOIN task_year_goal y ON y.task_id = t.id AND y.year = %s
WHERE {adm.sql_task_admission("pg", "t")}
  AND b.code = %s
ORDER BY t.sort_order, t.id
LIMIT %s
"""
    return sql, (y, code, int(limit))


def milestone_list(
    board_code: str | None,
    year: int | str | None = None,
    status: int | None = None,
    task_id: int | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """里程碑 / 标志性成果清单(rule:关联任务已发布且成果项未删)。

    ``year`` 与 ``status``(0 未完成 / 1 已完成)可选;不传年份时返回该看板全部年度,
    因此"某年成果"类问法必须由 caller 传 year。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t"), adm.sql_soft_delete("m")]
    params: list[object] = []
    if code:
        where.append("b.code = %s")
        params.append(code)
    if task_id is not None:
        where.append("t.id = %s")
        params.append(int(task_id))
    if year not in (None, ""):
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
        where.append("m.year = %s")
        params.append(y)
    if status is not None:
        if int(status) not in (0, 1):
            raise ValueError("milestone.status 只能是 0(未完成)或 1(已完成)")
        where.append("m.status = %s")
        params.append(int(status))
    sql = f"""
SELECT m.id, t.id AS task_id, t.task_name, m.year, m.category, m.group_name,
       m.content, m.status, m.reporter_id, m.owner_id, m.sort_order
FROM task_milestone m
JOIN task t     ON t.id = m.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {"\n  AND ".join(where)}
ORDER BY m.year, m.sort_order, m.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def freshness_distribution(
    as_of: str,
    board_code: str | None = None,
    in_flight_only: bool = False,
) -> tuple[str, tuple]:
    """进展新鲜度分档(30 天内 / 31-90 天 / 91-180 天 / 从未报进展 / 超过 180 天)。

    ``as_of`` **必须显式给**:分档以数据快照日为基准,不用 ``now()``
    (见 ``AS_OF_TRAP_NOTE``)。桶标签带序号前缀并按标签排序,与原工具一致。

    ``in_flight_only`` 只算在办任务(``status`` 0 与 1 都算在办)。原工具的 docstring
    记下了这条差异:「从未报进展」在办是 8 条、全量是 9 条,差的那条是任务 88(已完成)
    —— 两个数回答的是不同问题,不能混用。
    """
    _check_as_of(as_of)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    ts = adm.parse_ts_sql("t.latest_progress_time")
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    sql = f"""
SELECT CASE
           WHEN t.latest_progress_time IS NULL         THEN '4 从未报进展'
           WHEN {ts} >= %s::date - interval '30 days'  THEN '1 30 天内'
           WHEN {ts} >= %s::date - interval '90 days'  THEN '2 31-90 天'
           WHEN {ts} >= %s::date - interval '180 days' THEN '3 91-180 天'
           ELSE '5 超过 180 天'
       END      AS freshness_bucket,
       count(*) AS task_count
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
GROUP BY freshness_bucket
ORDER BY freshness_bucket
"""
    # as_of 在 SELECT 里出现三次;JOIN / WHERE 的过滤参数排在其后
    return sql, (as_of, as_of, as_of, *params)


def freshness_overall(
    as_of: str,
    board_code: str | None = None,
    in_flight_only: bool = False,
) -> tuple[str, tuple]:
    """新鲜度总览:最新进展时间、距快照日滞后天数、任务总数。

    与分档一起给出:回答"看板整体有多新"时不必再单独查任务总数,
    也让分档之和可以自校验(应等于 ``task_total``)。
    """
    _check_as_of(as_of)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    ts = adm.parse_ts_sql("t.latest_progress_time")
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    sql = f"""
SELECT {adm.normalize_ts_sql("MAX(t.latest_progress_time)")} AS newest_progress,
       date_part('day', %s::date - MAX({ts}))::int           AS days_behind,
       count(*)                                              AS task_total
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
"""
    return sql, (as_of, *params)


def freshness_within(
    as_of: str,
    within_days: int,
    board_code: str | None = None,
) -> tuple[str, tuple]:
    """在快照日前 ``within_days`` 天内报过进展的任务数(任意窗口,例如 7 天)。

    固定档位表达不了任意窗口(题面里就有问 7 天的),所以单独给出这一条。
    """
    _check_as_of(as_of)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    ts = adm.parse_ts_sql("t.latest_progress_time")
    where = [adm.sql_task_admission("pg", "t"), "t.latest_progress_time IS NOT NULL"]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    sql = f"""
SELECT count(*) AS reported_within
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
  AND {ts} >= %s::date - make_interval(days => %s)
"""
    return sql, (as_of, int(within_days), *params)


def stale_tasks(
    as_of: str,
    stale_days: int,
    board_code: str | None = None,
    in_flight_only: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """滞后任务清单:最新进展早于窗口的;从未报过的**也算滞后并排在最前**。

    默认只看在办任务 —— "哪些在办任务拖着没更新"才是要问的问题,已完成任务长期
    不更新属于正常。``NULLS FIRST`` 让从未报过的排最前。
    """
    _check_as_of(as_of)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    ts = adm.parse_ts_sql("t.latest_progress_time")
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    sql = f"""
SELECT t.id, t.task_no, t.task_name, t.status, t.project_owner_name,
       {adm.normalize_ts_sql("t.latest_progress_time")}     AS latest_progress_time,
       CASE WHEN t.latest_progress_time IS NULL THEN NULL
            ELSE date_part('day', %s::date - {ts})::int END AS days_behind
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
  AND (t.latest_progress_time IS NULL
       OR {ts} < %s::date - make_interval(days => %s))
ORDER BY t.latest_progress_time NULLS FIRST, t.id
LIMIT %s
"""
    return sql, (as_of, as_of, int(stale_days), *params, int(limit))


def latest_progress_drift(
    board_code: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """漂移检查:``task.latest_progress_time`` 与真实最新已发布进展不一致的任务。

    冗余列随发布同步,可能落后于进展表。不查这一项的话,错误的新鲜度答案与正确的
    答案从外观上无法区分 —— 原工具把这条检查直接写进了 docstring。
    """
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    sql = f"""
SELECT t.id, t.task_no, t.task_name,
       {adm.normalize_ts_sql("t.latest_progress_time")} AS denormalized_time,
       {adm.normalize_ts_sql("m.newest")}               AS real_newest_progress
FROM task t
{board_join}
JOIN LATERAL (
    SELECT max(p.report_time) AS newest
    FROM task_progress p
    WHERE p.task_id = t.id AND p.is_published = 1
) m ON TRUE
WHERE {"\n  AND ".join(where)}
  AND m.newest IS NOT NULL
  AND (t.latest_progress_time IS NULL
       OR {adm.parse_ts_sql("t.latest_progress_time")} <> {adm.parse_ts_sql("m.newest")})
ORDER BY t.sort_order, t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


# ---- batch 3: 提交单 / 审批域 -------------------------------------------------

SUBMISSION_SCOPES = (
    "by_kind",
    "by_status",
    "external_ids",
    "inflight_count",
    "inflight_by_board",
    "inflight_by_kind",
    "inflight_external",
    "rejected_by_board",
    "rounds_per_task",
)

# 「在途」必须按成员枚举,不能写成 status <> 'published':
# cancelled 那张单既未发布也不在途,取反会把它算进来(60 vs 59)。
SUBMISSION_INFLIGHT = ("pending_fill", "signing", "pending_audit", "pending_leader", "rejected")

# 提交单本身没有 board_id,看板在 task 上 —— 所有按看板的问法都要从任务侧下推,
# 否则 462 张单在封顶 200 行的清单里手工挑选必然残缺。
SUBMISSION_GATE = "t.is_deleted = 0"


def _submission_where(board_code: str | None) -> tuple[str, str, list[object]]:
    """提交单域的公共 WHERE:只有 t.is_deleted = 0(不带任务发布门)。"""
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [SUBMISSION_GATE]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    return "\n  AND ".join(where), board_join, params


def submission_stats(
    scope: str,
    board_code: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """提交单 / 审批域的服务端聚合,对齐 mock 的 ``weekly_submission_query`` 各 scope。

    口径要点:

    * 提交单**没有** ``board_id``,看板在 ``task`` 上:按看板提问必须从任务侧下推
      (演示数据共 462 张单,清单封顶 200 行,手工挑必然残缺);
    * 提交单域只加 ``t.is_deleted = 0``(**不加**任务发布门)—— 462 = 470 行减去
      8 个软删任务下的单;
    * 「在途」按成员枚举 ``SUBMISSION_INFLIGHT``(含 ``rejected`` 不含 ``cancelled``),
      写成 ``status <> 'published'`` 会多算 cancelled 那张(60 vs 59);
    * ``rejected_by_board`` 的分子分母**都在提交单上**(技术组 9/293 = 3.07% >
      集团组 4/169 = 2.37%);动作流水表的驳回数是"动作次数"不是"单数",不能混用;
    * ``external_ids`` 的三个 O2OA 标识列只有这一档会输出(清单行不带)。
    """
    if scope not in SUBMISSION_SCOPES:
        raise ValueError(f"未知 scope:{scope};可选 {', '.join(SUBMISSION_SCOPES)}")
    where_sql, board_join, params = _submission_where(board_code)
    inflight_list = ", ".join(f"'{s}'" for s in SUBMISSION_INFLIGHT)

    if scope == "by_kind":
        sql = f"""
SELECT s.submission_kind, count(*) AS forms
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
GROUP BY s.submission_kind
ORDER BY forms DESC, s.submission_kind
"""
        return sql, tuple(params)

    if scope == "by_status":
        sql = f"""
SELECT s.status, count(*) AS forms, count(DISTINCT s.task_id) AS tasks
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
GROUP BY s.status
ORDER BY forms DESC, s.status
"""
        return sql, tuple(params)

    if scope == "external_ids":
        sql = f"""
SELECT count(*)                                                  AS total,
       count(*) FILTER (WHERE s.o2_process_id IS NOT NULL)        AS has_process_id,
       count(*) FILTER (WHERE s.o2_work_id    IS NOT NULL)        AS has_work_id,
       count(*) FILTER (WHERE s.o2_task_id    IS NOT NULL)        AS has_task_id,
       count(*) FILTER (WHERE s.o2_task_id    IS NULL)            AS missing_task_id,
       round(count(*) FILTER (WHERE s.o2_task_id IS NULL) * 100.0
             / NULLIF(count(*), 0), 1)                            AS missing_task_id_pct
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
"""
        return sql, tuple(params)

    if scope == "inflight_count":
        sql = f"""
SELECT count(*)                    AS inflight_forms,
       count(DISTINCT s.task_id)   AS tasks
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
  AND s.status IN ({inflight_list})
"""
        return sql, tuple(params)

    if scope == "inflight_by_board":
        sql = f"""
SELECT b.code AS board_code, s.status, count(*) AS forms
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {SUBMISSION_GATE}
  AND s.status IN ({inflight_list})
GROUP BY b.code, s.status
ORDER BY b.code, forms DESC, s.status
"""
        return sql, ()

    if scope == "inflight_by_kind":
        sql = f"""
SELECT s.status, s.submission_kind, count(*) AS forms
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
  AND s.status IN ({inflight_list})
GROUP BY s.status, s.submission_kind
ORDER BY s.status, s.submission_kind
"""
        return sql, tuple(params)

    if scope == "inflight_external":
        sql = f"""
SELECT count(*)                  AS inflight_with_process_id,
       count(DISTINCT s.task_id) AS tasks
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
  AND s.o2_process_id IS NOT NULL
  AND s.status IN ({inflight_list})
"""
        return sql, tuple(params)

    if scope == "rejected_by_board":
        sql = f"""
SELECT b.code                                                   AS board_code,
       b.name                                                   AS board_name,
       count(*)                                                 AS submissions,
       count(*) FILTER (WHERE s.status = 'rejected')             AS rejected,
       round(count(*) FILTER (WHERE s.status = 'rejected') * 100.0
             / NULLIF(count(*), 0), 2)                           AS rejected_pct
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {SUBMISSION_GATE}
GROUP BY b.code, b.name
ORDER BY rejected_pct DESC, b.code
"""
        return sql, ()

    # rounds_per_task:分子分母都给,避免模型自己拿别的分母去除
    sql = f"""
SELECT count(DISTINCT s.task_id) AS tasks,
       count(*)                  AS forms,
       round(count(*)::numeric / NULLIF(count(DISTINCT s.task_id), 0), 2) AS rounds_per_task
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
"""
    return sql, tuple(params)


# ---- batch 4: 文本规则域(服务端固化正则,模型照抄结果)------------------------

TEXT_RULES = ("number_conflict", "availability", "keyword")

# 规则用的正则与 mock 完全一致(PG 的 ARE 支持 \d / \s / (?:...),可原样移植)。
DRAFT_RE = r"草案(\d+)\s*项"
REPORT_RE_A = r"(\d+)\s*项已?报批"
REPORT_RE_B = r"已?报批(\d+)\s*项"
CONSULT_RE_A = r"(\d+)\s*项进入征求意见"
CONSULT_RE_B = r"征求意见(\d+)\s*项"
AVAILABILITY_RE = r"可用性(\d+(?:\.\d+)?)%"
COORDINATION_RE = r"协调|协同|联动|牵头组织"

AVAILABILITY_FLOOR = 90
SUM_ANOMALY_LIMIT = 100


def text_check(
    rule: str,
    board_code: str | None = None,
    task_id: int | None = None,
    task_name: str | None = None,
    keyword: str | None = None,
    all_versions: bool = False,
    group_history_granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """文本规则检查(number_conflict / availability / keyword),规则固化在服务端。

    为什么放服务端:这些题让模型自己从正文里数,要么跑满 max_rounds,要么方向不符;
    正则与判定固化后模型只需调一次并照抄结果。

    **进展正文在两个地方**:技术看板在 ``task_progress.latest_progress``,
    集团看板在 ``task_group_progress_history.progress_effect``。只扫前者会漏掉集团任务
    的历史版本 —— 演示数据里"任务 103 的 V8 冲突"就在集团历史表里,因此这里 UNION 两张表。

    默认每任务取**最新一期**已发布进展(期号倒序、同期按 id 兜底);
    ``all_versions=True`` 扫全部已发布轮次(问"历史上哪一版出过冲突"时用)。
    ``task_group_progress_history`` 属四张可选表之一:未授权时退化为只看技术组,
    调用方应把这一限制写进回答。
    """
    if rule not in TEXT_RULES:
        raise ValueError(f"不支持的规则:{rule};支持 {', '.join(TEXT_RULES)}")
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)

    def side(alias: str, table: str, text_expr: str, next_expr: str, latest_clause: str) -> tuple[str, list[object]]:
        """构造一支数据源(技术组或集团组),各自带自己的过滤参数。"""
        where = [adm.sql_task_admission("pg", "t")]
        params: list[object] = []
        join = ""
        if board:
            join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
            where.append("b.code = %s")
            params.append(board)
        if task_id is not None:
            where.append("t.id = %s")
            params.append(int(task_id))
        if task_name:
            where.append("t.task_name = %s")
            params.append(task_name)
        sql = f"""SELECT t.id AS task_id, t.task_name, {alias}.version_no AS version_no,
       {text_expr} AS progress_text, {next_expr} AS next_work, '{table}' AS source
FROM {table} {alias}
JOIN task t ON t.id = {alias}.task_id
{join}
WHERE {"\n  AND ".join(where)}
  AND {latest_clause}"""
        return sql, params

    tech_latest = (
        "p.is_published = 1 AND p.id = (SELECT q.id FROM task_progress q"
        " WHERE q.task_id = p.task_id AND q.is_published = 1"
        " ORDER BY q.version_no DESC, q.id DESC LIMIT 1)"
        if not all_versions
        else "p.is_published = 1"
    )
    hist_latest = (
        "h.is_published = 1 AND h.version_no = (SELECT max(h2.version_no)"
        " FROM task_group_progress_history h2"
        " WHERE h2.task_id = h.task_id AND h2.is_published = 1)"
        if not all_versions
        else "h.is_published = 1"
    )
    tech_sql, tech_params = side("p", "task_progress", "p.latest_progress", "p.next_work", tech_latest)
    hist_sql, hist_params = side("h", "task_group_progress_history", "h.progress_effect", "''", hist_latest)

    sources = [tech_sql] + ([hist_sql] if group_history_granted else [])
    params_out: list[object] = list(tech_params)
    if group_history_granted:
        params_out.extend(hist_params)
    rows_cte = " UNION ALL ".join(sources)

    if rule == "availability":
        # 正则里的字面 % 必须写成 %%:psycopg 会对整条 SQL 文本做占位符解析,
        # 裸 % 会被当成参数标记(报 "only '%s', '%b', '%t' are allowed as placeholders")。
        pattern = AVAILABILITY_RE.replace("%", "%%")
        expr = f"(regexp_match(progress_text, '{pattern}'))[1]"
        sql = f"""
WITH rows AS (
{rows_cte}
)
SELECT task_id, task_name, version_no, source,
       {expr} AS availability_pct
FROM rows
WHERE progress_text ~ '{pattern}'
  AND ({expr})::numeric < {AVAILABILITY_FLOOR}
ORDER BY task_id, version_no
LIMIT %s
"""
        return sql, (*params_out, int(limit))

    if rule == "keyword":
        pattern = keyword.strip() if keyword and keyword.strip() else COORDINATION_RE
        sql = f"""
WITH rows AS (
{rows_cte}
)
SELECT task_id, task_name, version_no, source, next_work
FROM rows
WHERE next_work ~ %s
ORDER BY task_id, version_no
LIMIT %s
"""
        return sql, (*params_out, pattern, int(limit))

    text_expr = "progress_text || ' ' || coalesce(next_work, '')"
    draft = f"(regexp_match({text_expr}, '{DRAFT_RE}'))[1]::int"
    report = (
        f"coalesce((regexp_match({text_expr}, '{REPORT_RE_A}'))[1],"
        f" (regexp_match({text_expr}, '{REPORT_RE_B}'))[1])::int"
    )
    consult = (
        f"coalesce((regexp_match({text_expr}, '{CONSULT_RE_A}'))[1],"
        f" (regexp_match({text_expr}, '{CONSULT_RE_B}'))[1])::int"
    )
    sql = f"""
WITH rows AS (
{rows_cte}
), extracted AS (
    SELECT task_id, task_name, version_no, source, progress_text,
           {draft}   AS draft_cnt,
           {report}  AS report_cnt,
           {consult} AS consult_cnt
    FROM rows
)
SELECT task_id, task_name, version_no, source,
       draft_cnt, report_cnt, consult_cnt,
       concat_ws(',',
           CASE WHEN report_cnt > draft_cnt  THEN 'hard_report_gt_draft'  END,
           CASE WHEN consult_cnt > draft_cnt THEN 'hard_consult_gt_draft' END,
           CASE WHEN report_cnt IS NOT NULL AND consult_cnt IS NOT NULL
                     AND draft_cnt + report_cnt + consult_cnt > {SUM_ANOMALY_LIMIT}
                THEN 'sum_anomaly' END) AS conflict_type,
       left(progress_text, 60) AS text_snippet
FROM extracted
WHERE draft_cnt IS NOT NULL
  AND (report_cnt > draft_cnt
       OR consult_cnt > draft_cnt
       OR (report_cnt IS NOT NULL AND consult_cnt IS NOT NULL
           AND draft_cnt + report_cnt + consult_cnt > {SUM_ANOMALY_LIMIT}))
ORDER BY task_id, version_no
LIMIT %s
"""
    return sql, (*params_out, int(limit))


# ---- batch 5: 年度目标行 / 里程碑(带任务过滤)/ 附件清单 ----------------------


def year_goal_rows(
    board_code: str | None = None,
    year: int | str | None = None,
    task_id: int | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """年度目标**行**清单(task_year_goal 一行一条),关联已发布任务。

    与 ``year_goal_list`` 的区别:那条是"每个已发布任务一行、未填写也保留";这条是
    "目标行本身有多少条",用来回答"某看板各任务的年度目标清单/共多少条"。
    演示数据里全看板 313 行 / 128 任务、集团板 109 行 / 46 任务、技术组 204 行 / 82 任务。

    ``year`` 可选(不传即所有年度);``task_id`` 可选。年份分布:2025 有 128 行、
    2026 有 117 行、2027 有 68 行 —— 因此"某年目标"类问法必须把 year 传下来。
    """
    y: int | None = None
    if year not in (None, ""):
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if y is not None:
        where.append("g.year = %s")
        params.append(y)
    if task_id is not None:
        where.append("t.id = %s")
        params.append(int(task_id))
    sql = f"""
SELECT t.id AS task_id, t.task_no, t.task_name, g.year,
       g.current_year_goal, g.milestone_summary,
       (g.current_year_goal IS NOT NULL AND g.current_year_goal <> '') OR
       (g.milestone_summary IS NOT NULL AND g.milestone_summary <> '') AS goal_filled
FROM task_year_goal g
JOIN task t ON t.id = g.task_id
{board_join}
WHERE {"\n  AND ".join(where)}
ORDER BY t.sort_order, t.id, g.year
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def attachment_list(
    board_code: str | None = None,
    task_id: int | None = None,
    granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """附件清单(**只出元数据**):文件名 / 字节数 / 上传人 / 上传时间。

    六-1:附件内容读不到,问答只能说"存在附件《文件名》",因此 ``storage_path`` 绝不出现
    在返回列里。演示数据里全看板 454 条 / 106 个任务、集团板 52 条 / 28 任务。

    看板在 ``task`` 上:不指定看板时"集团板有哪些附件"只能逐任务翻 46 次,所以按看板
    下推是必需的。
    """
    hint = adm.require_optional_table("task_attachment", granted)
    if hint:
        raise PermissionError(hint)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t"), adm.sql_soft_delete("a")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if task_id is not None:
        where.append("t.id = %s")
        params.append(int(task_id))
    sql = f"""
SELECT a.id, t.id AS task_id, t.task_no, t.task_name,
       a.file_name, a.file_size,
       {adm.normalize_ts_sql("a.upload_time")} AS upload_time,
       a.uploader_id
FROM task_attachment a
JOIN task t ON t.id = a.task_id
{board_join}
WHERE {"\n  AND ".join(where)}
ORDER BY t.sort_order, t.id, a.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def attachment_stats(board_code: str | None = None, granted: bool = True) -> tuple[str, tuple]:
    """附件汇总:条数 / 涉及任务数 / 总字节 / 平均 KB / 各扩展名条数。

    ``file_size`` 是字节,原样报出(不要换算成 KB/MB,也不要写"约")——口径如此规定。
    """
    hint = adm.require_optional_table("task_attachment", granted)
    if hint:
        raise PermissionError(hint)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t"), adm.sql_soft_delete("a")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    ext = "lower(substring(a.file_name from '\\.([^.]+)$'))"
    sql = f"""
WITH files AS (
    SELECT a.*, {ext} AS ext
    FROM task_attachment a
    JOIN task t ON t.id = a.task_id
    {board_join}
    WHERE {"\n  AND ".join(where)}
)
SELECT (SELECT count(*) FROM files)                                  AS attachment_count,
       (SELECT count(DISTINCT task_id) FROM files)                   AS tasks_with_attachment,
       (SELECT sum(file_size) FROM files)                            AS total_bytes,
       (SELECT count(*) FROM files WHERE ext = 'pptx')               AS ext_pptx,
       (SELECT count(*) FROM files WHERE ext = 'xlsx')               AS ext_xlsx,
       (SELECT count(*) FROM files WHERE ext = 'pdf')                AS ext_pdf,
       (SELECT count(*) FROM files WHERE ext = 'docx')               AS ext_docx
"""
    return sql, tuple(params)


# ---- batch 6: 责任人角色 / 集团板明细 / 行数体检 ------------------------------


def owner_roles(person: str) -> tuple[str, tuple]:
    """某人在正式任务里的角色拆分:主责 / 项目负责人 / 牵头领导 / 去重并集。

    ``weekly_task_query`` 的 owner 过滤把三列 OR 在一起,答不了"作为项目负责人几个、
    作为牵头领导几个";这条把三个角色分开计,再给一个去重的 any_role。

    匹配规则(与原工具一致):**先去空格再精确匹配**,id 与姓名都可以;姓名列是多值
    (以「、」连接),这里按整串匹配,不做子串。
    """
    token = (person or "").strip().replace(" ", "")
    if not token:
        raise ValueError("person 不能为空")
    strip = lambda col: f"replace(coalesce({col}, ''), ' ', '')"  # noqa: E731
    sql = f"""
SELECT count(*) FILTER (WHERE {strip("t.owner_user_id")} = %s)                        AS as_owner,
       count(*) FILTER (WHERE {strip("t.project_owner_id")} = %s
                           OR {strip("t.project_owner_name")} = %s)                    AS as_project_owner,
       count(*) FILTER (WHERE {strip("t.lead_owner_id")} = %s
                           OR {strip("t.lead_owner_name")} = %s)                       AS as_lead_owner,
       count(*) FILTER (WHERE {strip("t.owner_user_id")} = %s
                           OR {strip("t.project_owner_id")} = %s
                           OR {strip("t.project_owner_name")} = %s
                           OR {strip("t.lead_owner_id")} = %s
                           OR {strip("t.lead_owner_name")} = %s)                       AS any_role
FROM task t
WHERE {adm.sql_task_admission("pg", "t")}
"""
    return sql, (token,) * 10


GROUP_DETAIL_FIELDS = (
    "task_id",
    "target_result",
    "implementation_measure",
    "completion_time",
    "lead_owner_names",
    "lead_owner_ids",
    "project_owner_names",
    "project_owner_ids",
    "project_group",
    "progress_effect",
)


def group_detail_list(
    board_code: str | None = None,
    task_id: int | None = None,
    status: int | None = None,
    non_empty: tuple[str, ...] = (),
    contains: str | None = None,
    contains_field: str | None = None,
    fields: tuple[str, ...] = (),
    limit: int = 200,
) -> tuple[str, tuple]:
    """集团板专属扩展表 ``task_group_detail``(1:1)明细。

    这些列(目标成果 / 落实举措 / 完成时间 / 进度成效 / 多值负责人)只有这张表有,
    ``weekly_task_query`` 返回的是共享的 task 列,根本没有它们。

    口径要点:

    * ``completion_time`` 是**展示文本**(如「2026 年 12 月,后续持续推进」),按文本匹配,
      **绝不做日期运算**(原工具把这条记作 R-12);
    * ``status``(业务状态 0/1/2/3)在 ``task`` 上,成效描述在本表 —— "状态与成效矛盾"
      (未开始却写了成效)必须两边一起判,单看任何一张表都表达不了;
    * ``non_empty`` 给"必须非空"的列:漏掉它,未开始且成效为空的任务也会跟着进来、
      把矛盾数撑大。
    """
    if contains and not contains_field:
        raise ValueError("contains 必须与 field 一起使用")
    if contains_field and contains_field not in GROUP_DETAIL_FIELDS:
        raise ValueError(f"不支持的 field:{contains_field};可选 {', '.join(GROUP_DETAIL_FIELDS)}")
    for column in non_empty:
        if column not in GROUP_DETAIL_FIELDS:
            raise ValueError(f"不支持的 non_empty 列:{column};可选 {', '.join(GROUP_DETAIL_FIELDS)}")
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if task_id is not None:
        where.append("t.id = %s")
        params.append(int(task_id))
    if status is not None:
        if int(status) not in (0, 1, 2, 3):
            raise ValueError("status 只能是 0/1/2/3")
        where.append("t.status = %s")
        params.append(int(status))
    for column in non_empty:
        where.append(f"coalesce(g.{column}, '') <> ''")
    if contains and contains_field:
        where.append(f"g.{contains_field} LIKE %s")
        params.append(f"%{contains}%")
    # task_id / project_group 取自 task 侧(避免同名列出现两次);
    # 其余列只有 task_group_detail 有。fields 可选:给定则只返回这些列。
    detail_fields = [name for name in GROUP_DETAIL_FIELDS if name not in ("task_id", "project_group")]
    if fields:
        unknown = [name for name in fields if name not in GROUP_DETAIL_FIELDS]
        if unknown:
            raise ValueError(f"不支持的列:{', '.join(unknown)};可选 {', '.join(GROUP_DETAIL_FIELDS)}")
        rank = {name: index for index, name in enumerate(GROUP_DETAIL_FIELDS)}
        detail_fields = sorted(
            (name for name in fields if name not in ("task_id", "project_group")), key=lambda name: rank[name]
        )
    selected = ", ".join(f"g.{name}" for name in detail_fields)
    selected_sql = f",\n       {selected}" if selected else ""
    sql = f"""
SELECT t.id AS task_id, t.task_no, t.task_name, t.status, t.project_group{selected_sql}
FROM task_group_detail g
JOIN task t ON t.id = g.task_id
{board_join}
WHERE {"\n  AND ".join(where)}
ORDER BY t.sort_order, t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def table_row_counts() -> tuple[str, tuple]:
    """逐表精确行数(体检用)。

    正式源没有 ``information_schema`` 式的行数估算可靠值,所以逐表 ``count(*)``;
    四张可选表用 ``to_regclass`` 判断存在性,未授权/不存在时返回 NULL 而不是报错。
    """
    tables = (
        "task_board",
        "task_category",
        "task",
        "task_year_goal",
        "task_progress",
        "task_milestone",
        "task_workflow_submission",
        "task_group_detail",
        "task_group_progress_history",
        "task_workflow_action",
        "task_attachment",
        "task_progress_import",
    )
    parts = []
    for table in tables:
        parts.append(
            f"SELECT '{table}' AS table_name, "
            f"CASE WHEN to_regclass('public.{table}') IS NULL THEN NULL "
            f"ELSE (SELECT count(*) FROM {table}) END AS row_count"
        )
    sql = "\nUNION ALL\n".join(parts) + "\nORDER BY table_name"
    return sql, ()


# ---- batch 7: 审批动作流水(可选表)/ 提交单域列名对齐 ------------------------


WORKFLOW_SCOPES = ("by_node_action", "actions_per_task", "by_action", "recent")


def workflow_actions(
    scope: str,
    board_code: str | None = None,
    task_id: int | None = None,
    action: str | None = None,
    include_opinion: bool = False,
    granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """审批动作流水 ``task_workflow_action``(可选表)。

    演示库里该表 1,613 行,带 ``t.is_deleted = 0`` 后 **1,578 行** —— 与清单封顶 200 行
    差一个量级,所以"多少个"这类问题必须走服务端聚合,不能翻明细数。

    口径要点:

    * ``opinion``(审批意见)**按权限返回**(R-04/R-14):默认不出现在返回列里,
      ``include_opinion=True`` 才带上 —— 一刀切脱敏与一刀切放开都不满足要求;
    * 动作数 ≠ 单数:流水里 ``rejected`` 有 13 条,那是**动作**条数,
      "驳回率"的分子要用提交单自己的 ``status = 'rejected'``(技术组 9、集团组 4);
    * 带 ``t.is_deleted = 0`` 是正确闸门(1,578);再加任务发布门会掉到 1,519,
      而审批流水只关心任务是否被软删。
    """
    if scope not in WORKFLOW_SCOPES:
        raise ValueError(f"未知 scope:{scope};可选 {', '.join(WORKFLOW_SCOPES)}")
    hint = adm.require_optional_table("task_workflow_action", granted)
    if hint:
        raise PermissionError(hint)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_soft_delete("t")]
    params: list[object] = []
    joins = ""
    if board:
        joins = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if task_id is not None:
        where.append("a.task_id = %s")
        params.append(int(task_id))
    if action:
        where.append("a.action = %s")
        params.append(action)
    where_sql = "\n  AND ".join(where)
    opinion_col = ", a.opinion" if include_opinion else ""

    if scope == "by_node_action":
        sql = f"""
SELECT a.node_type, a.action, count(*) AS actions
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {where_sql}
GROUP BY a.node_type, a.action
ORDER BY a.node_type, a.action
"""
        return sql, tuple(params)

    if scope == "actions_per_task":
        sql = f"""
SELECT count(*)                  AS actions,
       count(DISTINCT a.task_id) AS tasks,
       round(count(*)::numeric / NULLIF(count(DISTINCT a.task_id), 0), 2) AS actions_per_task
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {where_sql}
"""
        return sql, tuple(params)

    if scope == "by_action":
        sql = f"""
SELECT a.action, count(*) AS actions
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {where_sql}
GROUP BY a.action
ORDER BY actions DESC, a.action
"""
        return sql, tuple(params)

    # recent / 默认:按动作自身时间倒序(默认清单按 task id 排序,答不了"最近谁被驳回")
    sql = f"""
SELECT a.id, a.task_id, t.task_no, t.task_name,
       a.submission_id, a.node_type, a.action,
       a.operator_id, a.operator_name{opinion_col},
       {adm.normalize_ts_sql("a.created_at")} AS action_time
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {where_sql}
ORDER BY a.created_at DESC, a.id DESC
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


# ---- batch 8: 规模横截面(三种 mode x 三种分组轴)----------------------------

SCALE_MODES = ("totals", "completeness", "intensity")
SCALE_AXES = ("board", "project_group", "primary_category")

# 轴 = (分组表达式, 排序键, 额外 JOIN)。排序键里出现聚合是为了 PG 的 group by 约束,
# 与演示源同一写法。
SCALE_AXIS_SQL: dict[str, tuple[str, str, str]] = {
    "board": ("b.name", "min(b.sort_order)", "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0"),
    "project_group": (
        "coalesce(nullif(trim(t.project_group), ''), '(未填)')",
        "tasks DESC, bucket",
        "",
    ),
    "primary_category": (
        "pc.name",
        "tasks DESC, bucket",
        "JOIN task_category c ON c.id = t.category_id AND c.is_deleted = 0 "
        "JOIN task_category pc ON pc.id = c.parent_id AND pc.is_deleted = 0",
    ),
}


def scale_cross_section(by: str = "board", mode: str = "totals", year: int | str = 2026) -> tuple[str, tuple]:
    """一次 JOIN 多张子表的横截面(规模 / 完整度 / 强度)。

    三种 mode 回答三个不同的问题,不能互相代答:

    * ``totals``:该组的任务数 + 里程碑数 + 附件数 + "设了该年度目标的任务数"。
      **三张子表同时 LEFT JOIN 会把行数相乘,所以每个计数都必须 ``COUNT(DISTINCT ...)``** ——
      不去重时技术组里程碑会从 294 变成 1363(fan_out_double_count);
    * ``completeness``:"有目标 / 有里程碑 / 有进展的**任务数**",用 ``SUM(EXISTS ...)``;
      它与 totals 的不是同一件事(totals 数的是子表行数);
    * ``intensity``:已发布进展**行数**与"每任务期数";分母是任务数且 ``LEFT JOIN`` 保留零期
      任务,否则"人均期数"会被抬高(inner_join_drops_zero)。

    轴:``board``(按看板排序列)/ ``project_group``(未填归入「(未填)」,按任务数倒序)/
    ``primary_category``(分类树的**一级**分类,按任务数倒序)。
    """
    axis_key = (by or "board").strip().lower()
    if axis_key not in SCALE_AXIS_SQL:
        raise ValueError(f"不支持的分组轴:{by};支持 {', '.join(SCALE_AXES)}")
    mode_key = (mode or "totals").strip().lower()
    if mode_key not in SCALE_MODES:
        raise ValueError(f"不支持的 mode:{mode};支持 {', '.join(SCALE_MODES)}")
    y, hint = adm.check_year(year)
    if hint:
        raise ValueError(hint)
    axis, order, extra = SCALE_AXIS_SQL[axis_key]
    gate = adm.sql_task_admission("pg", "t")

    if mode_key == "intensity":
        sql = f"""
SELECT {axis} AS bucket,
       count(DISTINCT t.id) AS tasks,
       count(p.id)          AS progress_rows,
       round(count(p.id)::numeric / NULLIF(count(DISTINCT t.id), 0), 2) AS rows_per_task
FROM task t
{extra}
LEFT JOIN task_progress p ON p.task_id = t.id AND {adm.sql_published_progress("pg", "p")}
WHERE {gate}
GROUP BY {axis}
ORDER BY rows_per_task DESC, bucket
"""
        return sql, ()

    if mode_key == "completeness":
        sql = f"""
SELECT {axis} AS bucket,
       count(*) AS tasks,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM task_year_goal g
                                       WHERE g.task_id = t.id AND g.year = %s))          AS has_goal,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM task_milestone m
                                       WHERE m.task_id = t.id AND {adm.sql_soft_delete("m")})) AS has_milestone,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM task_progress p
                                       WHERE p.task_id = t.id
                                         AND {adm.sql_published_progress("pg", "p")}))   AS has_progress
FROM task t
{extra}
WHERE {gate}
GROUP BY {axis}
ORDER BY {order}
"""
        return sql, (y,)

    sql = f"""
SELECT {axis} AS bucket,
       count(DISTINCT t.id)   AS tasks,
       count(DISTINCT g.task_id) AS with_year_goal,
       count(DISTINCT m.id)   AS milestones,
       count(DISTINCT a.id)   AS attachments
FROM task t
{extra}
LEFT JOIN task_year_goal g ON g.task_id = t.id AND g.year = %s
LEFT JOIN task_milestone m ON m.task_id = t.id AND {adm.sql_soft_delete("m")}
LEFT JOIN task_attachment a ON a.task_id = t.id AND {adm.sql_soft_delete("a")}
WHERE {gate}
GROUP BY {axis}
ORDER BY {order}
"""
    return sql, (y,)
