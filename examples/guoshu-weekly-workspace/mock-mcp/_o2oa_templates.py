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

import datetime as dt

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
    board_code: str | None,
    date_from: str,
    date_to: str,
    limit: int = 200,
) -> tuple[str, tuple]:
    """某时间窗内的正式进展(rule 2:只取展示版本),按进展日期倒序。

    ``progress_date`` 是 date 类型,窗口参数直接比较;这里的价值在于**窗口过滤发生在
    正式版本上**,而不是拿到历史/草稿版本再筛 —— 后者会把没进公示的进展答给用户。

    列集合照抄参考查询:``task_id / task_name / version_no / progress_date /
    report_time / lag_days``。进展正文(latest_progress / next_work)与填报人不在
    这个工具的返回里 —— 它答的是"哪些任务在窗口内报过",列正文只会把 200 行的
    回包撑成几万字,而正文有专门的逐任务出口。
    ``lag_days`` = 上报日 - 周期日(补报更早周期时为正)。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    board_clause = "AND b.code = %s" if code else ""
    params: list[object] = [code] if code else []
    sql = f"""
SELECT t.id AS task_id, t.task_name, p.version_no,
       {adm.normalize_date_sql("p.progress_date")} AS progress_date,
       {adm.normalize_ts_sql("p.report_time")} AS report_time,
       ({adm.parse_ts_sql("p.report_time")})::date - ({adm.parse_ts_sql("p.progress_date")})::date AS lag_days
FROM task_progress p
JOIN task t     ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  {board_clause}
  AND {adm.sql_published_progress("pg", "p")}
  AND {adm.parse_ts_sql("p.progress_date")} >= %s
  AND {adm.parse_ts_sql("p.progress_date")} <= %s
ORDER BY p.progress_date DESC, t.sort_order
LIMIT %s
"""
    params.extend([date_from, date_to, int(limit)])
    return sql, tuple(params)


def progress_range_totals(
    board_code: str | None,
    date_from: str,
    date_to: str,
) -> tuple[str, tuple]:
    """同一窗口的**总数**:行数与涉及任务数。

    单列出来是因为它们必须活过截断:"今年以来报了多少期"是 366 行,取到 200 行 +
    ``has_more`` 之后调用方无法还原真值,只能报"至少 200"。与参考查询同法(先查总数,
    再查明细)。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    board_clause = "AND b.code = %s" if code else ""
    params: list[object] = [code] if code else []
    sql = f"""
SELECT count(*) AS total_rows, count(DISTINCT p.task_id) AS total_tasks
FROM task_progress p
JOIN task t     ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  {board_clause}
  AND {adm.sql_published_progress("pg", "p")}
  AND {adm.parse_ts_sql("p.progress_date")} >= %s
  AND {adm.parse_ts_sql("p.progress_date")} <= %s
"""
    params.extend([date_from, date_to])
    return sql, tuple(params)


def published_progress_recency(board_code: str | None = None) -> tuple[str, tuple]:
    """全场正式进展的**最新周期日**与总行数(短窗口 0 行时用来解释这个 0)。

    短窗口取回 0 行有两个完全不同的原因:"窗口里确实没人报"与"这张表按月上报,
    短于半月的窗口必然为空"。不区分它们,调用方会把后者答成前者 —— 参考实现为此
    专门附了一段提示,这里给它同一份事实,但数字取自当前库而不是写死的。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    board_clause = "AND b.code = %s" if code else ""
    params: list[object] = [code] if code else []
    sql = f"""
SELECT {adm.normalize_date_sql("max(p.progress_date)")} AS latest_progress_date,
       count(*) AS published_rows
FROM task_progress p
JOIN task t     ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  {board_clause}
  AND {adm.sql_published_progress("pg", "p")}
"""
    return sql, tuple(params)


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
       (%s::date - MAX({ts})::date)::int                     AS days_behind,
       count(*)                                              AS task_total
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
"""
    return sql, (as_of, *params)


def freshness_lag_bands(
    as_of: str,
    group_history_granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """每看板的陈旧度分档:0-7 / 8-14 / 15-30 / 超过 30 / 无正式进展。

    固定档位(30/90/180)表达不了这套边界:技术组 17/56/9 与集团组 14/28/4 都落在
    原来的"30 天内"和"31-90 天"两档里,答出来的表是给不出边界的。

    **两个看板的正式进展存在不同表**:技术组在 ``task_progress``(取 ``progress_date``),
    集团组在 ``task_group_progress_history``(取 ``report_time``)。用
    ``task.latest_progress_time`` 一把抓会把集团组算空,也会把技术组未发布的进展算进来。
    """
    _check_as_of(as_of)
    if not group_history_granted:
        hint = adm.require_optional_table("task_group_progress_history", False)
        raise PermissionError(hint)
    pdate = adm.parse_ts_sql("p.progress_date")
    htime = adm.parse_ts_sql("h.report_time")
    sql = f"""
WITH per_task AS (
    SELECT t.id, t.board_id,
           CASE WHEN b.code = 'group'
                THEN (%s::date - max(CASE WHEN h.is_published = 1 THEN {htime} END)::date)::int
                ELSE (%s::date - max(CASE WHEN p.is_published = 1 THEN {pdate} END)::date)::int
           END AS d
    FROM task t
    JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
    LEFT JOIN task_progress p ON p.task_id = t.id
    LEFT JOIN task_group_progress_history h ON h.task_id = t.id
    WHERE {adm.sql_task_admission("pg", "t")}
    GROUP BY t.id, t.board_id, b.code
)
SELECT b.name AS board_name,
       CASE WHEN x.d IS NULL    THEN '5 无正式进展'
            WHEN x.d <= 7       THEN '1 0-7 天'
            WHEN x.d <= 14      THEN '2 8-14 天'
            WHEN x.d <= 30      THEN '3 15-30 天'
            ELSE '4 超过 30 天' END AS lag_band,
       count(*) AS task_count
FROM per_task x
JOIN task_board b ON b.id = x.board_id
GROUP BY b.id, b.name, b.sort_order, lag_band
ORDER BY b.sort_order, lag_band
LIMIT %s
"""
    return sql, (as_of, as_of, int(limit))


def freshness_within(
    as_of: str,
    within_days: int,
    board_code: str | None = None,
) -> tuple[str, tuple]:
    """在快照日前 ``within_days`` 天内报过进展的任务数(任意窗口,例如 7 天)。

    固定档位表达不了任意窗口(题面里就有问 7 天的),所以单独给出这一条。
    与参考实现同形:一行三列 —— 任务数、最新进展时间、距基准日天数;只给计数时
    调用方答不出"最新那条是哪天",而这两个数本来就是同一个问题的两半。
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
    sql = f"""
SELECT count(*) AS task_count,
       {adm.normalize_ts_sql("max(t.latest_progress_time)")} AS newest_progress,
       (%s::date - max({ts})::date)::int                     AS days_behind
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
  AND {ts} >= %s::date - make_interval(days => %s)
"""
    return sql, (as_of, as_of, int(within_days), *params)


def recent_reporters(
    as_of: str,
    recent_days: int,
    board_code: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """近 ``recent_days`` 天内报过进展的任务清单(与 ``stale_tasks`` 相反的一端)。

    **不加 status 闸门**:问的是"有没有报进展",不是"任务在不在办"。
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
    sql = f"""
SELECT t.id, t.task_name, t.status,
       {adm.normalize_ts_sql("t.latest_progress_time")} AS latest_progress_time,
       (%s::date - {ts}::date)::int                      AS days_since
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
  AND {ts} >= %s::date - make_interval(days => %s)
ORDER BY t.latest_progress_time DESC, t.id
LIMIT %s
"""
    return sql, (as_of, as_of, int(recent_days), *params, int(limit))


def stale_tasks(
    as_of: str,
    stale_days: int,
    board_code: str | None = None,
    in_flight_only: bool = True,
    reported_only: bool = False,
    limit: int = 200,
) -> tuple[str, tuple]:
    """滞后任务清单:最新进展早于窗口的;从未报过的**也算滞后并排在最前**。

    默认只看在办任务 —— "哪些在办任务拖着没更新"才是要问的问题,已完成任务长期
    不更新属于正常。排序照抄参考实现:``latest_progress_time IS NOT NULL`` 先把
    从未报过的排到最前(PG 的 ``NULLS FIRST`` 在 ASC 下语义相同,但写成布尔键后
    "从未报过"这层意思在 SQL 里是显式的),然后按时间、按 id。

    ``reported_only``:问"最久没上报的前 N 条"时,从未报过的任务没有天数可比
    (``days_since`` 为空),会把前 N 名整段占满 —— 那是另一问
    (用默认档或 ``weekly_progress_coverage scope=never_reported``)。
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
    if reported_only:
        where.append("t.latest_progress_time IS NOT NULL")
    sql = f"""
SELECT t.id, t.task_name, t.status,
       {adm.normalize_ts_sql("t.latest_progress_time")} AS latest_progress_time,
       CASE WHEN t.latest_progress_time IS NULL THEN NULL
            ELSE (%s::date - {ts}::date)::int END       AS days_since
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
  AND (t.latest_progress_time IS NULL
       OR {ts} < %s::date - make_interval(days => %s))
ORDER BY t.latest_progress_time IS NOT NULL, t.latest_progress_time, t.id
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
SELECT t.id AS task_id, t.task_name,
       {adm.normalize_ts_sql("t.latest_progress_time")} AS latest_progress_time,
       {adm.normalize_ts_sql("m.newest")}               AS actual_latest_report
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
ORDER BY t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def _stale_predicate(as_of: str, days: int) -> tuple[str, list[object]]:
    """滞后判据(一次写好,配合 CTE 只出现一次)。"""
    ts = adm.parse_ts_sql("t.latest_progress_time")
    return (
        f"(t.latest_progress_time IS NULL OR {ts} < %s::date - make_interval(days => %s))",
        [as_of, int(days)],
    )


# 滞后/活跃分组的两根轴(照抄参考实现):看板轴要 JOIN 回 task_board 才有名字 ——
# **不等调用方给看板过滤**,轴自己就需要这个 JOIN;专项组就在 task 上,空值归入
# 「(未填)」而不是整组丢掉。
STALE_AXES: dict[str, str] = {
    "board": "b.name",
    "project_group": "coalesce(nullif(btrim(t.project_group), ''), '(未填)')",
}


def _stale_axis(axis: str, board_code: str | None) -> tuple[str, str, str, list[object]]:
    """→ (分组表达式, 额外 JOIN, 看板过滤片段, 参数)。看板轴与看板过滤共用同一个 JOIN。"""
    expression = STALE_AXES.get(axis)
    if expression is None:
        raise ValueError(f"不支持的分组轴:{axis};支持 {', '.join(sorted(STALE_AXES))}")
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    needs_board_join = axis == "board" or bool(board)
    board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
    join = board_join if needs_board_join else ""
    clause = "b.code = %s" if board else ""
    return expression, join, clause, ([board] if board else [])


def stale_grouped(
    as_of: str,
    days: int,
    axis: str,
    recent_end: bool,
    board_code: str | None = None,
    in_flight_only: bool = False,
    limit: int = 200,
) -> tuple[str, tuple]:
    """按分组轴给出滞后/活跃的**条数与占比**(分母同排返回)。

    只给 stale_count 答不了"哪个组滞后占比最高":标准安全组 5 条最多,但它有 19 个
    任务、占比 26.3%,低于国家工程办的 4/15 = 26.7%。占比必须服务端算、分母必须
    与服务端同一个 —— 让调用方拿别处的任务数手工相除,一错就全错。

    排序跟着问句走:问滞后按 ``stale_pct`` 倒序,问活跃(``recent_end``)按
    ``active_pct`` 倒序 —— 排错端等于把末位当第一。

    判据只出现一次(CTE 里算好 ``is_stale``):同一条 SQL 里重复四遍谓词,参数
    个数就得跟着重复四遍,改一处忘三处是必然的。
    """
    _check_as_of(as_of)
    expression, extra_join, board_clause, params = _stale_axis(axis, board_code)
    stale, stale_params = _stale_predicate(as_of, days)
    where = [adm.sql_task_admission("pg", "t")]
    if board_clause:
        where.append(board_clause)
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    order_col = "active_pct" if recent_end else "stale_pct"
    sql = f"""
WITH per_task AS (
    SELECT {expression} AS bucket, {stale} AS is_stale
    FROM task t
    {extra_join}
    WHERE {"\n  AND ".join(where)}
)
SELECT bucket,
       count(*)                                             AS total,
       count(*) FILTER (WHERE is_stale)                     AS stale_count,
       round(count(*) FILTER (WHERE is_stale)::numeric / count(*) * 100, 1)     AS stale_pct,
       count(*) FILTER (WHERE NOT is_stale)                 AS active_count,
       round(count(*) FILTER (WHERE NOT is_stale)::numeric / count(*) * 100, 1) AS active_pct
FROM per_task
GROUP BY bucket
ORDER BY {order_col} DESC, bucket
LIMIT %s
"""
    return sql, (*stale_params, *params, int(limit))


def stale_group_totals(
    as_of: str,
    days: int,
    axis: str,
    board_code: str | None = None,
    in_flight_only: bool = False,
) -> tuple[str, tuple]:
    """分组档的合计一行:各列合计由服务端给,别让调用方自己把十来行加一遍。

    (C3-04 的表格逐行都对,结论里的合计却写错 —— 与服务端算率同一个理由。)
    """
    _check_as_of(as_of)
    expression, extra_join, board_clause, params = _stale_axis(axis, board_code)
    stale, stale_params = _stale_predicate(as_of, days)
    where = [adm.sql_task_admission("pg", "t")]
    if board_clause:
        where.append(board_clause)
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    sql = f"""
WITH per_task AS (
    SELECT {expression} AS bucket, {stale} AS is_stale
    FROM task t
    {extra_join}
    WHERE {"\n  AND ".join(where)}
)
SELECT count(*)                              AS task_total,
       count(*) FILTER (WHERE is_stale)      AS stale_total,
       count(*) FILTER (WHERE NOT is_stale)  AS active_total,
       count(DISTINCT bucket)                AS group_total
FROM per_task
"""
    return sql, (*stale_params, *params)


def stale_totals(
    as_of: str,
    days: int,
    board_code: str | None = None,
    in_flight_only: bool = True,
) -> tuple[str, tuple]:
    """滞后清单的两个自检数:符合口径的任务总数、其中从未报过的条数。

    ``reported_only`` 把从未报过的排除,让"最久没报"的天数可比;那两个数必须
    同时给,调用方才知道自己排除了多少条。
    """
    _check_as_of(as_of)
    stale, stale_params = _stale_predicate(as_of, days)
    where = [adm.sql_task_admission("pg", "t"), stale]
    board_join = ""
    if board_code:
        _code, hint = adm.check_board_code(board_code)
        if hint:
            raise ValueError(hint)
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    # 参数顺序 = 占位符顺序:滞后判据在 SELECT 里,看板码在 WHERE 里
    params: list[object] = [*stale_params, *([board_code] if board_join else [])]
    sql = f"""
SELECT count(*) AS total_count,
       count(*) FILTER (WHERE t.latest_progress_time IS NULL) AS never_reported_count
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
"""
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
       (SELECT round(sum(file_size) / 1024.0 / 1024.0, 1) FROM files) AS total_mb,
       (SELECT round(avg(file_size) / 1024.0, 1) FROM files)         AS avg_kb,
       (SELECT count(DISTINCT uploader_id) FROM files)               AS uploader_count,
       (SELECT count(*) FROM files WHERE progress_id IS NOT NULL)                                AS linked_to_progress,
       (SELECT count(*) FROM files WHERE workflow_submission_id IS NOT NULL) AS linked_to_submission,
       (SELECT count(*) FROM files
         WHERE progress_id IS NULL AND workflow_submission_id IS NULL)      AS linked_to_task,
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


# ---- batch 9: 排名(三种并列语义 + 六种子表度量)-----------------------------

RANK_MODES = ("cut", "keep_ties", "per_group")
RANK_GROUPINGS = ("project_group", "board", "primary_category", "status")
RANK_SHAPES = ("rank", "count")

# 度量 = (子表, 子表闸门, 计数表达式, 中文标签, 是否可选表)
# 全部走 LEFT JOIN,零值任务才不会被 INNER JOIN 静默丢掉(inner_join_drops_zero)。
RANK_METRICS: dict[str, tuple[str, str, str, str, str | None]] = {
    "progress_rounds": ("task_progress", "x.is_published = 1", "count(x.id)", "已发布进展期数", None),
    "milestones": ("task_milestone", "x.is_deleted = 0", "count(x.id)", "里程碑数", None),
    "milestones_done": (
        "task_milestone",
        "x.is_deleted = 0",
        "count(*) FILTER (WHERE x.status = 1)",
        "已完成里程碑数",
        None,
    ),
    "attachments": ("task_attachment", "x.is_deleted = 0", "count(x.id)", "附件数", "task_attachment"),
    "submissions": ("task_workflow_submission", "TRUE", "count(x.id)", "审批提交单数", None),
    "group_rounds": (
        "task_group_progress_history",
        "x.is_published = 1",
        "count(x.id)",
        "集团看板成效期数",
        "task_group_progress_history",
    ),
    # 唯一不 JOIN 子表的度量:值就在 task 行上。
    "project_team_size": ("", "", "_TEAM_SIZE", "项目团队人数", None),
}

# 项目团队人数 = 三个分隔符(、 , ;)出现次数 + 1。
# **三种都要数**:只数顿号会把另外两种写法算成 1 人。
# 这一列在 task 行上、覆盖两个看板 128 条;集团明细的 project_owner_names 是另一列,
# 只覆盖集团板 46 条,两个"人数"必须分开。
# 分隔符用转义序列写出:它们是**数据里真实存在的字符**(顿号/半角逗号/全角分号),
# 不能换成近似字符;直接写字面量会触发 RUF001(ambiguous unicode),转义后源码保持 ASCII。
TEAM_SIZE_SEPARATORS = ("\u3001", ",", "\uff1b")

TEAM_SIZE_SQL = (
    "length(coalesce(t.project_owner_name, '')) - length("
    + "replace(" * len(TEAM_SIZE_SEPARATORS)
    + "coalesce(t.project_owner_name, '')"
    + "".join(f", '{sep}', '')" for sep in TEAM_SIZE_SEPARATORS)
    + ") + 1"
)

RANK_GROUP_SQL: dict[str, tuple[str, str]] = {
    "project_group": ("t.project_group", "t.project_group"),
    "board": ("b.name", "b.sort_order"),
    "primary_category": ("pc.name", "pc.id"),
    "status": ("t.status", "t.status"),
}


def rank_tasks(
    metric: str = "progress_rounds",
    mode: str = "cut",
    top: int = 5,
    ascending: bool = False,
    group_by: str | None = None,
    board_code: str | None = None,
    granted_optional: tuple[str, ...] = (),
    shape: str = "rank",
) -> tuple[str, tuple]:
    """任务排名:并列规则由服务端定(cut / keep_ties / per_group)。

    三种语义在**同一份数据上返回不同的集合与行数**,不能让调用方拿到明细后自己裁:

    * ``cut``:硬切前 N 条,并列按 task id 定序(返回 ``total_count`` = 符合口径的任务总数,
      不是本次行数 —— 问"每个任务各有几个"时集合大小由它定);
    * ``keep_ties``:用 ``RANK()`` 保留并列,返回到第 N 名为止的**全部**任务。
      演示数据里"进展期数前 3 名"是 **12 行**(第 3 名有 12 条并列),而 cut 只给 3 行;
    * ``per_group``:每组第一名,一组一行,``top`` 在这一档无意义。

    ``shape`` 是两个**工具**各自的参考查询形状,语义不同不能互相顶替:

    * ``rank``(``weekly_rank``):LEFT JOIN 保留零值任务(升序问"最少"时它们是答案),
      列名 ``task_id / task_name / metric_value``,cut 档另给 ``total_count``;
    * ``count``(``weekly_task_ranking``):INNER JOIN,只让**有该子表记录**的任务参赛,
      列名 ``id / task_name / cnt``,没有 ``total_count``(refer 查询也没有),
      只支持 cut 且固定降序 —— 参考查询就是这么写的。

    两档都多给一列 ``tie_count`` = **并列在同一度量值上的任务数**(调用方把它提成
    顶层的 ``tied_at_top``)。硬切在并列值上的取舍是任意的:少了这个数,"进展期数最多的
    任务"会被念成唯一的第一名,而真实情况可能是十几条并列;碰上"整列同值"的度量
    (项目团队人数在演示数据里 128 条全是 1 人),不带并列信息就只能答出一个假冠军。

    NULL 排序按 MySQL 语义对齐:降序时 NULL 在最后、升序时在最前
    (PG 默认相反,不写 ``NULLS LAST/FIRST`` 会与演示源给出不同的名次)。
    """
    if metric not in RANK_METRICS:
        raise ValueError(f"不支持的 metric:{metric};支持 {', '.join(sorted(RANK_METRICS))}")
    if mode not in RANK_MODES:
        raise ValueError(f"不支持的 mode:{mode};支持 {', '.join(RANK_MODES)}")
    if shape not in RANK_SHAPES:
        raise ValueError(f"不支持的 shape:{shape};支持 {', '.join(RANK_SHAPES)}")
    if shape == "count" and mode != "cut":
        raise ValueError("shape=count 只有 cut 档(参考查询没有并列语义)")
    if shape == "count" and ascending:
        raise ValueError("shape=count 固定降序(参考查询 ORDER BY cnt DESC)")
    bound = max(1, min(200, int(top)))
    table, gate, expression, _label, optional = RANK_METRICS[metric]
    if shape == "count" and not table:
        raise ValueError(f"shape=count 需要子表度量,{metric} 的值在 task 行上")
    if optional and optional not in granted_optional:
        hint = adm.require_optional_table(optional, False)
        raise PermissionError(hint)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)

    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    joins = ""
    if board:
        joins = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    if table:
        expr = expression
        join_kind = "JOIN" if shape == "count" else "LEFT JOIN"
        joins += f"\n{join_kind} {table} x ON x.task_id = t.id AND {gate}"
    else:
        expr = TEAM_SIZE_SQL
        # 零负责人的任务不算"1 人团队"而是**没有团队可比**,直接排除:
        # coalesce 兜底会把空串算成 1 人,于是「人数最多」的尾巴里混进一批假 1 人。
        where.append("t.project_owner_name IS NOT NULL AND t.project_owner_name <> ''")
    where_sql = "\n  AND ".join(where)
    direction = "ASC" if ascending else "DESC"
    # 对齐 MySQL:降序 NULL 最后、升序 NULL 最前
    nulls = "NULLS FIRST" if ascending else "NULLS LAST"

    # 不 JOIN 子表的度量(项目团队人数)直接引用 task 行上的列,必须进 GROUP BY:
    # PG 只在有主键/唯一非空约束时才做函数依赖推断,缺约束时会直接报
    # "column ... must appear in the GROUP BY clause"。带上这一列在任何 schema 下都成立。
    group_extra = ", t.project_owner_name" if not table else ""

    if mode == "keep_ties":
        # 不额外给 row_count_to_place:它恒等于信封的 row_count,多一个同值列只会
        # 诱使调用方去比对两个本来就相等的数;演示源的 keep_ties 也只给这 4 列。
        sql = f"""
WITH ranked AS (
    SELECT t.id AS task_id, t.task_name, {expr} AS metric_value,
           rank() OVER (ORDER BY {expr} {direction} {nulls}) AS rk
    FROM task t
    {joins}
    WHERE {where_sql}
    GROUP BY t.id, t.task_name{group_extra}
)
SELECT task_id, task_name, metric_value, rk
FROM ranked
WHERE rk <= %s
ORDER BY rk, task_name
"""
        return sql, (*params, bound)

    if mode == "per_group":
        axis_key = (group_by or "").strip()
        grouping = RANK_GROUP_SQL.get(axis_key)
        if grouping is None:
            raise ValueError(f"per_group 需要 group_by,支持 {', '.join(RANK_GROUPINGS)}")
        axis, order_key = grouping
        extra = ""
        if axis_key == "board":
            extra = f"\nJOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        elif axis_key == "primary_category":
            extra = (
                f"\nJOIN task_category c ON c.id = t.category_id AND {adm.sql_soft_delete('c')}"
                f"\nJOIN task_category pc ON pc.id = c.parent_id AND {adm.sql_soft_delete('pc')}"
            )
        # 三部分 JOIN 各司其职,不能互相顶替:
        #   axis_join  —— 分组轴需要的表(board 轴用 b,一级分类轴用 c+pc)
        #   filter_join —— 调用方给了 board 过滤时的看板表(board 轴已由 axis_join 提供)
        #   metric_join —— **度量自己的 LEFT JOIN**,漏掉它会报 missing FROM-clause entry for table "x"
        axis_join = extra
        filter_join = ""
        if board and axis_key != "board":
            filter_join = f"\nJOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        metric_join = f"\nLEFT JOIN {table} x ON x.task_id = t.id AND {gate}" if table else ""
        sql = f"""
WITH per_task AS (
    SELECT t.id AS task_id, t.task_name, {axis} AS bucket, {order_key} AS bucket_order,
           {expr} AS metric_value
    FROM task t
    {axis_join}
    {filter_join}
    {metric_join}
    WHERE {where_sql}
    GROUP BY t.id, t.task_name, {axis}, {order_key}
), ranked AS (
    SELECT task_id, task_name, bucket, metric_value,
           row_number() OVER (PARTITION BY bucket
                              ORDER BY metric_value {direction} {nulls}, bucket_order, task_id) AS rn
    FROM per_task
)
SELECT bucket, task_id, task_name, metric_value
FROM ranked
WHERE rn = 1
ORDER BY bucket
"""
        return sql, tuple(params)

    if shape == "count":
        # weekly_task_ranking 的参考查询是 **INNER JOIN** + COUNT(*) + ORDER BY cnt DESC, t.id:
        # 它问的是"哪条任务最多",零条目的任务不是零分参赛者而是**不参赛**
        # (LEFT JOIN 会让它们以 0 混进 top=50 的尾巴里,集合与参考查询不同)。
        # 列名也照抄参考查询(id / task_name / cnt),换数据源不该换字段名。
        # tie_count 先按任务算完再开窗:PARTITION BY count(x.id) 是聚合套窗口,PG 直接报错。
        sql = f"""
WITH per_task AS (
    SELECT t.id AS id, t.task_name, count(x.id) AS cnt
    FROM task t
    {joins}
    WHERE {where_sql}
    GROUP BY t.id, t.task_name
), ranked AS (
    SELECT id, task_name, cnt, count(*) OVER (PARTITION BY cnt) AS tie_count
    FROM per_task
)
SELECT id, task_name, cnt, tie_count
FROM ranked
ORDER BY cnt DESC, id
LIMIT %s
"""
        return sql, (*params, bound)

    sql = f"""
WITH per_task AS (
    SELECT t.id AS task_id, t.task_name, {expr} AS metric_value
    FROM task t
    {joins}
    WHERE {where_sql}
    GROUP BY t.id, t.task_name{group_extra}
), ranked AS (
    SELECT task_id, task_name, metric_value,
           count(*) OVER () AS total_count,
           count(*) OVER (PARTITION BY metric_value) AS tie_count
    FROM per_task
)
SELECT task_id, task_name, metric_value, total_count, tie_count
FROM ranked
ORDER BY metric_value {direction} {nulls}, task_id
LIMIT %s
"""
    return sql, (*params, bound)


# ---- batch 10: 人员统计(person_stats 的 9 个 scope)--------------------------

PERSON_SCOPES = (
    "workload",
    "workload_top",
    "workload_summary",
    "single_task",
    "group_roster",
    "cross_group",
    "dual_role",
    "id_format",
    "reporters",
)

# role -> (分组列, 中文标签, 对应姓名列或空)
# 「主责人」落在工号列上,与姓名列不是同一批人(演示数据里技术组 owner_user_id 45 人、
# 姓名列 45 人,而牵头人只有 16 人),所以三者必须分开问。
PERSON_ROLES: dict[str, tuple[str, str, str]] = {
    "lead_owner": ("lead_owner_name", "牵头领导", ""),
    "project_owner": ("project_owner_name", "项目负责人", ""),
    "owner": ("owner_user_id", "主责人", ""),
}


def _person_columns(role: str) -> tuple[str, str]:
    if role not in PERSON_ROLES:
        raise ValueError(f"不支持的 role:{role};支持 {', '.join(PERSON_ROLES)}")
    column, label, _name_column = PERSON_ROLES[role]
    return column, label


def person_stats(
    scope: str,
    role: str = "lead_owner",
    project_group: str | None = None,
    board_code: str | None = None,
    top: int = 200,
) -> tuple[str, tuple]:
    """人员统计(按任务数聚合),对齐 mock 的 ``weekly_person_stats``。

    口径要点:

    * **姓名为空的行不是「一个叫空的人」**:计人头时必须排除,否则人数会多 1;
    * ``workload`` 是**硬切**(`top=1` 只给首行,并列被切掉),``workload_top`` 是
      ``HAVING = MAX`` **保留并列**(两者答的是两个问题,不能互相代答);
      并列个数由服务端给出(``tied_at_top``),不让模型在明细上临场裁决;
    * ``workload_summary`` 的 ``avg_tasks_per_person`` 是**全局均值**,不是组内均值的平均;
    * ``group_roster`` 数的是**去重后的人**(标准安全组 19 条任务只有 9 位牵头人),
      不能拿任务条数当人数;
    * ``id_format`` 只统计**有标识**的任务,空标识不进任何档,各档相加不等于任务总数;
    * ``reporters`` 的口径是"任务闸门 + ``p.is_published = 1``"两道闸门,
      填报人在 ``task_progress`` 上而不在 ``task`` 上。
    """
    if scope not in PERSON_SCOPES:
        raise ValueError(f"未知 scope:{scope};可选 {', '.join(PERSON_SCOPES)}")
    column, _label = _person_columns(role)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    joins = ""
    if board:
        joins = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    gate_sql = "\n  AND ".join(where)
    named = f"{gate_sql}\n  AND t.{column} IS NOT NULL\n  AND t.{column} <> ''"

    if scope in ("workload", "single_task", "group_roster"):
        extra = ""
        if scope == "group_roster":
            target = (project_group or "").strip()
            if not target:
                raise ValueError("group_roster 需要 project_group(先用规模横截面看有哪些组)")
            extra = "\n  AND t.project_group = %s"
            params.append(target)
        having = "\nHAVING count(*) = 1" if scope == "single_task" else ""
        sql = f"""
SELECT t.{column} AS person, count(*) AS task_count
FROM task t
{joins}
WHERE {named}{extra}
GROUP BY t.{column}{having}
ORDER BY task_count DESC, person
LIMIT %s
"""
        return sql, (*params, int(top))

    if scope == "workload_top":
        # 子查询自己也要看板闸门(它只查 task,别名换成 t2/b2),否则带 board 过滤时
        # 会引用一个不存在的别名。参数按 SQL 文本顺序:外层先、子查询后。
        inner_where = [adm.sql_task_admission("pg", "t2")]
        inner_join = ""
        inner_params: list[object] = []
        if board:
            inner_join = f"JOIN task_board b2 ON b2.id = t2.board_id AND {adm.sql_soft_delete('b2')}"
            inner_where.append("b2.code = %s")
            inner_params.append(board)
        sql = f"""
SELECT t.{column} AS person, count(*) AS task_count
FROM task t
{joins}
WHERE {named}
GROUP BY t.{column}
HAVING count(*) = (
    SELECT max(g.c) FROM (
        SELECT count(*) AS c FROM task t2
        {inner_join}
        WHERE {" AND ".join(inner_where)}
          AND t2.{column} IS NOT NULL AND t2.{column} <> ''
        GROUP BY t2.{column}
    ) g
)
ORDER BY person
"""
        return sql, (*params, *inner_params)

    if scope == "workload_summary":
        sql = f"""
SELECT count(*)                                                AS tasks,
       count(DISTINCT t.{column})                              AS people,
       round(count(*)::numeric / NULLIF(count(DISTINCT t.{column}), 0), 2) AS avg_tasks_per_person
FROM task t
{joins}
WHERE {named}
"""
        return sql, tuple(params)

    if scope == "cross_group":
        sql = f"""
SELECT t.{column} AS person,
       count(DISTINCT t.project_group)                              AS group_count,
       string_agg(DISTINCT t.project_group, ',' ORDER BY t.project_group) AS group_list,
       count(*)                                                     AS task_count
FROM task t
{joins}
WHERE {named}
  AND t.project_group IS NOT NULL
GROUP BY t.{column}
HAVING count(DISTINCT t.project_group) > 1
ORDER BY group_count DESC, person
LIMIT %s
"""
        return sql, (*params, int(top))

    if scope == "dual_role":
        sql = f"""
SELECT x.person, x.as_lead, x.as_project_owner
FROM (
    SELECT t.lead_owner_name AS person,
           count(*)         AS as_lead,
           (SELECT count(*) FROM task t2
             WHERE {adm.sql_task_admission("pg", "t2")}
               AND t2.project_owner_name = t.lead_owner_name) AS as_project_owner
    FROM task t
    {joins}
    WHERE {gate_sql}
      AND t.lead_owner_name IS NOT NULL
      AND t.lead_owner_name <> ''
    GROUP BY t.lead_owner_name
) x
WHERE x.as_project_owner > 0
ORDER BY x.as_lead DESC, x.person
LIMIT %s
"""
        return sql, (*params, int(top))

    if scope == "id_format":
        # LIKE 模式里的百分号要写成双百分号:psycopg 会对整条 SQL 文本做占位符解析。
        # 注意注释也别写进 SQL 文本里 —— 注释里的单个百分号同样会被解析(踩过)。
        sql = f"""
SELECT CASE
           WHEN t.owner_user_id ~ '^[0-9]+$'   THEN '纯数字工号'
           WHEN t.owner_user_id LIKE 'u%%'     THEN 'u 前缀账号'
           WHEN t.owner_user_id LIKE 'NDG%%'   THEN 'NDG 域账号'
           ELSE '其他'
       END      AS id_format,
       count(*) AS task_count
FROM task t
{joins}
WHERE {gate_sql}
  AND t.owner_user_id IS NOT NULL
  AND t.owner_user_id <> ''
GROUP BY id_format
ORDER BY task_count DESC, id_format
"""
        return sql, tuple(params)

    # reporters:任务闸门 + 进展行发布闸门,两道都要
    sql = f"""
SELECT p.reporter_id, count(*) AS reported_rounds, count(DISTINCT p.task_id) AS tasks
FROM task_progress p
JOIN task t ON t.id = p.task_id
{joins}
WHERE {gate_sql}
  AND {adm.sql_published_progress("pg", "p")}
GROUP BY p.reporter_id
ORDER BY reported_rounds DESC, p.reporter_id
LIMIT %s
"""
    return sql, (*params, int(top))


def person_ties(role: str = "lead_owner", board_code: str | None = None) -> tuple[str, tuple]:
    """``workload`` 的并列自检:首名的任务数以及与他并列的人数。

    单独一条查询,因为它是**数据**(并列人数),不是让模型去看明细自己数——
    问句是单数("最多的是谁")时按首行答,再据 ``tied_at_top`` 补一句"另有 N 人并列"。
    """
    column, _label = _person_columns(role)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    joins = ""
    if board:
        joins = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    inner_where = [adm.sql_task_admission("pg", "t2")]
    inner_join = ""
    inner_params: list[object] = []
    if board:
        inner_join = f"JOIN task_board b2 ON b2.id = t2.board_id AND {adm.sql_soft_delete('b2')}"
        inner_where.append("b2.code = %s")
        inner_params.append(board)
    sql = f"""
SELECT max(g.c)                                                        AS top_task_count,
       count(*) FILTER (WHERE g.c = (SELECT max(g2.c) FROM (
           SELECT count(*) AS c FROM task t2
           {inner_join}
           WHERE {" AND ".join(inner_where)}
             AND t2.{column} IS NOT NULL AND t2.{column} <> ''
           GROUP BY t2.{column}) g2))                                  AS tied_at_top
FROM (
    SELECT count(*) AS c FROM task t
    {joins}
    WHERE {"\n  AND ".join(where)}
      AND t.{column} IS NOT NULL AND t.{column} <> ''
    GROUP BY t.{column}
) g
"""
    return sql, (*params, *inner_params)


# ---- batch 11: 集团板历史 / 集团板多值负责人 / 附件统计细化 -------------------

GROUP_HISTORY_SCOPES = ("rows", "year", "month", "quarter", "task", "reporter", "lag", "linkage")

# 分组轴 -> (表达式, ORDER BY)。口径照抄参考实现:分档按 bucket,名次题按
# progress_count DESC 后接定序键,滞报榜按 lag_days。
GROUP_HISTORY_GROUPINGS: dict[str, tuple[str, str]] = {
    "year": ("extract(year from {ts})::int::text", "bucket"),
    "month": ("to_char({ts}, 'YYYY-MM')", "bucket"),
    "quarter": ("extract(year from {ts})::int::text || 'Q' || extract(quarter from {ts})::int::text", "bucket"),
    # 名次题的定序键一律 task id,不按任务名:同名次的两个集合不同
    "task": ("t.task_name", "progress_count DESC, t.id"),
    "reporter": ("h.reporter_id", "progress_count DESC, bucket"),
    "lag": ("t.id", "lag_days DESC, task_id"),
    "linkage": ("t.id", "bucket"),
}


def _multivalue_array(column: str) -> str:
    """把多值列切成数组:分隔符**顿号与逗号都要处理**,空格一律去掉。

    演示数据里两种分隔符混用(有的任务写"任建华、潘启明",有的写"胡建国,方永康"),
    只按顿号切会把逗号串当成一个人;而用 LIKE '%名字%' 又会在不同人之间碰撞
    (multivalue_like_collision)。所以元素级匹配必须先把两种分隔符统一再切数组。
    """
    return f"string_to_array(replace(replace(coalesce({column}, ''), ',', '、'), ' ', ''), '、')"


def _group_history_window(
    as_of: str,
    date_from: str,
    date_to: str,
    last_days: int,
    last_months: int,
) -> tuple[list[str], list[object], list[str]]:
    """集团历史的日期窗(按 ``report_time`` 的**日期部分**比)。

    ``report_time`` 是时间戳而窗口端点是日期,直接 ``<=`` 会把最后一天切在 00:00,
    当天 18:40 报的那条就丢了。``last_months`` 按自然月回溯,不是 N*30 天
    (最近三个月 = 05-15,90 天 = 05-17,差的正是那三条五月的行)。
    """
    if last_days and last_months:
        raise ValueError("last_days 与 last_months 只能给一个:两者边界不同,同时给会得出第三个窗口")
    where: list[str] = []
    params: list[object] = []
    notes: list[str] = []
    lo = date_from.strip()
    hi = date_to.strip()
    if last_months:
        lo = lo or _month_back(as_of, last_months)
        hi = hi or as_of
        notes.append(f"最近 {last_months} 个月按自然月回溯(非 {last_months * 30} 天),基准日 {as_of}")
    elif last_days:
        lo = lo or _days_back(as_of, last_days)
        hi = hi or as_of
        notes.append(f"窗口以数据基准日 {as_of} 为基准(非系统当前时间)")
    if lo:
        where.append("({ts})::date >= %s")
        params.append(lo)
    if hi:
        where.append("({ts})::date <= %s")
        params.append(hi)
    if lo or hi:
        notes.append(f"上报时间介于 {lo or '不限'} 与 {hi or '不限'} 之间(含端点)")
    return where, params, notes


def _days_back(as_of: str, days: int) -> str:
    return (dt.date.fromisoformat(as_of) - dt.timedelta(days=int(days))).isoformat()


def _month_back(as_of: str, months: int) -> str:
    """自然月回溯:月份减 N,日号保留(跨月越界时取该月最后一天)。"""
    base = dt.date.fromisoformat(as_of)
    total = base.year * 12 + (base.month - 1) - int(months)
    year, month = divmod(total, 12)
    month += 1
    last_day = (dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(year, month, min(base.day, last_day)).isoformat()


def _group_history_gate(
    *,
    task_id: int | None = None,
    version_no: int | None = None,
    latest_only: bool = False,
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    last_months: int = 0,
    as_of: str = "",
    granted: bool = True,
) -> tuple[str, tuple]:
    """集团历史的公共闸门(**明细 / 总数 / 滞报分母共用同一份条件**)。

    共用是必须的:三处各写一遍,任何一处漏掉 ``is_published = 1`` 或日期窗,
    明细与它自己的总数就对不上,而两边看上去都"正常"。
    """
    hint = adm.require_optional_table("task_group_progress_history", granted)
    if hint:
        raise PermissionError(hint)
    ts = adm.parse_ts_sql("h.report_time")
    where = [adm.sql_task_admission("pg", "t"), "h.is_published = 1"]
    params: list[object] = []
    if task_id is not None:
        where.append("h.task_id = %s")
        params.append(int(task_id))
    if version_no is not None:
        where.append("h.version_no = %s")
        params.append(int(version_no))
    if latest_only:
        where.append(
            "h.version_no = (SELECT max(h2.version_no) FROM task_group_progress_history h2 "
            "WHERE h2.task_id = h.task_id AND h2.is_published = 1)"
        )
    window_where, window_params, _notes = _group_history_window(
        as_of or dt.date.today().isoformat(), date_from, date_to, last_days, last_months
    )
    for fragment, value in zip(window_where, window_params, strict=True):
        where.append(fragment.format(ts=ts))
        params.append(value)
    return "\n  AND ".join(where), tuple(params)


def group_history_window_note(
    as_of: str,
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    last_months: int = 0,
) -> str:
    """日期窗的口径文案(没给窗口时返回空串)。"""
    _where, _params, notes = _group_history_window(as_of, date_from, date_to, last_days, last_months)
    return "\uff1b".join(notes)  # 全角分号按转义写:RUF001 不接受字面量


def group_history(
    scope: str = "rows",
    as_of: str = "",
    task_id: int | None = None,
    version_no: int | None = None,
    latest_only: bool = False,
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    last_months: int = 0,
    granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """集团板进展历史(``task_group_progress_history``,可选表)。

    集团板的进展写在这张表里,**``task_progress`` 一行都没有** —— 所以
    ``weekly_progress_history`` / ``weekly_progress_range`` 对集团任务返回空,
    这里是它们的入口。

    两道闸门必须同时成立:任务正式(R-01)**且**行 ``is_published = 1``;
    少任何一道就会把 42 条未审草稿算进来(演示数据:全表 404 行、已发布 362 行、草稿 42 行)。

    唯一**故意不过第二道闸门**的是 ``linkage``:它问的是"有多少行挂上了提交单",
    分母该是表内全部 404 行,用过闸的 362 行会把 42 条草稿的挂接状况一起丢掉。
    """
    if scope not in GROUP_HISTORY_SCOPES:
        raise ValueError(f"未知 scope:{scope};可选 {', '.join(GROUP_HISTORY_SCOPES)}")
    ts = adm.parse_ts_sql("h.report_time")
    where_sql, params = _group_history_gate(
        task_id=task_id,
        version_no=version_no,
        latest_only=latest_only,
        date_from=date_from,
        date_to=date_to,
        last_days=last_days,
        last_months=last_months,
        as_of=as_of,
        granted=granted,
    )
    params = list(params)

    if scope in ("year", "month", "quarter", "task", "reporter"):
        axis, order = GROUP_HISTORY_GROUPINGS[scope]
        expression = axis.format(ts=ts) if "{ts}" in axis else axis
        # task 档把 task_id 一并选出并纳入 GROUP BY:并列要按 id 定序,只回任务名
        # 调用方手上没有定序键(按名排与按 id 排是两个不同的前 5 条)。
        if scope == "task":
            select = f"t.id AS task_id, {expression} AS bucket, count(*) AS progress_count"
            group_sql = "t.id, bucket"
        else:
            select = f"{expression} AS bucket, count(*) AS progress_count, count(DISTINCT h.task_id) AS task_count"
            group_sql = "bucket"
        sql = f"""
SELECT {select}
FROM task_group_progress_history h
JOIN task t ON t.id = h.task_id
WHERE {where_sql}
GROUP BY {group_sql}
ORDER BY {order}
LIMIT %s
"""
        return sql, (*params, int(limit))

    if scope == "linkage":
        # 唯一**故意不过** is_published 闸门的一档:问的是挂接率,分母是表内全部
        # 404 行;只用过闸的 362 行会把 42 条草稿的挂接状况一起丢掉。
        bare = "\n  AND ".join(f for f in where_sql.split("\n  AND ") if "h.is_published" not in f)
        sql = f"""
SELECT count(*)                                                          AS total_rows,
       count(*) FILTER (WHERE h.workflow_submission_id IS NOT NULL)       AS linked_rows,
       count(*) FILTER (WHERE h.workflow_submission_id IS NULL)           AS unlinked_rows,
       count(*) FILTER (WHERE h.is_published = 1)                         AS published_rows
FROM task_group_progress_history h
JOIN task t ON t.id = h.task_id
WHERE {bare}
"""
        return sql, tuple(params)

    if scope == "lag":
        # 滞报天数取 MAX(report_time) 与**基准日**之差,不是 MIN,更不是 now():
        # 问的是"最后一次报到现在多久",用最早一期会把老任务全排到榜首,
        # 用系统当前时间则整榜都错(演示数据的基准日是 2026-08-15)。
        sql = f"""
SELECT t.id AS task_id, t.task_name,
       (%s::date - max({ts})::date)::int AS lag_days,
       count(*)                          AS rounds,
       {adm.normalize_ts_sql("max(h.report_time)")} AS last_report_time
FROM task_group_progress_history h
JOIN task t ON t.id = h.task_id
WHERE {where_sql}
GROUP BY t.id, t.task_name
ORDER BY lag_days DESC, t.id
LIMIT %s
"""
        return sql, (as_of or dt.date.today().isoformat(), *params, int(limit))

    # 明细:列集合照抄参考实现(不再多给 id / task_no / is_published 等自身列),
    # 总数与涉及任务数由调用方作为顶层键给出,问"一共多少行"时才不必再查一次。
    sql = f"""
SELECT h.task_id, t.task_name, h.version_no, h.progress_effect, h.completion_time,
       h.reporter_id, {adm.normalize_ts_sql("h.report_time")} AS report_time
FROM task_group_progress_history h
JOIN task t ON t.id = h.task_id
WHERE {where_sql}
ORDER BY h.task_id, h.version_no DESC, h.id DESC
LIMIT %s
"""
    return sql, (*params, int(limit))


def group_history_totals(
    task_id: int | None = None,
    version_no: int | None = None,
    latest_only: bool = False,
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    last_months: int = 0,
    as_of: str = "",
    granted: bool = True,
) -> tuple[str, tuple]:
    """与明细同一口径的**总数**与涉及任务数(与参考实现同法单独查一次)。

    200 行封顶之后调用方还原不出真值:已发布 362 行会被报成"200 行且还有更多"。
    滞报榜取其中的 ``total_tasks`` 作分母 —— 本表只有报过的任务,从未报过的不在榜上。
    """
    where_sql, params = _group_history_gate(
        task_id=task_id,
        version_no=version_no,
        latest_only=latest_only,
        date_from=date_from,
        date_to=date_to,
        last_days=last_days,
        last_months=last_months,
        as_of=as_of,
        granted=granted,
    )
    sql = f"""
SELECT count(*) AS total_rows, count(DISTINCT h.task_id) AS total_tasks
FROM task_group_progress_history h
JOIN task t ON t.id = h.task_id
WHERE {where_sql}
"""
    return sql, params


def group_owner(
    person: str | None = None,
    role: str = "lead",
    limit: int = 200,
) -> tuple[str, tuple]:
    """集团板多值负责人查询(``task_group_detail`` 的两列多值文本)。

    ``role=lead`` 用 ``lead_owner_ids`` / ``lead_owner_names``,``role=project`` 用
    ``project_owner_*``;两者是**不同的角色、不同的列**,不可互换。

    匹配是**元素级精确**(把两种分隔符统一后切数组再判等):
    用 ``LIKE '%名字%'`` 会在不同人之间碰撞(短名是长名的子串时尤其明显)。
    """
    roles = {
        "lead": ("lead_owner_ids", "lead_owner_names", "牵头人"),
        "project": ("project_owner_ids", "project_owner_names", "项目负责人"),
    }
    key = (role or "lead").strip().lower()
    if key not in roles:
        raise ValueError(f"不支持的角色:{role};支持 {', '.join(sorted(roles))}")
    id_column, name_column, _label = roles[key]
    where = [adm.sql_task_admission("pg", "t"), "b.code = 'group'"]
    params: list[object] = []
    if person and person.strip():
        token = person.strip().replace(" ", "")
        where.append(
            f"(%s = ANY({_multivalue_array('g.' + id_column)}) OR %s = ANY({_multivalue_array('g.' + name_column)}))"
        )
        params.extend([token, token])
    sql = f"""
SELECT g.task_id, t.task_no, t.task_name,
       g.{id_column}   AS owner_ids,
       g.{name_column} AS owner_names,
       g.project_group
FROM task_group_detail g
JOIN task t ON t.id = g.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {"\n  AND ".join(where)}
ORDER BY t.sort_order, t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)
