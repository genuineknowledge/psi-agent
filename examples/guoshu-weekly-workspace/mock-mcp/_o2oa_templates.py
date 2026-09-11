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
import re

import _admission as adm

# 调用方给的日期要么是这个格式,要么就是口径错(与 ``_formal`` 同一形状)。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
       t.board_id, t.category_id, t.status,
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


PROGRESS_DATE_FIELDS = ("progress_date", "report_time")
"""``weekly_progress_range`` 的时间轴字段(与参考实现的 ``_PROGRESS_DATE_FIELDS`` 同域)。

两个字段答的不是同一个问题:``progress_date`` 是**所报周期**,``report_time`` 是
**交上来的时刻**。补报时后者晚于前者,所以「什么时候交的」必须能切到 report_time
—— 拿 progress_date 答它会把补报的那批算到它们所属的周期上。
"""

PROGRESS_GROUPINGS = ("month", "quarter", "task")
"""``by=`` 的三个分组轴,与参考实现的 ``_PROGRESS_GROUPINGS`` 同域同序。

``month`` / ``quarter`` 不带 ``peak`` 时要**环比**(相邻两档并排),这不是修饰:
只给各月计数,调用方自己错位相减会把上一档记错(参考实现为此吃过 K3-03 那轮返工)。
"""


def progress_date_expr(date_field: str) -> str:
    """时间轴字段的**可比较时序表达式**(窗口过滤与排序都用它)。

    两列在库里的存储形状不同(period 是 date、report_time 是时间戳或文本),所以
    一律先 ``::timestamp`` 再比较 —— 直接比原列在文本存储的环境里是字典序比较。
    """
    if date_field not in PROGRESS_DATE_FIELDS:
        raise ValueError(
            f"不支持的 date_field:{date_field};支持 {', '.join(PROGRESS_DATE_FIELDS)}"
        )
    return adm.parse_ts_sql(f"p.{date_field}")


def _progress_window_clause(
    date_field: str, date_from: str, date_to: str
) -> tuple[str, list[object]]:
    """窗口条件(含两端的闭区间)。**两端都可以为空** —— 这就是"无界"。

    参考实现的 ``date_from`` / ``date_to`` 文档写的是 "Empty means unbounded",
    所以空串不是"没给参数"而是"这一端不设限":把它当成必须值会让
    ``weekly_progress_range()`` 这个**默认档**在正式源上无法回答(此前正是回落的一档)。
    """
    expr = progress_date_expr(date_field)
    clauses: list[str] = []
    params: list[object] = []
    if date_from:
        clauses.append(f"AND {expr} >= %s")
        params.append(date_from)
    if date_to:
        clauses.append(f"AND {expr} <= %s")
        params.append(date_to)
    return " ".join(clauses), params


def _progress_bucket_expr(by: str, date_field: str) -> str:
    """分组表达式(``bucket`` 这个 SELECT 别名是下游排序的锚点)。"""
    if by == "month":
        # substr 取 YYYY-MM:与参考实现的 DATE_FORMAT('%Y-%m') 同一形状、同序
        return f"substr(to_char(({progress_date_expr(date_field)}), 'YYYY-MM-DD'), 1, 7)"
    if by == "quarter":
        # 2026Q1:与参考实现的 CONCAT(YEAR, 'Q', QUARTER) 同形
        expr = progress_date_expr(date_field)
        return (
            f"to_char(({expr}), 'YYYY') || 'Q' || "
            f"(((extract(month from ({expr}))::int - 1) / 3) + 1)::text"
        )
    if by == "task":
        return "t.task_name"
    raise ValueError(f"不支持的 by:{by};支持 {', '.join(PROGRESS_GROUPINGS)}")


def progress_range(
    board_code: str | None,
    date_from: str,
    date_to: str,
    limit: int = 200,
    date_field: str = "progress_date",
    by: str = "",
) -> tuple[str, tuple]:
    """某时间窗内的正式进展(rule 2:只取展示版本),按时间轴字段倒序。

    两个轴都可为**空**(无界),与参考实现一致:空串表示这一端不设限,不是"缺参数"。

    列集合照抄参考查询:``task_id / task_name / version_no / progress_date /
    report_time / lag_days``。进展正文(latest_progress / next_work)与填报人不在
    这个工具的返回里 —— 它答的是"哪些任务在窗口内报过",列正文只会把 200 行的
    回包撑成几万字,而正文有专门的逐任务出口。
    ``lag_days`` 恒为 ``上报日 - 周期日``(补报更早周期时为正),**不随 date_field 变**:
    它答的就是"报的是哪一期、什么时候交的"这组关系,切轴只换过滤与排序的轴。

    ``by`` 走 :func:`progress_range_grouped` —— 分组是另一张表,列也不同。
    """
    grouping = (by or "").strip().lower()
    if grouping:
        return progress_range_grouped(board_code, date_from, date_to, limit, date_field, grouping)

    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    board_clause = "AND b.code = %s" if code else ""
    params: list[object] = [code] if code else []
    window, window_params = _progress_window_clause(date_field, date_from, date_to)
    params.extend(window_params)
    expr = progress_date_expr(date_field)
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
  {window}
ORDER BY {expr} DESC, t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def progress_range_totals(
    board_code: str | None,
    date_from: str,
    date_to: str,
    date_field: str = "progress_date",
) -> tuple[str, tuple]:
    """同一窗口的**总数**:行数与涉及任务数。

    单列出来是因为它们必须活过截断:"今年以来报了多少期"是 366 行,取到 200 行 +
    ``has_more`` 之后调用方无法还原真值,只能报"至少 200"。与参考查询同法(先查总数,
    再查明细)。窗口两端同样可为空(无界)。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    board_clause = "AND b.code = %s" if code else ""
    params: list[object] = [code] if code else []
    window, window_params = _progress_window_clause(date_field, date_from, date_to)
    params.extend(window_params)
    sql = f"""
SELECT count(*) AS total_rows, count(DISTINCT p.task_id) AS total_tasks
FROM task_progress p
JOIN task t     ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  {board_clause}
  AND {adm.sql_published_progress("pg", "p")}
  {window}
"""
    return sql, tuple(params)


def _progress_grouped_base(
    board_code: str | None,
    date_from: str,
    date_to: str,
    date_field: str,
    grouping: str,
) -> tuple[str, list[object]]:
    """分组查询的公共部分(闸门 + 窗口 + ``GROUP BY bucket``),**不带 ORDER BY / LIMIT**。

    抽出来是因为环比档要把它整段当子查询包一层。``progress_momentum`` 不能回头调
    ``progress_range_grouped(peak=False)``:那条路径对 month/quarter 正是"去取环比",
    会立刻递归回自己 —— 实测就是这么炸的(RecursionError)。所以"基础查询"与
    "要不要包环比"必须是两层,不能互相调。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    board_clause = "AND b.code = %s" if code else ""
    params: list[object] = [code] if code else []
    window, window_params = _progress_window_clause(date_field, date_from, date_to)
    params.extend(window_params)

    bucket = _progress_bucket_expr(grouping, date_field)
    select = f"{bucket} AS bucket, count(*) AS progress_count"
    if grouping != "task":
        select += ", count(DISTINCT p.task_id) AS task_count"
    # 周期日为空的行走不进任何时间档:库里有 44 行已发布进展的 progress_date 是 NULL,
    # 分组时它们会挤成一个 bucket = NULL 的档 —— 而那个档在计数降序下会冲到第一,
    # 「进展最多的月份」于是答成「那 44 行没有周期日的」。它不是"某个时间段的计数",
    # 是**口径外的行**,所以分组里排除掉,并由 progress_range_unbucketed 单独报数
    # (见 _formal 的 caliber)。用 HAVING 而不是 WHERE:bucket 是 SELECT 别名,
    # WHERE 里不认,而 month/quarter 档还会把整段包成子查询。
    null_gate = ""
    if grouping != "task" and date_field == "progress_date":
        null_gate = f"HAVING {bucket} IS NOT NULL\n"
    sql = f"""
SELECT {select}
FROM task_progress p
JOIN task t     ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  {board_clause}
  AND {adm.sql_published_progress("pg", "p")}
  {window}
GROUP BY bucket
{null_gate}"""
    return sql, params


def progress_range_grouped(
    board_code: str | None,
    date_from: str,
    date_to: str,
    limit: int = 200,
    date_field: str = "progress_date",
    by: str = "month",
    peak: bool = False,
) -> tuple[str, tuple]:
    """按 ``by`` 分组的**计数**档(month / quarter / task)。

    与参考实现同形状,并且同样把两处判断留在服务端:

    * ``month`` / ``quarter`` 不带 ``peak`` 时回**环比**列(``prev_count`` /
      ``mom_change``),见 :func:`progress_momentum` —— 相邻两档该由 SQL 配对,
      不该让调用方拿着计数列表自己错位相减;
    * ``peak=True`` 时只回**一行**(计数降序、并列取 bucket 升序),``LIMIT 1`` 写进
      SQL 而不是靠 ``limit`` 截断 —— 靠截断会带出 ``has_more=True``,看着像
      "还有行没给",与"首行即答案"相冲。

    ``task`` 档按任务名分组,故列里没有 ``task_count``(一个任务名就是一组,
    再数一次任务数是恒等于 1 的假信息)。
    """
    grouping = (by or "").strip().lower()
    if grouping not in PROGRESS_GROUPINGS:
        raise ValueError(f"不支持的 by:{by};支持 {', '.join(PROGRESS_GROUPINGS)}")
    if grouping in ("month", "quarter") and not peak:
        return progress_momentum(
            board_code, date_from, date_to, limit, date_field, grouping
        )

    sql, params = _progress_grouped_base(
        board_code, date_from, date_to, date_field, grouping
    )
    order = "progress_count DESC, bucket" if grouping == "task" or peak else "bucket"
    if peak:
        # LIMIT 1 写进 SQL:靠 limit 参数截断会带出 has_more=True,与"首行即答案"相冲。
        # 这一档**不加** limit 参数,占位符与参数必须配平(多一个参数就是运行期报错)。
        sql += f"ORDER BY {order} LIMIT 1\n"
        return sql, tuple(params)
    sql += f"ORDER BY {order}\nLIMIT %s\n"
    params.append(int(limit))
    return sql, tuple(params)


def progress_range_unbucketed(
    board_code: str | None,
    date_from: str,
    date_to: str,
) -> tuple[str, tuple]:
    """**窗口内**周期日为空的正式进展行数(自检用,不进任何时间档)。

    这是一条"自检"查询,与短窗口自检同一用途:分组档把无周期的行排除在外(见
    ``_progress_grouped_base`` 的说明)。不报出这个数,调用方看到"各月之和"与
    "明细 total_count" 对不上时会以为分组漏了行 —— 差额本身是数据质量信号。

    **窗口用 ``report_time`` 判,不用 ``progress_date``** —— 这一档的定义就是
    "没有周期日",拿 period 去卡窗口会把它们全部挡掉(比较 NULL 恒为假),于是
    自检在有界窗口下恒为 0、与明细的 population 也对不上。用 ``report_time``
    才是同一批行:明细按 period 卡窗口时确实不含它们,而自检要回答的是
    "这段里交上来的、却没说属于哪一期的进展有多少"。
    """
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    board_clause = "AND b.code = %s" if code else ""
    params: list[object] = [code] if code else []
    expr = adm.parse_ts_sql("p.report_time")
    clauses = ["p.progress_date IS NULL"]
    if date_from:
        clauses.append(f"{expr} >= %s")
        params.append(date_from)
    if date_to:
        clauses.append(f"{expr} <= %s")
        params.append(date_to)
    gate = "\n  AND ".join(clauses)
    sql = f"""
SELECT count(*) AS unbucketed_rows
FROM task_progress p
JOIN task t     ON t.id = p.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  {board_clause}
  AND {adm.sql_published_progress("pg", "p")}
  AND {gate}
"""
    return sql, tuple(params)


def progress_momentum(
    board_code: str | None,
    date_from: str,
    date_to: str,
    limit: int = 200,
    date_field: str = "progress_date",
    by: str = "month",
) -> tuple[str, tuple]:
    """月/季分组 + **环比**列:``bucket / progress_count / task_count /
    prev_count / mom_change``。

    ``LAG`` 在服务端按时序取上一档:首档的 ``prev_count`` 与 ``mom_change`` 为
    NULL 是**对的**(没有上一档可比),填 0 会凭空造出一个 100% 下跌。

    注意 ``prev_count`` 只在**本次窗口内**取上一档:问"2026 年的环比"必须把窗口
    限定在该年(``date_from=2026-01-01``、``date_to=2026-12-31``),否则首档会拿到
    上一年的数 —— 那是跨年口径,不是该年的环比。这条写进 ``caliber``(见 ``_formal``)。
    """
    grouping = (by or "").strip().lower()
    if grouping not in ("month", "quarter"):
        raise ValueError(f"环比只适用于 month / quarter,收到:{by}")
    # 用基础查询而不是 progress_range_grouped:后者对 month/quarter 又回环比,会递归。
    inner_sql, inner_params = _progress_grouped_base(
        board_code, date_from, date_to, date_field, grouping
    )
    sql = f"""
SELECT bucket, progress_count, task_count,
       LAG(progress_count) OVER (ORDER BY bucket) AS prev_count,
       progress_count - LAG(progress_count) OVER (ORDER BY bucket) AS mom_change
FROM ({inner_sql}) buckets
ORDER BY bucket
LIMIT %s
"""
    return sql, (*inner_params, int(limit))


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
    group_history_granted: bool = True,
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
        # 列名照抄参考查询:published / unpublished / total / tasks
        # tasks 是**该口径覆盖的任务数**(","是几行"与"涉及几条任务"是两个问题)
        sql = f"""
SELECT count(*) FILTER (WHERE p.is_published = 1) AS published,
       count(*) FILTER (WHERE p.is_published = 0) AS unpublished,
       count(*)                                   AS total,
       count(DISTINCT p.task_id)                  AS tasks
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
"""
        return sql, tuple(params)

    if scope == "import_split":
        # 列名照抄参考查询:total / from_import / manual / manual_unpublished / batches
        # 「手工填的有多少」有两种闸门下的答案,必须给全:发布口径 943 全来自导入、手工 0;
        # 去掉发布闸门则是 1066 行里 948 导入、118 手工(那 118 条全是未发布草稿)
        sql = f"""
SELECT count(*)                                              AS total,
       count(*) FILTER (WHERE p.import_id IS NOT NULL)       AS from_import,
       count(*) FILTER (WHERE p.import_id IS NULL)           AS manual,
       count(*) FILTER (WHERE p.is_published = 0 AND p.import_id IS NULL) AS manual_unpublished,
       count(DISTINCT p.import_id)                           AS batches
FROM task_progress p
JOIN task t ON t.id = p.task_id
{board_join}
WHERE {where_sql}
  AND p.is_published = 1
"""
        return sql, tuple(params)

    if scope == "unpublished":
        # 列名照抄参考查询:status / status_label / cnt / task_count
        # cnt 是进展行数,task_count 是该档去重后的任务数(驳回 39 行落在 33 条任务上,
        # 拿行数当任务数就是错的);status 是进展行自己的审批码值,不是任务状态
        sql = f"""
SELECT p.status,
       CASE p.status WHEN 0 THEN '草稿' WHEN 1 THEN '待审核'
                     WHEN 2 THEN '驳回' WHEN 3 THEN '通过' ELSE '未知' END AS status_label,
       count(*)                  AS cnt,
       count(DISTINCT p.task_id) AS task_count
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
       {date_from}                                     AS earliest,
       {date_to}                                       AS latest,
       max(p.version_no)                               AS max_version
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

    # never_reported:存在性判定,不是 NULL 判定。列集合照抄参考实现:
    # task_id / task_name / board_name / project_group / has_group_history ——
    # has_group_history 说明"这条根本没往 task_progress 报过"里有多少是集团板的
    # (它们的成效写在另一张表),不点明就会被读成"漏报"。
    if in_flight_only:
        where.append("t.status IN (0, 1)")
    columns = ["t.id AS task_id", "t.task_name", "b.name AS board_name", "t.project_group"]
    if group_history_granted:
        columns.append(
            "EXISTS (SELECT 1 FROM task_group_progress_history h "
            "WHERE h.task_id = t.id AND h.is_published = 1) AS has_group_history"
        )
    sql = f"""
SELECT {",\n       ".join(columns)}
FROM task t
LEFT JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {where_sql}
  AND NOT EXISTS (
      SELECT 1 FROM task_progress p
      WHERE p.task_id = t.id AND p.is_published = 1
  )
ORDER BY t.id
LIMIT %s
"""
    return sql, (*params, int(limit))


def never_reported_totals(
    board_code: str | None = None,
    project_group: str | None = None,
) -> tuple[str, tuple]:
    """`never_reported` 的两个都成立的口径(55 与 9),必须一起给。

    55 = ``task_progress`` 里没有已发布行(含集团板全部 46 条,它们的成效不写这张表);
    9 = 两张表都没报过(等价于 ``latest_progress_time IS NULL``)。
    只给一个数,另一类问题就会被前一个数答掉(过火的否定句式牵连了 8 道题)。
    """
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
    sql = f"""
SELECT (SELECT count(*) FROM task t {board_join}
         WHERE {where_sql}
           AND NOT EXISTS (SELECT 1 FROM task_progress p
                           WHERE p.task_id = t.id AND p.is_published = 1))       AS total,
       (SELECT count(*) FROM task t {board_join}
         WHERE {where_sql} AND t.latest_progress_time IS NULL)                    AS both_empty
"""
    return sql, (*params, *params)


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

    列集合照抄参考查询(``task_id / task_name / year / current_year_goal /
    milestone_summary``):``task_no`` 与 ``goal_filled`` 是自造列,已去掉 ——
    "填没填"看 ``current_year_goal`` 是否为空即可,多一列反而让人以为有两种判据。
    """
    y, hint = adm.check_year(year)
    if hint:
        raise ValueError(hint)
    code, hint = adm.check_board_code(board_code)
    if hint:
        raise ValueError(hint)
    sql = f"""
SELECT t.id AS task_id, t.task_name,
       y.year, y.current_year_goal, y.milestone_summary
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


def freshness_task(
    as_of: str,
    task_id: int | None = None,
    task_name: str | None = None,
) -> tuple[str, tuple]:
    """**单个任务**的新鲜度 + 漂移核对(参考实现默认档的 ``task=`` 分支)。

    问"某个任务多久没报进展了"只能落到这一档:分档清单答的是"全库有几个桶",
    而这一档给的是那一行 —— 顺带把**漂移**一起核了。

    两条口径照抄参考实现:

    * ``days_behind`` = ``as_of`` 与 ``latest_progress_time`` 两个**日期**相减
      (``(a)::date - (b)::date``);写成 ``date - timestamp`` 会按当前时刻的时分秒截断,
      少算或多算一天。基准日是数据快照日,不是系统当天(``AS_OF_TRAP_NOTE``);
    * ``actual_latest_report`` 是**真实的**最新一期已发布进展时间,用来与任务行上那个
      去规范化列 ``latest_progress_time`` 对照 —— 两列不一致就是漂移。只给汇总列,
      调用方无法知道它是否可信。

    ``latest_progress_time`` 为 NULL(从未报过进展)时 ``days_behind`` 也是 NULL:
    那是"没有这个天数",不是 0 天。参考实现的 ``DATEDIFF`` 同样是 NULL,两边一致。
    """
    _check_as_of(as_of)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    if task_id is not None:
        where.append("t.id = %s")
        params.append(int(task_id))
    elif task_name:
        where.append("t.task_name = %s")
        params.append(task_name)
    sql = f"""
SELECT t.id                                                       AS task_id,
       t.task_name,
       {adm.normalize_ts_sql("t.latest_progress_time")}           AS latest_progress_time,
       {adm.normalize_ts_sql("max((p.report_time)::timestamp)")}  AS actual_latest_report,
       (%s::date - (t.latest_progress_time)::timestamp::date)::int AS days_behind
FROM task t
LEFT JOIN task_progress p ON p.task_id = t.id AND {adm.sql_published_progress("pg", "p")}
WHERE {"\n  AND ".join(where)}
GROUP BY t.id, t.task_name, t.latest_progress_time
"""
    return sql, (as_of, *params)


def freshness_task_probe(task_id: int | None = None, task_name: str | None = None) -> tuple[str, tuple]:
    """单任务档查不到时,**行本身是否存在**的判定(不带 R-01 闸门)。

    "查不到"有两种,给调用方的下一步动作完全不同:

    * 库里**没有**这一行 ⇒ 换 id / 更正名字;
    * 有这一行但**不过正式任务门**(已软删 / 未发布)⇒ 换任务,不是换问法 ——
      这一条尤其容易误读:真库里 ask 一个已软删任务会得到 0 行,和"这个任务不存在"
      长得一模一样,而它的提交单 / 审批动作其实照常能查。
    """
    where = []
    params: list[object] = []
    if task_id is not None:
        where.append("t.id = %s")
        params.append(int(task_id))
    else:
        where.append("t.task_name = %s")
        params.append(task_name)
    sql = f"""
SELECT t.id, t.task_name, t.is_deleted, t.workflow_status
FROM task t
WHERE {" AND ".join(where)}
LIMIT 1
"""
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
    "rows",
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

# 明细清单档的列集合:与参考实现 ``weekly_submission_query`` 的默认清单**同名同序**。
# 少一列或换一次序,同一个问题换数据源就得换字段读,而模型只会读它在演示源下学会的
# 那个键(这条纪律在 weekly_rank 的 total_count 上已经踩过一次)。
SUBMISSION_ROW_COLUMNS = (
    "id",
    "task_id",
    "task_name",
    "round_no",
    "status",
    "submission_kind",
    "reporter_id",
    "reporter_name",
    "signer_name",
    "need_sign",
    "submitted_at",
    "completed_at",
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

    明细清单档(``scope=rows`` / 默认档)是 ``submission_rows``;本函数只做聚合。

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
    if scope == "rows":
        raise ValueError("rows 是明细清单档,请用 submission_rows()")
    where_sql, board_join, params = _submission_where(board_code)
    inflight_list = ", ".join(f"'{s}'" for s in SUBMISSION_INFLIGHT)

    if scope == "by_kind":
        # 列名照抄参考查询:submission_count(不是 forms);排序也跟着它走
        sql = f"""
SELECT s.submission_kind, count(*) AS submission_count
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
GROUP BY s.submission_kind
ORDER BY submission_count DESC, s.submission_kind
"""
        return sql, tuple(params)

    if scope == "by_status":
        # 列名照抄参考查询:cnt(不是 forms)
        sql = f"""
SELECT s.status, count(*) AS cnt, count(DISTINCT s.task_id) AS tasks
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
GROUP BY s.status
ORDER BY cnt DESC, s.status
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
        # 列名照抄参考查询:inflight_submissions(不是 inflight_forms)
        sql = f"""
SELECT count(*)                    AS inflight_submissions,
       count(DISTINCT s.task_id)   AS tasks
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
  AND s.status IN ({inflight_list})
"""
        return sql, tuple(params)

    if scope == "inflight_by_board":
        # 列名照抄参考查询:submission_count;rejected 也是在途的一档,漏掉它各看板都少算
        sql = f"""
SELECT b.code AS board_code, s.status, count(*) AS submission_count
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {SUBMISSION_GATE}
  AND s.status IN ({inflight_list})
GROUP BY b.code, s.status
ORDER BY b.code, submission_count DESC, s.status
"""
        return sql, ()

    if scope == "inflight_by_kind":
        sql = f"""
SELECT s.status, s.submission_kind, count(*) AS submission_count
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

    # rounds_per_task:一行三列(均值 / 分子 / 分母),列名与参考查询一致:
    # avg_rounds / total_submissions / tasks —— 462 / 150 = 3.08 一次给全,
    # 调用方不必拿别处的任务数去除(除错了整条结论都错)
    sql = f"""
SELECT round(count(*)::numeric / NULLIF(count(DISTINCT s.task_id), 0), 2) AS avg_rounds,
       count(*)                  AS total_submissions,
       count(DISTINCT s.task_id) AS tasks
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
"""
    return sql, tuple(params)


def submission_scope_where(
    *,
    board_code: str | None = None,
    task_id: int | None = None,
    task_name: str | None = None,
    reporter: str | None = None,
    status: str | None = None,
    exclude_status: str | None = None,
) -> tuple[str, str, tuple]:
    """默认清单档的公共范围:``(where_sql, board_join, params)``。

    抽出来是为了让"清单本身"与它的三个配套聚合(命中总数 / 状态分档 / 值域)用**同一个**
    范围 —— 两边各拼一遍 WHERE,迟早会出现"清单 18 行、配套计数 21"这种自相矛盾的回包,
    而调用方只会相信自己看到的那个数(实测踩过一次:带 ``status=published`` 的清单 12 行,
    ``total_count`` 却是未过滤的 28)。
    """
    where_sql, board_join, params = _submission_where(board_code)
    conditions = [where_sql]
    if task_id is not None:
        conditions.append("s.task_id = %s")
        params.append(int(task_id))
    elif task_name:
        conditions.append("t.task_name = %s")
        params.append(task_name)
    if reporter:
        conditions.append("(btrim(coalesce(s.reporter_id, '')) = %s OR btrim(coalesce(s.reporter_name, '')) = %s)")
        params.extend([reporter, reporter])
    if status:
        conditions.append("s.status = %s")
        params.append(status)
    if exclude_status:
        conditions.append("s.status <> %s")
        params.append(exclude_status)
    return "\n  AND ".join(conditions), board_join, tuple(params)


def submission_rows(
    *,
    board_code: str | None = None,
    task_id: int | None = None,
    task_name: str | None = None,
    reporter: str | None = None,
    status: str | None = None,
    exclude_status: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """提交单**明细清单**(参考实现 ``weekly_submission_query`` 的默认档)。

    列集合照抄参考实现(``SUBMISSION_ROW_COLUMNS``:``id / task_id / task_name /
    round_no / status / submission_kind / reporter_id / reporter_name / signer_name /
    need_sign / submitted_at / completed_at``),一行一张单,``task_id + round_no`` 唯一。

    两处"照抄"要留意,它们是这条桥的全部价值:

    * **闸门只有软删**:提交单域只加 ``t.is_deleted = 0``。加任务发布门会把在途任务的单
      一起吞掉 —— 而"我提交过、还没发布的单"正是最常见的问法(参考实现 R3-05 就踩过);
    * **排序按提交时间倒序**(不是 task id 升序):参考实现的兜底清单按 ``task_id,
      round_no`` 排,那是"按任务翻账本"的顺序;这里的调用方问的通常是"最近提交了哪些"
      /"这一轮交了没",所以最近一轮必须在最前。``task_id`` / ``round_no`` 都在列里,
      要按任务读的调用方自己排一遍即可,反过来则做不到(第一页里根本没有最新的单)。
    """
    where_sql, board_join, params = submission_scope_where(
        board_code=board_code,
        task_id=task_id,
        task_name=task_name,
        reporter=reporter,
        status=status,
        exclude_status=exclude_status,
    )
    refs = {name: f"s.{name}" for name in SUBMISSION_ROW_COLUMNS}
    refs["task_name"] = "t.task_name"
    select = ", ".join(refs[name] for name in SUBMISSION_ROW_COLUMNS)
    sql = f"""
SELECT {select}
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
{board_join}
WHERE {where_sql}
ORDER BY s.submitted_at DESC NULLS LAST, s.id DESC
LIMIT %s
"""
    return sql, (*params, int(limit))


def submission_scoped_count_sql(where_sql: str, board_join: str) -> str:
    """清单配套聚合的 FROM/WHERE 片段(与 ``submission_rows`` 同一范围)。"""
    return f"FROM task_workflow_submission s JOIN task t ON t.id = s.task_id {board_join} WHERE {where_sql}"


def submission_status_mismatch(limit: int = 200) -> tuple[str, tuple]:
    """**一任务一行**:任务的 ``workflow_status`` 与它最新一轮提交单 ``status`` 不一致的清单。

    与默认明细清单是**两种形状**,不能互相代答:

    * 明细清单一行是**一张单**(``task_id + round_no`` 唯一),这一档一行是**一个任务**
      (取 ``round_no`` 最大的那张单),行数即不一致任务数 —— 拿明细去数会把同一任务的
      多轮重复计入;
    * 两列是**两套码值**(任务侧 ``published / pending_* / rejected``,提交单侧
      ``pending_fill / signing / pending_audit / pending_leader / published / rejected /
      cancelled``),字面相等只是比较方式,不代表语义同一。所以口径里必须写明"按字面
      不等判定",否则调用方会以为某个码值一定有对应关系。

    **闸门只有 ``t.is_deleted = 0``**:任务侧再加发布门,会把"已发布但最新单还在流程里"
    这批**恰恰是本题答案**的行滤掉(参考实现写死了这条注释)。
    """
    sql = f"""
SELECT t.id                AS task_id,
       t.task_name,
       t.workflow_status,
       s.round_no,
       s.status            AS latest_submission_status
FROM task t
JOIN task_workflow_submission s ON s.task_id = t.id
  AND s.round_no = (SELECT max(x.round_no) FROM task_workflow_submission x WHERE x.task_id = t.id)
WHERE {adm.sql_soft_delete("t")}
  AND t.workflow_status <> s.status
ORDER BY t.id
LIMIT %s
"""
    return sql, (int(limit),)


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
SELECT t.id AS task_id, t.task_name, g.year,
       g.current_year_goal, g.milestone_summary
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
SELECT a.id, t.id AS task_id, a.progress_id, a.workflow_submission_id,
       a.file_name, a.file_size, a.uploader_id,
       {adm.normalize_ts_sql("a.upload_time")} AS upload_time,
       t.task_no, t.task_name
FROM task_attachment a
JOIN task t ON t.id = a.task_id
{board_join}
WHERE {"\n  AND ".join(where)}
ORDER BY t.sort_order, t.id, a.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def _attachment_gate(
    board: str | None,
    task_id: int | None,
    task_name: str | None,
    include_informal: bool,
    *,
    alias: str = "a",
) -> tuple[str, str, list[object]]:
    """附件各档的公共 FROM/WHERE:``(from_sql, where_sql, params)``。

    三档闸门,选择的依据是**问句的对象**而不是省事:

    * ``task_id`` / ``task_name`` ⇒ 闸门就是"这个任务的附件",**刻意放开任务门** ——
      附件按 ``task_id`` 外键挂,任务不过 R-01 时它的附件依然存在,加了门会把真实条数
      静默答成 0(演示数据里任务 2 正是 ``workflow_status = 'rejected'``);
    * ``include_informal`` ⇒ 全表口径:只留附件行自己的软删闸门,并且**JOIN 要换成
      LEFT JOIN** —— 光把闸门改成恒真还差 3 行,因为 INNER JOIN 自己就会丢掉孤儿附件
      (演示数据 507 + 3 孤儿 = 510);
    * 默认 ⇒ 任务过正式门 + 附件行 ``is_deleted = 0``,两道都要。

    ``board`` 是任务侧的事,三种闸门下都要 JOIN 看板表(全表口径下它会顺手把
    没有任务行的孤儿附件滤掉 —— 那是"按看板问"应有的语义)。
    """
    where = [adm.sql_soft_delete(alias)]
    params: list[object] = []
    board_join = ""
    scoped = task_id is not None or bool(task_name)
    join_kind = "LEFT JOIN" if (include_informal and not scoped) else "JOIN"
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
    if task_id is not None:
        where.append(f"{alias}.task_id = %s")
        params.append(int(task_id))
    elif task_name:
        where.append("t.task_name = %s")
        params.append(task_name)
    elif not include_informal:
        where.append(adm.sql_task_admission("pg", "t"))
    if board:
        where.append("b.code = %s")
        params.append(board)
    from_sql = f"FROM task_attachment {alias}\n{join_kind} task t ON t.id = {alias}.task_id"
    if board_join:
        from_sql += f"\n{board_join}"
    return from_sql, "\n  AND ".join(where), params


# 关联去向的分档表达式。三处口径(deleted_by_link 也用同一套)必须一致,抽出来共用。
ATTACHMENT_LINK_CASE = (
    "CASE WHEN a.progress_id IS NOT NULL THEN '挂在进展'\n"
    "       WHEN a.workflow_submission_id IS NOT NULL THEN '挂在提交单'\n"
    "       ELSE '挂在任务本体' END"
)


def attachment_zero_total(board_code: str | None = None) -> tuple[str, tuple]:
    """``zero_attachment`` 的命中总数(一行一个数)。

    清单被 ``limit`` 截断时,"一共有多少个零附件任务"必须由服务端算完再回 ——
    拿本次行数当总数是这类档最常见的错(演示数据 22 个零附件任务 / 128 个正式任务,
    默认 ``limit`` 一截就只剩一页)。分母(全部正式任务)在明细行里已经带了同一个数。
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
SELECT count(*) AS total_count
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
  AND NOT EXISTS (SELECT 1 FROM task_attachment a
                  WHERE a.task_id = t.id AND {adm.sql_soft_delete("a")})
"""
    return sql, tuple(params)


def attachment_stats(
    board_code: str | None = None,
    scope: str = "summary",
    granted: bool = True,
    limit: int = 200,
    task_id: int | None = None,
    task_name: str | None = None,
    include_informal: bool = False,
    date_from: str | None = None,
) -> tuple[str, tuple]:
    """附件统计的**全部 13 档**(每档一种形状,不能互相代答)。

    ``file_size`` 是字节,原样报出(不要换算成 KB/MB,也不要写"约")——口径如此规定。

    各档问的是不同的东西:

    * ``summary`` 一行多列(计数 / 字节 / 均 KB / 挂载点 / 大类型 Top4);``by_ext`` 每扩展名
      一行(汇总行答不了"哪种文件最多",分档也答不了总量);
    * ``largest`` 是**清单**(一行一个文件,按字节倒序);``by_uploader`` 是**按人分档**
      (一行一个人,带条数与字节) —— 拿清单去数人会把同一人的多个文件重复计入;
      ``uploader_count`` 只回去重人数,是服务端算的一个数;
    * ``by_link`` / ``deleted_by_link`` 按挂载去向分档,**优先级 进展 > 提交单 > 任务本体**,
      一条附件只进一档(所以各档相加等于总数);
    * ``by_progress`` 按(任务, 期号)聚合,**只算已发布进展**(``p.is_published = 1``,
      与任务闸门是两道);``on_open_submission`` 只算挂在**在途提交单**上的附件
      (提交单状态是它自己的一套码值,``published`` 才叫已发布);
    * ``by_month`` 按 ``upload_time`` 的年月分档,``date_from`` 是**闭区间下界**;
    * ``zero_attachment`` 列**一个有效附件都没有的正式任务**(``NOT EXISTS`` 而非
      LEFT JOIN + HAVING:问的是存在性),分母是全部正式任务,不是本次行数;
    * ``deleted`` / ``orphan`` 问的是**表本身**,故**全表口径、不加任务闸门**:
      按任务过滤会少算(软删的行本来就挂在不该再被过滤的任务上)。

    闸门细节见 ``_attachment_gate``:``task_id`` / ``task_name`` 与 ``include_informal``
    都会**放开任务门**,而 ``zero_attachment`` / ``deleted`` / ``deleted_by_link`` / ``orphan``
    是跨任务口径,传 ``task`` 无意义(调用方显式报 ``task_not_applicable``,不静默忽略)。
    """
    hint = adm.require_optional_table("task_attachment", granted)
    if hint:
        raise PermissionError(hint)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)

    if scope == "deleted":
        # 全表口径:这是关于表的问题(参考实现在这里刻意不加任务闸门)
        sql = """
SELECT count(*) FILTER (WHERE a.is_deleted = 0) AS active,
       count(*) FILTER (WHERE a.is_deleted = 1) AS deleted,
       count(*)                                 AS total_rows,
       coalesce(sum(a.file_size) FILTER (WHERE a.is_deleted = 1), 0) AS deleted_bytes,
       round(coalesce(sum(a.file_size) FILTER (WHERE a.is_deleted = 1), 0)
             / 1024.0 / 1024.0, 1)              AS deleted_mb
FROM task_attachment a
"""
        return sql, ()

    if scope == "orphan":
        # 外键悬空的行:任务已不在库里、附件还在。列名与演示源一致(orphan_count),
        # 且**带 a.is_deleted = 0** —— 已软删的悬空行不算"要修的孤儿"。
        # 用 NOT EXISTS 而不是 JOIN:JOIN 会把孤儿行整批丢掉,结果恒等于 0。
        sql = """
SELECT count(*) AS orphan_count
FROM task_attachment a
WHERE a.is_deleted = 0
  AND NOT EXISTS (SELECT 1 FROM task t WHERE t.id = a.task_id)
"""
        return sql, ()

    if scope == "deleted_by_link":
        # 软删审计的另一半:按挂载去向看**已软删**的那些。全表口径(不加任务闸门)——
        # 软删的附件本就挂在不该再被过滤的任务上,加门会少算。
        sql = f"""
SELECT {ATTACHMENT_LINK_CASE} AS link_type,
       count(*) AS n,
       round(sum(a.file_size) / 1024.0 / 1024.0, 1) AS total_mb
FROM task_attachment a
WHERE a.is_deleted = 1
GROUP BY link_type
ORDER BY n DESC, link_type
LIMIT %s
"""
        return sql, (int(limit),)

    if scope == "zero_attachment":
        # 「哪些任务一个附件都没有」:NOT EXISTS 而非 LEFT JOIN + HAVING COUNT = 0(问的是存在性)。
        # 分母(全部正式任务)由单独一条查询给出 —— 占比要用它,不能拿本次行数当分母
        # (演示数据:22 / 128)。行里也带上同一个数,便于逐行核对口径。
        where = [adm.sql_task_admission("pg", "t")]
        params: list[object] = []
        board_join = ""
        if board:
            board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
            where.append("b.code = %s")
            params.append(board)
        sql = f"""
SELECT t.id AS task_id, t.task_name,
       (SELECT count(*) FROM task t2
         WHERE {adm.sql_task_admission("pg", "t2")}) AS total_formal_tasks
FROM task t
{board_join}
WHERE {"\n  AND ".join(where)}
  AND NOT EXISTS (SELECT 1 FROM task_attachment a
                  WHERE a.task_id = t.id AND {adm.sql_soft_delete("a")})
ORDER BY t.id
LIMIT %s
"""
        return sql, (*params, int(limit))

    if scope == "by_progress":
        # 「哪些已发布进展带了附件」:闸门在 progress 行上(p.is_published = 1),与任务闸门两道。
        # 按 (任务, 期号) 聚合,同一任务可出现多期。
        where = [adm.sql_task_admission("pg", "t"), adm.sql_soft_delete("a")]
        params = []
        if task_id is not None:
            where.append("a.task_id = %s")
            params.append(int(task_id))
        sql = f"""
SELECT t.task_name, p.version_no, count(*) AS attachment_count
FROM task_attachment a
JOIN task_progress p ON p.id = a.progress_id AND {adm.sql_published_progress("pg", "p")}
JOIN task t ON t.id = a.task_id
WHERE {"\n  AND ".join(where)}
GROUP BY t.id, t.task_name, p.version_no
ORDER BY attachment_count DESC, t.id, p.version_no
LIMIT %s
"""
        return sql, (*params, int(limit))

    if scope == "on_open_submission":
        # 「在途」= 提交单状态不是 published。提交单状态另有码值,
        # 不能拿任务的 workflow_status 来判。
        from_sql, where_sql, params = _attachment_gate(board, task_id, task_name, include_informal)
        sql = f"""
SELECT count(*) AS attachment_count
{from_sql}
JOIN task_workflow_submission s ON s.id = a.workflow_submission_id
WHERE {where_sql}
  AND s.status <> 'published'
"""
        return sql, tuple(params)

    if scope == "by_month":
        from_sql, where_sql, params = _attachment_gate(board, task_id, task_name, include_informal)
        extra = ""
        if date_from:
            token = date_from.strip()
            if not _DATE_RE.match(token):
                raise ValueError(f"date_from 需为 YYYY-MM-DD:{date_from!r}")
            extra = "\n  AND a.upload_time >= %s"
            params.append(token)
        sql = f"""
SELECT to_char((a.upload_time)::timestamp, 'YYYY-MM') AS ym,
       count(*) AS n,
       round(sum(a.file_size) / 1024.0 / 1024.0, 1) AS total_mb
{from_sql}
WHERE {where_sql}{extra}
GROUP BY ym
ORDER BY ym
LIMIT %s
"""
        return sql, (*params, int(limit))

    if scope == "by_link":
        from_sql, where_sql, params = _attachment_gate(board, task_id, task_name, include_informal)
        sql = f"""
SELECT {ATTACHMENT_LINK_CASE} AS link_type, count(*) AS n
{from_sql}
WHERE {where_sql}
GROUP BY link_type
ORDER BY n DESC, link_type
LIMIT %s
"""
        return sql, (*params, int(limit))

    if scope == "uploader_count":
        from_sql, where_sql, params = _attachment_gate(board, task_id, task_name, include_informal)
        sql = f"""
SELECT count(DISTINCT a.uploader_id) AS uploader_count
{from_sql}
WHERE {where_sql}
"""
        return sql, tuple(params)

    if scope in ("largest", "by_uploader"):
        from_sql, where_sql, params = _attachment_gate(board, task_id, task_name, include_informal)
        if scope == "largest":
            sql = f"""
SELECT a.file_name, a.file_size,
       round(a.file_size / 1024.0 / 1024.0, 2) AS size_mb, t.task_name
{from_sql}
WHERE {where_sql}
ORDER BY a.file_size DESC, a.id
LIMIT %s
"""
        else:
            sql = f"""
SELECT a.uploader_id,
       count(*)                     AS upload_count,
       sum(a.file_size)             AS total_bytes,
       round(sum(a.file_size) / 1024.0 / 1024.0, 1) AS total_mb
{from_sql}
WHERE {where_sql}
GROUP BY a.uploader_id
ORDER BY upload_count DESC, a.uploader_id
LIMIT %s
"""
        return sql, (*params, int(limit))

    where = [adm.sql_task_admission("pg", "t"), adm.sql_soft_delete("a")]
    params = []
    board_join = ""
    if board:
        board_join = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
    ext = "lower(substring(a.file_name from '\\.([^.]+)$'))"
    if scope == "by_ext":
        # 每档一行,列名与排序照抄参考查询(n 降序、并列按 ext)
        select = """ext, count(*) AS n,
       sum(file_size)                 AS total_bytes,
       round(sum(file_size) / 1024.0 / 1024.0, 1) AS total_mb"""
        tail = "FROM files\nWHERE ext IS NOT NULL\nGROUP BY ext\nORDER BY n DESC, ext\nLIMIT %s"
        params.append(int(limit))
    else:
        select = """(SELECT count(*) FROM files)                                  AS attachment_count,
       (SELECT sum(file_size) FROM files)                            AS total_bytes,
       (SELECT round(sum(file_size) / 1024.0 / 1024.0, 1) FROM files) AS total_mb,
       (SELECT round(avg(file_size) / 1024.0, 1) FROM files)         AS avg_kb,
       (SELECT count(DISTINCT task_id) FROM files)                   AS tasks_with_attachment,
       (SELECT count(DISTINCT uploader_id) FROM files)               AS uploader_count,
       (SELECT count(*) FROM files WHERE progress_id IS NOT NULL)                                AS linked_to_progress,
       (SELECT count(*) FROM files WHERE workflow_submission_id IS NOT NULL) AS linked_to_submission,
       (SELECT count(*) FROM files
         WHERE progress_id IS NULL AND workflow_submission_id IS NULL)      AS linked_to_task,
       (SELECT count(*) FROM files WHERE ext = 'pptx')               AS ext_pptx,
       (SELECT count(*) FROM files WHERE ext = 'xlsx')               AS ext_xlsx,
       (SELECT count(*) FROM files WHERE ext = 'pdf')                AS ext_pdf,
       (SELECT count(*) FROM files WHERE ext = 'docx')               AS ext_docx"""
        tail = ""
    sql = f"""
WITH files AS (
    SELECT a.*, {ext} AS ext
    FROM task_attachment a
    JOIN task t ON t.id = a.task_id
    {board_join}
    WHERE {"\n  AND ".join(where)}
)
SELECT {select}
{tail}
"""
    return sql, tuple(params)


# ---- batch 6: 责任人角色 / 集团板明细 / 行数体检 ------------------------------

# ChatBI 契约覆盖的 12 张表:体检与字段字典都只认这一组。
# 不按 ``public`` 全 schema 列举 —— 正式库的 public 下还有别的表,列进来就是噪音,
# 而且会把字段字典顶到行数上限之外。
CHATBI_TABLES = (
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

# 禁止外泄的字段(与 ``_store.BLOCKED_FIELDS`` 同义):字段字典里也不出现,
# 这样"这个清单就是可对外引用的全部字段"这句话才成立。
BLOCKED_COLUMNS = ("storage_path", "payload")


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
    parts = []
    for table in CHATBI_TABLES:
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
    granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """审批动作流水 ``task_workflow_action``(可选表)。

    演示库里该表 1,613 行,带 ``t.is_deleted = 0`` 后 **1,578 行** —— 与清单封顶 200 行
    差一个量级,所以"多少个"这类问题必须走服务端聚合,不能翻明细数。

    口径要点:

    * ``opinion``(审批意见)**按权限展示**(R-04/R-14):列**始终在**,
      无权限时由调用方把值打码成「[按权限不展示]」—— 与参考实现同一套
      ``_scrub`` 语义:列藏掉了,调用方就分不清"这条没有意见"与"我没权限看";
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

    if scope == "by_node_action":
        # 列名照抄参考查询:action_count(不是 actions)
        sql = f"""
SELECT a.node_type, a.action, count(*) AS action_count
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {where_sql}
GROUP BY a.node_type, a.action
ORDER BY a.node_type, a.action
"""
        return sql, tuple(params)

    if scope == "actions_per_task":
        # 一行三列:avg_actions / total_actions / tasks(1,578 / 150 = 10.52)
        sql = f"""
SELECT round(count(*)::numeric / NULLIF(count(DISTINCT a.task_id), 0), 2) AS avg_actions,
       count(*)                  AS total_actions,
       count(DISTINCT a.task_id) AS tasks
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {where_sql}
"""
        return sql, tuple(params)

    if scope == "by_action":
        # 参考实现没有这一档(它的分面是 node x action);这是本移植**新增**的一档,
        # 答"各动作各有多少条",列名与 node x action 那档保持一致:action_count
        sql = f"""
SELECT a.action, count(*) AS action_count
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {where_sql}
GROUP BY a.action
ORDER BY action_count DESC, a.action
"""
        return sql, tuple(params)

    # recent / 默认:按动作自身时间倒序(默认清单按 task id 排序,答不了"最近谁被驳回")
    # 列名与 JOIN 都照抄参考实现:带任务名 + 该单填报人(INNER JOIN 提交单),
    # opinion 夹在操作人与时间之间、**始终在列里**(无权限时由调用方打码成
    # 「[按权限不展示]」,而不是把列藏掉 —— 列没了,调用方分不清"没有意见"
    # 与"没权限看意见"),时间列叫 acted_at。
    sql = f"""
SELECT a.id, a.task_id, t.task_name, s.round_no, s.reporter_name, s.status,
       a.node_type, a.action, a.operator_name, a.opinion,
       {adm.normalize_ts_sql("a.created_at")} AS acted_at
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
JOIN task_workflow_submission s ON s.id = a.submission_id
{joins}
WHERE {where_sql}
ORDER BY a.created_at DESC, t.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


# ---- batch 15: 两个默认清单档(「不传 scope」时走的那一档)---------------------
#
# 这两档此前是**唯一**没迁的分支:31 个工具虽然都接了线,但 ``weekly_submission_query()``
# 与 ``weekly_workflow_query()`` 在**空参数**下返回 ``None`` 回落演示路径 —— 生产环境没有
# 那台演示 MySQL,于是 agent 一用默认参数就拿到 ``store_unreachable``,而错误信息指向一个
# 与国数无关的库(容易被读成"数据库挂了")。补这两档是为了让"默认参数"也是一条正式桥。

# 审批流水的默认清单列集合:与参考实现 ``weekly_workflow_query`` 的兜底清单**同名同序**
# (``a.submission_id`` 与 ``a.created_at`` 都在其中;``recent`` 档另走一条形状)。
WORKFLOW_ROW_COLUMNS = (
    "id",
    "submission_id",
    "task_id",
    "round_no",
    "node_type",
    "action",
    "operator_name",
    "opinion",
    "created_at",
)


def workflow_action_rows(
    *,
    board_code: str | None = None,
    task_id: int | None = None,
    task_name: str | None = None,
    action: str | None = None,
    granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """审批动作流水的**默认清单**(一行一条动作,按任务与动作时间排序)。

    与 ``workflow_actions(scope="recent")`` 是两道不同的桥,差异必须保持:

    * **排序**:本档按 ``a.task_id, a.created_at, a.id`` —— 与参考实现的兜底清单一致
      (那是"某个任务的审批轨迹",按任务聚拢才好读);``recent`` 按动作自身时间倒序,
      答的是"最近谁被驳回"。**不能按 round_no 排**:轮次号不等于时间序,任务 3 的第 3 轮
      实际早于第 2 轮,按 round_no 排会把轨迹读反(参考实现里写死了这条注释)。
    * **JOIN**:本档对 ``task_workflow_submission`` 用 ``LEFT JOIN``、只取 ``round_no`` ——
      动作可能挂在已不在提交单清单里的单上,``INNER JOIN`` 会静默少行;``recent`` 要
      ``reporter_name`` 与 ``s.status``,缺了这两列那一档就没有意义,故沿用 ``INNER JOIN``。
    * **列**:``submission_id`` 与 ``created_at`` 只在明细清单里出现(见
      ``WORKFLOW_ROW_COLUMNS``),``recent`` 的时间列叫 ``acted_at``。
    """
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
    elif task_name:
        where.append("t.task_name = %s")
        params.append(task_name)
    if action:
        where.append("a.action = %s")
        params.append(action)
    refs = {
        "id": "a.id",
        "submission_id": "a.submission_id",
        "task_id": "a.task_id",
        "round_no": "s.round_no",
        "node_type": "a.node_type",
        "action": "a.action",
        "operator_name": "a.operator_name",
        "opinion": "a.opinion",
        "created_at": "a.created_at",
    }
    select = ", ".join(refs[name] for name in WORKFLOW_ROW_COLUMNS)
    sql = f"""
SELECT {select}
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
LEFT JOIN task_workflow_submission s ON s.id = a.submission_id
{joins}
WHERE {"\n  AND ".join(where)}
ORDER BY a.task_id, a.created_at, a.id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


def workflow_actions_by_task(
    *,
    board_code: str | None = None,
    task_id: int | None = None,
    action: str | None = None,
    granted: bool = True,
    limit: int = 200,
) -> tuple[str, tuple]:
    """按任务聚合的审批动作数(**一任务一行**,不是动作流水)。

    问"哪些任务被驳回过、各有几次"问的是**任务集合与次数**;流水里同一任务会出现多次
    (真库 91 条动作挂在 23 个任务上,最多的那个任务 19 条),拿流水行数报会把**次数
    当成任务数**。所以这一档必须服务端聚合。

    列集合照抄参考实现:有 ``board=`` 时 **JOIN 任务表并回 ``task_name``**(没有任务名的
    榜单答不了"哪些任务");不带看板时只回 ``task_id`` —— 这是参考实现的形状,不统一,
    因为带看板的那一问几乎总要念任务名,而不带看板时 id 已足够定位。
    """
    hint = adm.require_optional_table("task_workflow_action", granted)
    if hint:
        raise PermissionError(hint)
    board, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_soft_delete("t")]
    params: list[object] = []
    joins = ""
    name_column = ""
    if board:
        joins = f"JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete('b')}"
        where.append("b.code = %s")
        params.append(board)
        name_column = ",\n       t.task_name"
    if task_id is not None:
        where.append("a.task_id = %s")
        params.append(int(task_id))
    if action:
        where.append("a.action = %s")
        params.append(action)
    sql = f"""
SELECT a.task_id{name_column},
       count(*) AS action_count
FROM task_workflow_action a
JOIN task t ON t.id = a.task_id
{joins}
WHERE {"\n  AND ".join(where)}
GROUP BY a.task_id{", t.task_name" if board else ""}
ORDER BY action_count DESC, a.task_id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)

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
    "id_variants",
    "id_longest",
    "reporters",
    "reporter_count",
    "reviewers",
    "self_review",
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
    * ``id_variants`` 查"同一个人挂着不同标识",**0 行就是答案**(不存在这种人),
      不要读成"没查到";它需要姓名列与标识列成对存在,故不支持 ``role=owner``;
    * ``id_longest`` 一行一个**去重后的标识**(不是一行一个任务),按字符长度倒序,
      并列个数由 ``person_id_ties`` 给出;
    * ``reporters`` 的口径是"任务闸门 + ``p.is_published = 1``"两道闸门,
      填报人在 ``task_progress`` 上而不在 ``task`` 上;``reporter_count`` 是**同一批行**
      的去重人数(一个数,不是清单)—— 拿 ``reporters`` 的行数顶替会把"被 top 截断过的
      前 N 人"当成总人数;
    * ``reviewers`` / ``self_review`` **刻意不加** ``p.is_published``:审过但还没发布的
      进展同样是审过的,加了发布闸门会把"待审已审"整批滤掉。
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
        # 列集合照抄参考查询:除均值外还要 max_tasks / min_tasks —— 只有均值时分不清
        # "人人 8 条"与"有人 30 条有人 1 条"。
        # 一次聚合搞定:先按人算条数,再对这张小表求和/计数,谓词只出现一次
        # (参考实现为了让 MySQL 命名参数可重复,写了同一段谓词两遍;位置参数下
        # 重复一遍就要重复一遍参数,容易错位)。
        sql = f"""
WITH per_person AS (
    SELECT t.{column} AS person, count(*) AS task_count
    FROM task t
    {joins}
    WHERE {named}
    GROUP BY t.{column}
)
SELECT sum(task_count)                                          AS tasks,
       count(*)                                                 AS people,
       round(sum(task_count)::numeric / NULLIF(count(*), 0), 2) AS avg_tasks_per_person,
       max(task_count)                                          AS max_tasks,
       min(task_count)                                          AS min_tasks
FROM per_person
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

    # reporters / reporter_count:任务闸门 + 进展行发布闸门,两道都要
    if scope == "reporters":
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

    if scope == "reporter_count":
        # 与 ``reporters`` **同一批行**的去重计数:口径必须逐字一致(两道闸门一样),
        # 否则"有人 63 轮"与"一共几个填报人"会来自两个不同的分母。
        # 它是**一个数**而不是清单 —— 让调用方拿 reporters 的行数顶替,
        # 就会把"被 top 截断过的前 N 人"当成总人数(参考实现专门写了这条注释)。
        sql = f"""
SELECT count(DISTINCT p.reporter_id) AS reporter_count
FROM task_progress p
JOIN task t ON t.id = p.task_id
{joins}
WHERE {gate_sql}
  AND {adm.sql_published_progress("pg", "p")}
"""
        return sql, tuple(params)

    if scope == "id_variants":
        # "同一个人在不同任务里会不会挂着不同格式的标识"。**空集就是答案**:
        # 返回 0 行说明该口径下不存在这种人,不能反过来说"会出现"。
        # 列集合照抄参考查询:person / id_variants / ids(ids 是逗号连接的标识清单)。
        name_column = _person_id_columns(role)
        sql = f"""
SELECT t.{name_column[0]} AS person,
       count(DISTINCT t.{name_column[1]}) AS id_variants,
       string_agg(DISTINCT t.{name_column[1]}, ',' ORDER BY t.{name_column[1]}) AS ids
FROM task t
{joins}
WHERE {gate_sql}
  AND t.{name_column[0]} IS NOT NULL
  AND t.{name_column[1]} IS NOT NULL
GROUP BY t.{name_column[0]}
HAVING count(DISTINCT t.{name_column[1]}) > 1
ORDER BY id_variants DESC, person
LIMIT %s
"""
        return sql, (*params, int(top))

    if scope in ("reviewers", "self_review"):
        # 审核人在 task_progress 上。**审核口径刻意不加 p.is_published**:
        # 审过但还没发布的进展同样是审过的 —— 加了发布闸门会把"待审已审"整批滤掉。
        hist = "FROM task_progress p\nJOIN task t ON t.id = p.task_id"
        if scope == "reviewers":
            sql = f"""
SELECT p.reviewer_id, count(*) AS reviewed
{hist}
{joins}
WHERE {gate_sql}
  AND p.reviewer_id IS NOT NULL
GROUP BY p.reviewer_id
ORDER BY reviewed DESC, p.reviewer_id
LIMIT %s
"""
            return sql, (*params, int(top))
        sql = f"""
SELECT t.task_name, p.version_no, p.reporter_id, p.reviewer_id
{hist}
{joins}
WHERE {gate_sql}
  AND p.reviewer_id IS NOT NULL
  AND p.reporter_id = p.reviewer_id
ORDER BY t.id, p.version_no
LIMIT %s
"""
        return sql, (*params, int(top))

    # id_longest:问的是**标识**而不是任务 —— 同一个标识挂 3 个任务只算一个标识。
    # 不去重会返回同一个标识重复多行,模型会把"最长的是哪一个"答成一串重复项。
    longest = f"""
SELECT t.owner_user_id, length(t.owner_user_id) AS id_length, count(*) AS task_count
FROM task t
{joins}
WHERE {gate_sql}
  AND t.owner_user_id IS NOT NULL
  AND t.owner_user_id <> ''
GROUP BY t.owner_user_id
ORDER BY id_length DESC, t.owner_user_id
LIMIT %s
"""
    return longest, (*params, int(top))


def person_id_ties(board_code: str | None = None) -> tuple[str, tuple]:
    """``id_longest`` 的并列自检:首行那个标识长度上共有几个标识。

    问句"最长的是哪一个"是**单数**,而真库里等长标识常常不止一个 —— 不给这个数,
    模型要么只报一个(漏掉并列),要么把并列的都塞进答案行(改写了行数)。
    与 ``person_ties`` 同一个理由,只是度量从"任务数"换成"标识长度"。
    """
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
    sql = f"""
WITH ids AS (
    SELECT t.owner_user_id, length(t.owner_user_id) AS id_length
    FROM task t
    {joins}
    WHERE {"\n  AND ".join(where)}
      AND t.owner_user_id IS NOT NULL
      AND t.owner_user_id <> ''
    GROUP BY t.owner_user_id
)
SELECT max(id_length)                                          AS max_id_length,
       count(*) FILTER (WHERE id_length = (SELECT max(id_length) FROM ids)) AS tied_at_top
FROM ids
"""
    return sql, tuple(params)


def _person_id_columns(role: str) -> tuple[str, str]:
    """``id_variants`` 用的 (姓名列, 标识列) —— 两者必须来自同一个角色。

    只有牵头领导与项目负责人有姓名列;主责人只有工号列,没有可比的"同名"列,
    故这一档不支持 ``role=owner``(显式报错,不去猜一个替代列)。
    """
    pairs = {
        "lead_owner": ("lead_owner_name", "lead_owner_id"),
        "project_owner": ("project_owner_name", "project_owner_id"),
    }
    if role not in pairs:
        raise ValueError(
            f"id_variants 需要「姓名列 + 标识列」成对存在,不支持 role={role};"
            f"支持 {', '.join(pairs)}"
        )
    return pairs[role]


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
    # 列名照抄参考查询:**按角色给各自的原名**(lead_owner_names / project_owner_names),
    # 不统一改叫 owner_names —— 两个角色是两列,同名会让人以为可以互换;
    # owner_count 数的是**这行的负责人个数**(按 id 列里的分隔符个数 + 1),
    # 排序也跟着它走(并列按 task_id 升序)。演示实现数的是半角逗号,
    # 这里按同一套多值分隔符(顿号与逗号都算)数,否则顿号写的行会被算成 1 个。
    owner_count = "cardinality(" + _multivalue_array("g." + id_column) + ")"
    sql = f"""
SELECT g.task_id, t.task_name,
       g.{name_column},
       g.{id_column},
       {owner_count} AS owner_count
FROM task_group_detail g
JOIN task t ON t.id = g.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {"\n  AND ".join(where)}
ORDER BY owner_count DESC, g.task_id
LIMIT %s
"""
    params.append(int(limit))
    return sql, tuple(params)


# ---- batch 12: 里程碑统计(milestone_stats 的 6 个 scope x 10 个维度)----------
#
# 三条必须照抄的判据(第 25 轮侦察,结论见接入说明第 4 节):
#
#   1. ``m.status`` 是 **0/1 两值码**(1 已完成 / 0 未完成),「已完成」只认
#      ``status = 1``,不做文本匹配,也不拿 ``task.status`` 顶替;
#   2. ``fully_deleted`` 用 **NOT EXISTS 未删里程碑**判,不是「有软删行」——
#      有删的任务 23 条,删干净的只有 3 条,混起来差一个量级;
#   3. ``per_task`` 必须用 **LEFT JOIN 保留零里程碑任务**(inner join 会把
#      1 条都没配的任务整行抹掉,而覆盖率的分母正是它们),并给 ``top_tie_count``
#      —— 榜首是 23 路并列在 6 个,只回榜单时模型会把并列读成 23 个独立答案。
#
# 另有两个**像但不是一个轴**的维度,取值集合都不一样,不可互换:
# ``group_name`` 是里程碑行自己的承担组短标签(区域组/安全组… 6 种),
# ``project_group`` 是任务上的项目组(关键技术攻关组/算力网络组… 11 种)。

MILESTONE_STATS_SCOPES = (
    "summary",
    "by_dimension",
    "deleted",
    "fully_deleted",
    "per_task",
    "mismatch",
)

# 维度 -> (分组表达式, 中文标签)。表达式只能来自这张白名单:它直接进 SQL。
MILESTONE_DIMENSIONS: dict[str, tuple[str, str]] = {
    "year": ("m.year", "里程碑年度"),
    "category": ("m.category", "里程碑类别"),
    "group_name": ("m.group_name", "里程碑承担组"),
    "status": ("m.status", "里程碑完成状态"),
    "task_status": ("t.status", "任务状态"),
    # 任务分类树的一级:任务的分类只到二级,一级要再往上跳一层 parent_id。
    # 与 ``category``(里程碑自己的类别文本)不是一个维度 —— 按 m.category 分组
    # 首行是「国家任务 58.9%」,按一级分类分组首行是「改革与治理 67.5%」。
    "primary_category": ("pc.name", "任务一级分类"),
    "project_group": ("coalesce(nullif(btrim(t.project_group), ''), '(未填)')", "任务项目组"),
    "reporter_id": ("m.reporter_id", "里程碑填报人"),
    "owner_id": ("m.owner_id", "里程碑责任人"),
    "board": ("bd.name", "看板"),
}

# 只有这两个维度要 JOIN 别的表,其余全在 m / t 上。JOIN 各自带 is_deleted = 0。
MILESTONE_DIMENSION_JOINS: dict[str, str] = {
    "primary_category": (
        "JOIN task_category c  ON c.id = t.category_id AND c.is_deleted = 0\n"
        "JOIN task_category pc ON pc.id = c.parent_id  AND pc.is_deleted = 0"
    ),
    "board": "JOIN task_board bd ON bd.id = t.board_id AND bd.is_deleted = 0",
}

# 问「哪个维度完成率最高 / 最低」时按比率排序,首行即答案;其余按条数排序。
# 让模型自己在结果里挑最高,样本小的分类会被排到末页而看不见 —— 排错端等于把末位当第一。
MILESTONE_RATE_ORDERED = frozenset({"primary_category", "project_group"})

MILESTONE_MISMATCH_KINDS = ("task_done_milestones_open", "milestones_done_task_open")

# 里程碑完成数表达式。PG 没有 ``sum(bool)``(把 MySQL 的 ``SUM(m.status = 1)``
# 直接搬过来会报 ``function sum(boolean) does not exist``),一律写 FILTER。
_MS_FINISHED = "count(*) FILTER (WHERE m.status = 1)"
_MS_UNFINISHED = "count(*) FILTER (WHERE m.status = 0)"


def _milestone_year_category(year: int | str | None, category: str | None) -> tuple[list[str], list[object]]:
    """year / category 两个可选收窄条件。"""
    where: list[str] = []
    params: list[object] = []
    if year not in (None, "", 0, "0"):
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
        where.append("m.year = %s")
        params.append(y)
    if category and category.strip():
        where.append("m.category = %s")
        params.append(category.strip())
    return where, params


def _milestone_active(
    year: int | str | None = None,
    category: str | None = None,
) -> tuple[str, tuple]:
    """``active`` 条件:任务闸门 + 里程碑行未删(+ 可选年度/类别)。"""
    where = [adm.sql_task_admission("pg", "t"), adm.sql_soft_delete("m")]
    extra, params = _milestone_year_category(year, category)
    where.extend(extra)
    return "\n  AND ".join(where), tuple(params)


def _milestone_per_task_join(
    year: int | str | None = None,
    category: str | None = None,
    *,
    milestone_alias: str = "m",
    task_alias: str = "t",
) -> tuple[str, tuple]:
    """``per_task`` 的 LEFT JOIN。

    年度/类别条件**必须挂在 ON 上,不能进 WHERE**:进 WHERE 会把「没有该年度里程碑」
    的任务整行删掉,而那恰好就是要数的部分,分母同时从 128 缩到 112,覆盖率永远算成
    100%。挂 ON 上则保留它们:128 项里 16 项没配、112 项配了,即 87.5%。
    """
    extra, params = _milestone_year_category(year, category)
    clause = (
        f"LEFT JOIN task_milestone {milestone_alias} ON {milestone_alias}.task_id = {task_alias}.id"
        f" AND {adm.sql_soft_delete(milestone_alias)}"
    )
    for item in extra:
        clause += " AND " + item.replace("m.", f"{milestone_alias}.")
    return clause, tuple(params)


def milestone_stats(
    scope: str,
    by: str = "category",
    year: int | str | None = None,
    category: str | None = None,
    min_total: int = 0,
    kind: str = "task_done_milestones_open",
    limit: int = 200,
) -> tuple[str, tuple]:
    """里程碑统计的 5 个"一次查询"scope(``per_task`` 走 ``milestone_per_task_*``)。

    ``deleted`` / ``fully_deleted`` 是全表口径,**刻意不套任务闸门**:它们问的是
    "表里有多少行被软删",按任务过滤会少算。全表口径下 year/category 也不参与
    —— 与其余 scope 正好相反,别顺手统一。
    """
    if scope not in MILESTONE_STATS_SCOPES:
        raise ValueError(f"未知 scope:{scope};可选 {', '.join(MILESTONE_STATS_SCOPES)}")
    bounded = max(1, int(limit))

    if scope == "deleted":
        sql = """
SELECT count(*) FILTER (WHERE m.is_deleted = 0) AS active,
       count(*) FILTER (WHERE m.is_deleted = 1) AS deleted,
       count(*)                                 AS total_rows
FROM task_milestone m
"""
        return sql, ()

    if scope == "fully_deleted":
        sql = f"""
SELECT t.id AS task_id, t.task_name, count(*) AS deleted_milestones
FROM task_milestone m
JOIN task t ON t.id = m.task_id
WHERE {adm.sql_task_admission("pg", "t")}
  AND m.is_deleted = 1
  AND NOT EXISTS (SELECT 1 FROM task_milestone m2
                  WHERE m2.task_id = t.id AND m2.is_deleted = 0)
GROUP BY t.id, t.task_name
ORDER BY t.id
LIMIT %s
"""
        return sql, (bounded,)

    base_from = "FROM task_milestone m\nJOIN task t ON t.id = m.task_id"

    if scope == "summary":
        active, params = _milestone_active(year, category)
        sql = f"""
SELECT count(*)       AS total,
       {_MS_FINISHED}   AS finished,
       {_MS_UNFINISHED} AS unfinished,
       round({_MS_FINISHED}::numeric / NULLIF(count(*), 0) * 100, 1) AS finish_rate_pct
{base_from}
WHERE {active}
"""
        return sql, params

    if scope == "by_dimension":
        dimension = (by or "category").strip().lower()
        if dimension not in MILESTONE_DIMENSIONS:
            raise ValueError(f"不支持的维度:{by};支持 {', '.join(sorted(MILESTONE_DIMENSIONS))}")
        column, _label = MILESTONE_DIMENSIONS[dimension]
        joins = MILESTONE_DIMENSION_JOINS.get(dimension, "")
        active, params = _milestone_active(year, category)
        having = ""
        if int(min_total or 0) > 0:
            having = "\nHAVING count(*) >= %s"
            params = (*params, max(1, int(min_total)))
        order = "finish_rate_pct DESC, bucket" if dimension in MILESTONE_RATE_ORDERED else "total DESC, bucket"
        sql = f"""
SELECT {column} AS bucket,
       count(*)  AS total,
       {_MS_FINISHED} AS finished,
       round({_MS_FINISHED}::numeric / NULLIF(count(*), 0) * 100, 1) AS finish_rate_pct
{base_from}
{joins}
WHERE {active}
GROUP BY {column}{having}
ORDER BY {order}
LIMIT %s
"""
        return sql, (*params, bounded)

    # mismatch:任务状态与里程碑状态互相矛盾的两种比法。
    mismatch = (kind or "task_done_milestones_open").strip().lower()
    if mismatch not in MILESTONE_MISMATCH_KINDS:
        raise ValueError(f"不支持的比对:{kind};支持 {', '.join(MILESTONE_MISMATCH_KINDS)}")
    if mismatch == "task_done_milestones_open":
        extra, having = "t.status = 2", f"{_MS_FINISHED} < count(*)"
    else:
        extra, having = "t.status = 1", f"{_MS_FINISHED} = count(*)"
    active, params = _milestone_active(year, category)
    sql = f"""
SELECT t.id AS task_id, t.task_name, t.status AS task_status,
       count(*)       AS milestones,
       {_MS_FINISHED} AS finished_milestones
{base_from}
WHERE {active}
  AND {extra}
GROUP BY t.id, t.task_name, t.status
HAVING {having}
ORDER BY t.id
LIMIT %s
"""
    return sql, (*params, bounded)


def milestone_per_task_summary(
    year: int | str | None = None,
    category: str | None = None,
) -> tuple[str, tuple]:
    """``per_task`` 的总览:分母是**全部正式任务**(含零里程碑任务)。"""
    join, join_params = _milestone_per_task_join(year, category)
    sql = f"""
SELECT count(DISTINCT t.id) AS tasks,
       count(m.id)          AS milestones,
       round(count(m.id)::numeric / NULLIF(count(DISTINCT t.id), 0), 2) AS avg_per_task,
       count(*) FILTER (WHERE m.id IS NULL) AS tasks_without_milestone,
       count(DISTINCT CASE WHEN m.id IS NOT NULL THEN t.id END) AS tasks_with_milestone,
       round(count(DISTINCT CASE WHEN m.id IS NOT NULL THEN t.id END)::numeric
             / NULLIF(count(DISTINCT t.id), 0) * 100, 1) AS coverage_pct
FROM task t
{join}
WHERE {adm.sql_task_admission("pg", "t")}
"""
    return sql, join_params


def milestone_per_task_rows(
    year: int | str | None = None,
    category: str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """``per_task`` 的逐任务清单(LEFT JOIN,零里程碑任务保留为 0)。"""
    join, join_params = _milestone_per_task_join(year, category)
    sql = f"""
SELECT t.id AS task_id, t.task_name, t.status AS task_status,
       count(m.id)    AS milestones,
       {_MS_FINISHED} AS finished
FROM task t
{join}
WHERE {adm.sql_task_admission("pg", "t")}
GROUP BY t.id, t.task_name, t.status
ORDER BY milestones DESC, t.id
LIMIT %s
"""
    return sql, (*join_params, max(1, int(limit)))


def milestone_per_task_ties(
    year: int | str | None = None,
    category: str | None = None,
) -> tuple[str, tuple]:
    """``per_task`` 的并列档条数(榜首那个里程碑数上并列了几条任务)。

    参数按 **SQL 文本顺序**出现:两次子查询各自带一遍 ON 上的年度/类别参数。
    """
    join, join_params = _milestone_per_task_join(year, category)
    join2, join_params2 = _milestone_per_task_join(year, category, milestone_alias="m2", task_alias="t2")
    sql = f"""
SELECT count(*) AS tied_at_top
FROM (
    SELECT t.id, count(m.id) AS n
    FROM task t
    {join}
    WHERE {adm.sql_task_admission("pg", "t")}
    GROUP BY t.id
) r
WHERE r.n = (
    SELECT max(r2.n) FROM (
        SELECT count(m2.id) AS n
        FROM task t2
        {join2}
        WHERE {adm.sql_task_admission("pg", "t2")}
        GROUP BY t2.id
    ) r2
)
"""
    return sql, (*join_params, *join_params2)


# ---- batch 13: 年度目标统计(year_goal_stats 的 6 个 scope)--------------------
#
# 一条最要紧的区分(演示实现的 docstring 写死):**缺口类口径永远按正式任务算**。
# ``coverage`` / ``missing`` / ``missing_by_group`` 量的是「正式任务里有多少没设目标」,
# 把分母放宽到已删除、未发布的任务上,这个缺口就不成立了 —— 所以全表出口
# (``include_informal``)只对 ``by_year`` / ``span`` 这类纯计数生效。
#
# 另一条:``coverage`` 必须用 **EXISTS 而不是 JOIN**。没有目标行的任务恰好就是要数的
# 那部分,INNER JOIN 会把它们整行丢掉,缺口永远算成 0(``missing_goal_as_zero`` 陷阱)。

YEAR_GOAL_STATS_SCOPES = ("by_year", "coverage", "missing", "missing_by_group", "span", "multi_year")

# 缺口类口径:永远按正式任务算,include_informal 对它们无效。
YEAR_GOAL_GAP_SCOPES = frozenset({"coverage", "missing", "missing_by_group"})


def _year_goal_gate(board_code: str | None, whole_table: bool) -> tuple[str, tuple]:
    """年度目标统计的准入口径。``whole_table`` 放开任务闸门(只给纯计数口径用)。"""
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = ["1 = 1"] if whole_table else [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    if code:
        where.append("t.board_id = (SELECT id FROM task_board WHERE code = %s AND is_deleted = 0)")
        params.append(code)
    return "\n  AND ".join(where), tuple(params)


def year_goal_stats(
    scope: str,
    year: int | str | None = None,
    year_to: int | str | None = None,
    min_years: int = 3,
    board_code: str | None = None,
    whole_table: bool = False,
    in_progress_only: bool = False,
    limit: int = 200,
) -> tuple[str, tuple]:
    """年度目标统计的 5 个"一次查询"scope(``span`` 另有两条模板)。"""
    if scope not in YEAR_GOAL_STATS_SCOPES:
        raise ValueError(f"未知 scope:{scope};可选 {', '.join(YEAR_GOAL_STATS_SCOPES)}")
    if scope == "span":
        # span 要均值 + 清单两条查询(``year_goal_span_avg`` / ``year_goal_span_rows``),
        # 不是一次查询能答的。落到这里说明路由写错了,直接报错而不是悄悄跑成 multi_year。
        raise ValueError("span 请用 year_goal_span_avg / year_goal_span_rows")
    bounded = max(1, int(limit))
    gate, gate_params = _year_goal_gate(board_code, whole_table and scope not in YEAR_GOAL_GAP_SCOPES)

    if scope == "by_year":
        sql = f"""
SELECT g.year, count(*) AS goal_count, count(DISTINCT g.task_id) AS task_count
FROM task_year_goal g
JOIN task t ON t.id = g.task_id
WHERE {gate}
GROUP BY g.year
ORDER BY g.year
LIMIT %s
"""
        return sql, (*gate_params, bounded)

    if scope == "coverage":
        # EXISTS 而不是 JOIN:没有目标行的任务正是要数的缺口,JOIN 会把它们丢掉。
        # 口径里那条 ``yr`` 参数在三处都出现,写进 CTE 只需要给一次。
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
        sql = f"""
WITH g AS (
    SELECT EXISTS (SELECT 1 FROM task_year_goal y
                   WHERE y.task_id = t.id AND y.year = %s) AS has_goal
    FROM task t
    WHERE {gate}
)
SELECT count(*) AS total_tasks,
       count(*) FILTER (WHERE has_goal)     AS has_goal,
       count(*) FILTER (WHERE NOT has_goal) AS missing_goal,
       round(count(*) FILTER (WHERE has_goal)::numeric / NULLIF(count(*), 0) * 100, 1) AS coverage_pct
FROM g
"""
        return sql, (y, *gate_params)

    if scope == "missing":
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
        extra = "\n  AND t.status IN (0, 1)" if in_progress_only else ""
        sql = f"""
SELECT t.id AS task_id, t.task_name, t.status, t.project_group
FROM task t
WHERE {gate}{extra}
  AND NOT EXISTS (SELECT 1 FROM task_year_goal g WHERE g.task_id = t.id AND g.year = %s)
ORDER BY t.id
LIMIT %s
"""
        return sql, (*gate_params, y, bounded)

    if scope == "missing_by_group":
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
        sql = f"""
SELECT t.project_group, count(*) AS missing_count
FROM task t
WHERE {gate}
  AND NOT EXISTS (SELECT 1 FROM task_year_goal g WHERE g.task_id = t.id AND g.year = %s)
GROUP BY t.project_group
ORDER BY missing_count DESC, t.project_group
LIMIT %s
"""
        return sql, (*gate_params, y, bounded)

    # multi_year
    y1, hint = adm.check_year(year)
    if hint:
        raise ValueError(hint)
    y2, hint = adm.check_year(year_to)
    if hint:
        raise ValueError(hint)
    if y1 == y2:
        raise ValueError(f"multi_year 需要两个不同的年度:{y1} 与 {y2} 相同")
    # HAVING 里**不能**引用输出列别名(PG 只在 ORDER BY 与 GROUP BY 允许),
    # 所以两处 CASE 表达式要写全 —— 抄演示实现的 ``HAVING goal_year_1 IS NOT NULL``
    # 会报 ``column "goal_year_1" does not exist``。
    case1 = f"max(CASE WHEN g.year = {y1} THEN g.current_year_goal END)"
    case2 = f"max(CASE WHEN g.year = {y2} THEN g.current_year_goal END)"
    sql = f"""
SELECT t.id AS task_id, t.task_name,
       {case1} AS goal_year_1,
       {case2} AS goal_year_2
FROM task_year_goal g
JOIN task t ON t.id = g.task_id
WHERE {gate}
  AND g.year IN ({y1}, {y2})
GROUP BY t.id, t.task_name
HAVING {case1} IS NOT NULL AND {case2} IS NOT NULL
ORDER BY t.id
LIMIT %s
"""
    return sql, (*gate_params, bounded)


def year_goal_missing_total(
    year: int | str,
    board_code: str | None = None,
    in_progress_only: bool = False,
) -> tuple[str, tuple]:
    """``missing`` 的总数(明细被 200 行截断后,调用方还原不出真值)。"""
    y, hint = adm.check_year(year)
    if hint:
        raise ValueError(hint)
    gate, gate_params = _year_goal_gate(board_code, whole_table=False)
    extra = "\n  AND t.status IN (0, 1)" if in_progress_only else ""
    sql = f"""
SELECT count(*) AS total_count
FROM task t
WHERE {gate}{extra}
  AND NOT EXISTS (SELECT 1 FROM task_year_goal g WHERE g.task_id = t.id AND g.year = %s)
"""
    return sql, (*gate_params, y)


def year_goal_span_avg(
    board_code: str | None = None,
    whole_table: bool = False,
    year: int | str | None = None,
) -> tuple[str, tuple]:
    """``span`` 的均值:分母只含**已设过目标**的任务(没设过的不该拉低均值)。

    ``year`` 给了就是**只数该年度内的目标条数**(``count(*) FILTER (WHERE g.year = %s)``):
    没给是"这个任务前后设了几个年度的目标",给了是"这个任务在该年度设了几条目标"。
    两者都是"每个任务有几条目标"的均值,只是分母口径随窗口收窄 —— 所以带 year 的
    span 能答,而不是回落(此前正是回落的一档:旧实现把 year 静默丢掉,按年度过滤后
    会返回一个范围更小的答案,于是干脆不接)。
    """
    gate, gate_params = _year_goal_gate(board_code, whole_table)
    counted = "count(*)"
    params: list[object] = []
    if year:
        counted = "count(*) FILTER (WHERE g.year = %s)"
        params.append(int(year))
    sql = f"""
SELECT round(avg(yr_cnt)::numeric, 2) AS avg_years
FROM (
    SELECT {counted} AS yr_cnt
    FROM task_year_goal g
    JOIN task t ON t.id = g.task_id
    WHERE {gate}
    GROUP BY g.task_id
) x
"""
    return sql, (*gate_params, *params)


def year_goal_span_rows(
    min_years: int = 3,
    board_code: str | None = None,
    whole_table: bool = False,
    limit: int = 200,
    year: int | str | None = None,
) -> tuple[str, tuple]:
    """``span`` 的逐任务清单:至少 ``min_years`` 个年度(边界取等)。

    ``GROUP_CONCAT(g.year ORDER BY g.year)`` 在 PG 里是
    ``string_agg(g.year::text, ',' ORDER BY g.year)`` —— ``year`` 是整数,
    不转文本会报 ``function string_agg(integer, unknown) does not exist``。

    ``year`` 的语义与 :func:`year_goal_span_avg` 同一处收窄:给了就把目标限定在该年度,
    于是 ``year_count`` 变成"该年度设了几条目标"(故 ``min_years`` 此时也是按**条数**比)。
    这层变化写进 ``caliber``(见 ``_formal``),不让调用方以为它还是"跨了几个年度"。
    """
    gate, gate_params = _year_goal_gate(board_code, whole_table)
    threshold = max(1, int(min_years))
    params: list[object] = []
    if year:
        gate = f"{gate}\n  AND g.year = %s"
        params.append(int(year))
    sql = f"""
SELECT t.id AS task_id, t.task_name, count(*) AS year_count,
       string_agg(g.year::text, ',' ORDER BY g.year) AS years
FROM task_year_goal g
JOIN task t ON t.id = g.task_id
WHERE {gate}
GROUP BY t.id, t.task_name
HAVING count(*) >= %s
ORDER BY year_count DESC, t.id
LIMIT %s
"""
    return sql, (*gate_params, *params, threshold, max(1, int(limit)))



def year_goal_multi_year_total(
    year: int | str,
    year_to: int | str,
    board_code: str | None = None,
) -> tuple[str, tuple]:
    """``multi_year`` 的「两个年度都设了目标」的任务数(明细只有那批任务,总数另给)。"""
    y1, hint = adm.check_year(year)
    if hint:
        raise ValueError(hint)
    y2, hint = adm.check_year(year_to)
    if hint:
        raise ValueError(hint)
    gate, gate_params = _year_goal_gate(board_code, whole_table=False)
    sql = f"""
SELECT count(*) AS tasks
FROM (
    SELECT g.task_id
    FROM task_year_goal g
    JOIN task t ON t.id = g.task_id
    WHERE {gate}
      AND g.year IN ({y1}, {y2})
    GROUP BY g.task_id
    HAVING count(DISTINCT g.year) = 2
) x
"""
    return sql, gate_params


# ---- batch 14: 看板/分类/字段字典(schema)与数据快照日期(freshness)---------
#
# 这两个工具的返回值都**没有 columns** —— 它们是复合信封(dict of lists /
# dict of dicts),不是一张表。所以:
#
#   * 字段字典**只列举 ChatBI 契约覆盖的 12 张表**,不按 ``public`` 全 schema 列。
#     正式库的 public 下还有别的表,列进来是噪音,还会把结果顶到行数上限之外;
#   * 字段字典**剔除禁止外泄的列**(storage_path / payload):它们在数据里不出现,
#     在 schema 里也不该出现,否则"这个清单就是可对外引用的全部字段"这句话不成立。


def schema_boards() -> tuple[str, tuple]:
    """看板清单(``is_deleted = 0``,按 sort_order 定序)。"""
    sql = """
SELECT id, name, code, sort_order
FROM task_board
WHERE is_deleted = 0
ORDER BY sort_order, id
"""
    return sql, ()


def schema_categories(board_code: str | None = None) -> tuple[str, tuple]:
    """分类树;``parent_id`` 为空即一级分类(rule 4:路径靠自关联拼,不臆造)。"""
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_soft_delete("c")]
    params: list[object] = []
    if code:
        where.append("c.board_id = (SELECT id FROM task_board WHERE code = %s AND is_deleted = 0)")
        params.append(code)
    sql = f"""
SELECT c.id, c.board_id, c.parent_id, c.name, c.sort_order
FROM task_category c
WHERE {"\n  AND ".join(where)}
ORDER BY c.board_id, c.parent_id, c.sort_order
LIMIT %s
"""
    return sql, (*params, 200)


def schema_columns() -> tuple[str, tuple]:
    """字段字典:表名 + 列名 + 类型 + 列注释(PG 的注释在 ``pg_description`` 里)。

    演示源读 MySQL 的 ``information_schema.COLUMNS.COLUMN_COMMENT``;PG 的
    ``information_schema.columns`` **没有**这一列,注释要经 ``pg_class`` 的 oid
    去 ``pg_description`` 取(``objsubid`` 就是 ordinal_position)。
    """
    sql = """
SELECT c.table_name, c.column_name, c.data_type,
       coalesce(d.description, '') AS comment
FROM information_schema.columns c
JOIN pg_catalog.pg_class     cls ON cls.relname = c.table_name
JOIN pg_catalog.pg_namespace ns  ON ns.oid = cls.relnamespace AND ns.nspname = c.table_schema
LEFT JOIN pg_catalog.pg_description d
       ON d.objoid = cls.oid AND d.objsubid = c.ordinal_position
WHERE c.table_schema = %s
  AND c.table_name = ANY(%s)
  AND c.column_name <> ALL(%s)
ORDER BY c.table_name, c.ordinal_position
"""
    return sql, ("public", list(CHATBI_TABLES), list(BLOCKED_COLUMNS))


def freshness_board_latest(as_of: str) -> tuple[str, tuple]:
    """每个看板:最新进展时间(``task.latest_progress_time``,含未发布行)与落后天数。

    LEFT JOIN 保留没有正式任务的看板 —— 用 INNER JOIN 会让那个看板整行消失,
    读起来像"这个看板不存在"。
    """
    _check_as_of(as_of)
    sql = f"""
SELECT b.name AS board_name,
       {adm.normalize_ts_sql("max((t.latest_progress_time)::timestamp)")} AS latest_progress,
       (%s::date - max((t.latest_progress_time)::timestamp)::date)::int AS days_behind,
       count(t.id) AS formal_task_count
FROM task_board b
LEFT JOIN task t ON t.board_id = b.id AND {adm.sql_task_admission("pg", "t")}
WHERE {adm.sql_soft_delete("b")}
GROUP BY b.id, b.name, b.sort_order
ORDER BY b.sort_order
"""
    return sql, (as_of,)


def freshness_snapshot_overall(as_of: str) -> tuple[str, tuple]:
    """全库那一对(最新时间点 + 落后天数):分看板行答不了"整个数据更新到什么时候"。"""
    _check_as_of(as_of)
    sql = f"""
SELECT {adm.normalize_ts_sql("max((t.latest_progress_time)::timestamp)")} AS newest,
       (%s::date - max((t.latest_progress_time)::timestamp)::date)::int AS days_behind,
       count(*) AS formal_task_count
FROM task t
WHERE {adm.sql_task_admission("pg", "t")}
"""
    return sql, (as_of,)


def freshness_published_per_board(as_of: str, group_history_granted: bool) -> tuple[str, tuple]:
    """每个看板的**正式**最新进展:按看板各取自己的表,不看 ``latest_progress_time``。

    两件事都是踩过的坑:

    * ``task.latest_progress_time`` **含未发布行**,所以它比正式口径新(技术组
      08-09 vs 07-31)。问"技术组数据更新到什么时候"要答正式口径那一列;
    * 两个看板的正式进展不在同一张表:技术组 ``task_progress``、集团组
      ``task_group_progress_history``。只 JOIN ``task_progress`` 会把集团组算成 NULL,
      等于把"集团组数据更新到什么时候"答成"没有数据"。分流条件是 **``b.code``**
      而不是演示实现里写死的 ``b.id = 2`` —— 正式库的看板 id 不保证与演示库一致。
    """
    _check_as_of(as_of)
    hint = adm.require_optional_table("task_group_progress_history", group_history_granted)
    if hint:
        raise PermissionError(hint)
    group_expr = "max(CASE WHEN h.is_published = 1 THEN (h.report_time)::timestamp END)"
    tech_expr = "max(CASE WHEN p.is_published = 1 THEN (p.report_time)::timestamp END)"
    pick = f"CASE WHEN b.code = 'group' THEN {group_expr} ELSE {tech_expr} END"
    sql = f"""
SELECT b.name AS board_name,
       {adm.normalize_ts_sql(pick)} AS newest_published_progress,
       (%s::date - ({pick})::date)::int AS published_days_behind
FROM task_board b
LEFT JOIN task t ON t.board_id = b.id AND {adm.sql_task_admission("pg", "t")}
LEFT JOIN task_progress p ON p.task_id = t.id
LEFT JOIN task_group_progress_history h ON h.task_id = t.id
WHERE {adm.sql_soft_delete("b")}
GROUP BY b.id, b.name, b.code, b.sort_order
ORDER BY b.sort_order
"""
    return sql, (as_of,)


def freshness_import_batches() -> tuple[str, tuple]:
    """导入批次日期:只有 ``status = 1`` 才算跑完。

    "技术组的正式数据卡在导入批次上":最后一个跑完的批次才是"数据更新到"的日期,
    还在处理中的那批没过发布门,不能当答案。
    """
    sql = f"""
SELECT {adm.normalize_date_sql("max(CASE WHEN status = 1 THEN (data_date)::timestamp END)")}
           AS newest_finished_batch,
       {adm.normalize_date_sql("max((data_date)::timestamp)")} AS newest_batch_any_status,
       {adm.normalize_date_sql("max(CASE WHEN status <> 1 THEN (data_date)::timestamp END)")}
           AS newest_unfinished_batch
FROM task_progress_import
"""
    return sql, ()


# ---- batch 15: 字段完整度 / 单任务进展历史 ------------------------------------
#
# 两条判据:
#
#   1. **空字符串按未填计入**。判据是 ``IS NULL OR = ''``,只看 NULL 会把"填了个空格"
#      当成已填 —— 真库上 project_owner_id 有 9 条是空的,而 project_owner_name 全满;
#   2. **完整率必须服务端算**,并且要一起给 ``distinct_values`` / ``top_value_rows``。
#      集团组"实施举措"55 行全部非空、填写率 100%,但 55 行是同一句话复制的 ——
#      只报填写率会推出"字段没问题",与真相相反。

# 可统计的字段白名单 -> (表, 中文标签)。列名直接进 SQL(标识符,占位符绑不了),
# 所以只能白名单,不能由调用方的字符串拼出来。
COMPLETENESS_FIELDS: dict[str, tuple[str, str]] = {
    "overall_goal": ("task", "总体目标"),
    "annual_goals": ("task", "年度目标"),
    "project_owner_name": ("task", "项目负责人"),
    "lead_owner_name": ("task", "分管领导"),
    "project_group": ("task", "项目组"),
    # 姓名列与 ID 列的完整度不是一回事:project_owner_name 128 条全满,
    # project_owner_id 只有 119 条,缺的那 9 条只能从 ID 列看出来。
    "owner_user_id": ("task", "责任人 ID"),
    "project_owner_id": ("task", "项目负责人 ID"),
    "lead_owner_id": ("task", "分管领导 ID"),
    "target_result": ("task_group_detail", "目标成果"),
    "implementation_measure": ("task_group_detail", "实施举措"),
    "progress_effect": ("task_group_detail", "进度成效"),
    "completion_time": ("task_group_detail", "完成时间(文本)"),
}


def _completeness_target(field: str) -> tuple[str, str, str]:
    """(表, 标签, 取该列的别名前缀)。明细表字段要 LEFT JOIN 回 task。"""
    if field not in COMPLETENESS_FIELDS:
        raise ValueError(f"不支持的字段:{field};支持 {', '.join(sorted(COMPLETENESS_FIELDS))}")
    table, label = COMPLETENESS_FIELDS[field]
    return table, label, ("t" if table == "task" else "d")


def completeness_counts(field: str) -> tuple[str, tuple]:
    """完整率 + 分母 + 不同值个数 + 最高频值占的行数(全部服务端算)。

    明细表字段用 LEFT JOIN:**没有明细行的任务也算缺项**(R-08),用 INNER JOIN
    会把它们整行丢掉,分母从 128 缩到有明细的那些,填写率凭空变高。
    """
    table, _label, alias = _completeness_target(field)
    join = "" if table == "task" else f"LEFT JOIN {table} d ON d.task_id = t.id"
    filled = f"{alias}.{field} IS NOT NULL AND {alias}.{field} <> ''"
    sql = f"""
SELECT count(*) AS total,
       count(*) FILTER (WHERE {filled}) AS filled,
       count(*) FILTER (WHERE NOT ({filled})) AS missing,
       round(count(*) FILTER (WHERE {filled})::numeric / NULLIF(count(*), 0) * 100, 1) AS filled_pct
FROM task t
{join}
WHERE {adm.sql_task_admission("pg", "t")}
"""
    return sql, ()


def completeness_quality(field: str) -> tuple[str, tuple]:
    """区分度:非空值里有多少个**不同值** + 最高频那个值占多少行。

    只报填写率是不够的 —— "同一句话复制 N 遍"的字段填写率可以 100%,
    而它不具备任何区分度。两个数都在服务端算:模型没法从占比里反推出来。
    """
    table, _label, alias = _completeness_target(field)
    join = "" if table == "task" else f"LEFT JOIN {table} d ON d.task_id = t.id"
    filled = f"{alias}.{field} IS NOT NULL AND {alias}.{field} <> ''"
    sql = f"""
WITH v AS (
    SELECT {alias}.{field} AS val
    FROM task t
    {join}
    WHERE {adm.sql_task_admission("pg", "t")}
      AND {filled}
)
SELECT count(*) AS distinct_values,
       coalesce(max(g.n), 0) AS top_value_rows
FROM (SELECT val, count(*) AS n FROM v GROUP BY val) g
"""
    return sql, ()


def completeness_missing_rows(field: str, limit: int = 200) -> tuple[str, tuple]:
    """缺项清单:字段为空或空串的正式任务(与计数同一个判据,不许两套)。"""
    table, _label, alias = _completeness_target(field)
    join = "" if table == "task" else f"LEFT JOIN {table} d ON d.task_id = t.id"
    filled = f"{alias}.{field} IS NOT NULL AND {alias}.{field} <> ''"
    sql = f"""
SELECT t.id, t.task_name, t.owner_user_id, t.project_owner_id,
       t.project_owner_name, t.lead_owner_name
FROM task t
{join}
WHERE {adm.sql_task_admission("pg", "t")}
  AND NOT ({filled})
ORDER BY t.id
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def completeness_missing_total(field: str) -> tuple[str, tuple]:
    """缺项总数(清单被 200 行截断后,调用方还原不出真值)。"""
    table, _label, alias = _completeness_target(field)
    join = "" if table == "task" else f"LEFT JOIN {table} d ON d.task_id = t.id"
    filled = f"{alias}.{field} IS NOT NULL AND {alias}.{field} <> ''"
    sql = f"""
SELECT count(*) AS total_count
FROM task t
{join}
WHERE {adm.sql_task_admission("pg", "t")}
  AND NOT ({filled})
"""
    return sql, ()


def completeness_raw_table(field: str) -> tuple[str, tuple]:
    """明细表字段另给**裸表口径**(不加任务闸门)。

    两个分母都对、各答各的问题:过闸的分母是 128 条正式任务(R-08 把无明细行的
    任务算成缺项),裸表的分母是这张表自己的行数。不写明分母,问"字段质量"的会拿
    128 当分母、问业务结论的会拿表行数当分母,两边都答偏。
    """
    table, _label, _alias = _completeness_target(field)
    if table == "task":
        raise ValueError("task 表上的字段没有裸表口径")
    sql = f"""
SELECT count(*) AS raw_row_count,
       count(*) FILTER (WHERE d.{field} IS NOT NULL AND d.{field} <> '') AS raw_filled,
       count(DISTINCT d.{field}) FILTER (WHERE d.{field} IS NOT NULL AND d.{field} <> '')
           AS raw_distinct_values
FROM {table} d
"""
    return sql, ()


# ---- 单任务进展历史 -----------------------------------------------------------

# 同名系列:名字去掉结尾的「(N期)」后相同的其他任务。
# 「数据资源登记体系建设」与它的三个"N期"是四条**独立**任务,各有自己的进展;
# 按裸名解析只会落到其中一条(这是对的),但只被告知"这里有 14 期"的调用方
# 无从知道系列存在,答"这个任务的进展历史"时就容易把整个系列铺开。
#
# 全角括号按转义序列写出:它是**任务名的字面量**的一部分(库里就是这个字符),
# 写成全角会触发 RUF001,换成半角就匹配不上了。
SERIES_SUFFIX = "(?:\uff08\\d+期\uff09)$"
# SQL 文本里的字面 ``%`` 必须写成 ``%%``:psycopg 会对整条 SQL 做占位符解析,
# 单写的 ``%`` 会被当成参数标记。这里后面紧跟的是**多字节**汉字,报出来的还不是
# 那句 "only '%s' ... are allowed",而是 ``'utf-8' codec can't decode byte 0xe6``
# —— 同一个坑的另一种面孔,纯单测与 pglast 语法校验都照不出来。
_SERIES_LIKE = "base.name || '\uff08%%期\uff09'"


def progress_history_rows(task_id: int, published_only: bool = True, limit: int = 200) -> tuple[str, tuple]:
    """某任务的进展各期(**相邻两期并排**,不是各期原文)。

    ``prev_progress`` 与 ``gap_days`` 由服务端 ``lag()`` 算好:"这几期有什么变化"
    要的是相邻两期并排,只给各期让模型自己错位对照,它会把上一期的正文抄串行。
    ``reporter_id`` 与 ``report_time`` 同排返回:问"最新一次进展是谁报的、什么时候报的"
    本是一问,分两次调用取的行集不一定对齐(提交单按轮次、进展按期号)。

    注意与 ``historical_progress_versions`` 的区别:那条走的是 rule 2 的例外
    (历史版本须 ``status = 3`` 已通过),这条走的是"某任务的进展各期",
    默认只要 ``is_published = 1``。
    """
    where = ["t.id = %s", adm.sql_task_admission("pg", "t")]
    params: list[object] = [int(task_id)]
    if published_only:
        where.append(adm.sql_published_progress("pg", "p"))
    ts = adm.normalize_ts_sql("p.report_time")
    pdate = adm.normalize_date_sql("p.progress_date")
    sql = f"""
SELECT p.id, p.task_id, p.version_no, p.latest_progress, p.next_work,
       {pdate} AS progress_date,
       {ts}    AS report_time,
       p.reporter_id, p.is_published, p.review_comment,
       lag(p.latest_progress) OVER (ORDER BY p.version_no) AS prev_progress,
       (p.progress_date::date
        - lag(p.progress_date) OVER (ORDER BY p.version_no)::date)::int AS gap_days
FROM task_progress p
JOIN task t ON t.id = p.task_id
WHERE {"\n  AND ".join(where)}
ORDER BY p.version_no DESC, p.id DESC
LIMIT %s
"""
    return sql, (*params, max(1, int(limit)))


def progress_history_gap_summary(task_id: int, published_only: bool = True) -> tuple[str, tuple]:
    """平均间隔天数:服务端 ``round(avg(), 1)``,首期没有上一期、不进分母。

    让模型拿 ``gap_days`` 自己平均,结果是 30.285714,报成 30.29 而口径是 30.3 ——
    与完成率 24.22 vs 24.2 同一族的毛病:小数位由谁定。
    """
    where = ["t.id = %s", adm.sql_task_admission("pg", "t")]
    params: list[object] = [int(task_id)]
    if published_only:
        where.append(adm.sql_published_progress("pg", "p"))
    sql = f"""
WITH g AS (
    SELECT (p.progress_date::date
            - lag(p.progress_date) OVER (ORDER BY p.version_no)::date)::int AS gap_days
    FROM task_progress p
    JOIN task t ON t.id = p.task_id
    WHERE {"\n  AND ".join(where)}
)
SELECT round(avg(gap_days)::numeric, 1) AS avg_gap_days,
       count(gap_days) AS gap_count
FROM g
WHERE gap_days IS NOT NULL
"""
    return sql, tuple(params)


def name_series(task_id: int) -> tuple[str, tuple]:
    """同名系列里的**其他**正式任务(名字去掉结尾「(N期)」后相同)。

    正则作为**参数**传,不拼进 SQL 文本 —— 它含反斜杠与全角括号,拼进去要处理
    两层转义(psycopg 还会把字面 ``%`` 当占位符解析)。基准名放在 CTE 里算一次,
    三处引用共用。
    """
    sql = f"""
WITH base AS (
    SELECT regexp_replace(t2.task_name, %s, '') AS name
    FROM task t2 WHERE t2.id = %s
)
SELECT t.id, t.task_name
FROM task t, base
WHERE {adm.sql_task_admission("pg", "t")}
  AND t.id <> %s
  AND (t.task_name = base.name OR t.task_name LIKE {_SERIES_LIKE})
ORDER BY t.id
"""
    return sql, (SERIES_SUFFIX, int(task_id), int(task_id))


# ---- batch 16: 导入批次核对 / 任务生命周期(建立与发布)-------------------------
#
# 两件事都在同一张表上,但问句不同:
#
#   * ``import_audit`` 核的是"声明的改了 N 条"与"实际落了几条"对不对得上 ——
#     声明值在批次行上,落库值只能对 ``task_progress`` 数出来,**两张表单独列谁都答不了**;
#   * ``task_lifecycle`` 走的是 ``task.created_at`` / ``published_at`` 这条**另一个钟**,
#     与"报进展"的钟不是一回事。

CREATED_GROUPINGS: dict[str, str] = {
    "month": "to_char((t.created_at)::timestamp, 'YYYY-MM')",
    "year": "extract(year from (t.created_at)::timestamp)::int",
}


def import_audit_summary() -> tuple[str, tuple]:
    """批次数 vs 去重业务快照日期数 vs 去重导入时间数(R-09/R-10)。"""
    sql = """
SELECT count(*)                    AS batch_count,
       count(DISTINCT data_date)   AS distinct_dates,
       count(DISTINCT import_time) AS distinct_import_times
FROM task_progress_import
"""
    return sql, ()


def import_audit_latest_finished(granted: bool) -> tuple[str, tuple]:
    """最近一批**跑完**(``status = 1``)的批次。"""
    hint = adm.require_optional_table("task_progress_import", granted)
    if hint:
        raise PermissionError(hint)
    sql = """
SELECT id, file_name, data_date, import_time,
       changed_tasks AS declared_tasks, status
FROM task_progress_import
WHERE status = 1
ORDER BY data_date DESC, id DESC
LIMIT 1
"""
    return sql, ()


def import_audit_batch_tasks(batch_id: int, limit: int = 200) -> tuple[str, tuple]:
    """某一批影响的**任务**(不是行数):``progress_rows`` 是该任务在这批里的进展行数。"""
    sql = f"""
SELECT p.import_id, p.task_id, t.task_name, count(*) AS progress_rows
FROM task_progress p
JOIN task t ON t.id = p.task_id
WHERE {adm.sql_task_admission("pg", "t")}
  AND p.import_id = %s
GROUP BY p.import_id, p.task_id, t.task_name
ORDER BY p.task_id
LIMIT %s
"""
    return sql, (int(batch_id), max(1, int(limit)))


def import_audit_orphans() -> tuple[str, tuple]:
    """孤儿:``import_id`` 非空、但批次表里查不到该批次(NOT EXISTS)。

    ``import_id IS NULL`` 是"没走导入"的手工填报,**不是孤儿** —— 混在一起会把
    手工填报的进展全报成孤儿,所以单列 ``rows_without_import``。
    """
    sql = """
SELECT count(*) FILTER (
           WHERE p.import_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM task_progress_import i WHERE i.id = p.import_id)
       ) AS orphan_rows,
       count(DISTINCT CASE
           WHEN p.import_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM task_progress_import i WHERE i.id = p.import_id)
           THEN p.import_id END) AS orphan_batch_ids,
       count(*) FILTER (WHERE p.import_id IS NULL) AS rows_without_import
FROM task_progress p
"""
    return sql, ()


def import_audit_reconcile(limit: int = 200) -> tuple[str, tuple]:
    """逐批"声明 vs 实际"。LEFT JOIN 保留**零落库**的批次。"""
    sql = """
SELECT i.id, i.file_name, i.data_date,
       i.changed_tasks AS declared_tasks,
       count(DISTINCT p.task_id) AS actual_tasks,
       count(p.id)              AS actual_rows,
       count(DISTINCT p.task_id) - i.changed_tasks AS task_diff
FROM task_progress_import i
LEFT JOIN task_progress p ON p.import_id = i.id
GROUP BY i.id, i.file_name, i.data_date, i.changed_tasks
ORDER BY i.id DESC
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def import_audit_mismatch_count() -> tuple[str, tuple]:
    """声明与实际任务数不等的批次数。

    ``actual_tasks`` 与 ``actual_rows`` 是**两个**口径:声明的是任务数,
    拿落库**行数**去比声明会得出反向结论。
    """
    sql = """
SELECT count(*) AS mismatched_batches
FROM (
    SELECT i.id, i.changed_tasks AS declared, count(DISTINCT p.task_id) AS actual_tasks
    FROM task_progress_import i
    LEFT JOIN task_progress p ON p.import_id = i.id
    GROUP BY i.id, i.changed_tasks
    HAVING i.changed_tasks <> count(DISTINCT p.task_id)
) x
"""
    return sql, ()


def import_audit_listing(limit: int = 200) -> tuple[str, tuple]:
    """批次清单(默认分支):``changed_tasks`` 是批次**自己声明**的数字。"""
    sql = """
SELECT id, file_name, data_date, import_time, total_tasks, changed_tasks, status
FROM task_progress_import
ORDER BY data_date DESC, id DESC
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def task_lifecycle_summary(year: int | str | None = None) -> tuple[str, tuple]:
    """建立/发布的汇总:最早、最晚、到发布天数(仅 ``published_at`` 非空的计入)。"""
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    y = None
    if year not in (None, "", 0, "0"):
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
        where.append("extract(year from (t.created_at)::timestamp)::int = %s")
        params.append(y)
    sql = f"""
SELECT count(*) AS formal_tasks,
       {adm.normalize_ts_sql("min((t.created_at)::timestamp)")}  AS earliest_created,
       {adm.normalize_ts_sql("max((t.created_at)::timestamp)")}  AS latest_created,
       count(*) FILTER (WHERE t.published_at IS NOT NULL) AS with_published_at,
       round(avg(((t.published_at)::timestamp)::date - ((t.created_at)::timestamp)::date)
             ::numeric, 1) AS avg_days_to_publish,
       max(((t.published_at)::timestamp)::date - ((t.created_at)::timestamp)::date) AS max_days_to_publish
FROM task t
WHERE {"\n  AND ".join(where)}
"""
    return sql, tuple(params)


def task_lifecycle_by(
    grouping: str,
    year: int | str | None = None,
    limit: int = 200,
) -> tuple[str, tuple]:
    """按建单档看**当前**状态(月 / 年)。

    任务表没有"完成时间"列,所以这是"按建单档看当前 status",**不是**
    "那一年完成的任务数" —— 跨档完成的任务仍记在建单档。口径里必须说明。
    """
    key = (grouping or "").strip().lower()
    if key not in CREATED_GROUPINGS:
        raise ValueError(f"不支持的 by:{grouping};支持 {', '.join(sorted(CREATED_GROUPINGS))}")
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    if year not in (None, "", 0, "0"):
        y, hint = adm.check_year(year)
        if hint:
            raise ValueError(hint)
        where.append("extract(year from (t.created_at)::timestamp)::int = %s")
        params.append(y)
    bucket = CREATED_GROUPINGS[key]
    sql = f"""
SELECT {bucket} AS bucket,
       count(*) AS created_count,
       count(*) FILTER (WHERE t.status = 2)     AS currently_finished,
       count(*) FILTER (WHERE t.status IN (0, 1)) AS currently_in_flight,
       round(count(*) FILTER (WHERE t.status = 2)::numeric / NULLIF(count(*), 0) * 100, 1)
           AS finished_pct
FROM task t
WHERE {"\n  AND ".join(where)}
GROUP BY bucket
ORDER BY bucket
LIMIT %s
"""
    return sql, (*params, max(1, int(limit)))


# ---- batch 17: 单任务详情(task_detail 的四个部分)-----------------------------
#
# 返回值**没有 columns** —— 它是复合信封:task(一行)+ group_detail(0/1 行)
# + recent_progress(最近 3 期)+ year_goals(各年度目标)。
#
# 两条口径必须在场:
#
#   1. ``completion_time`` 是**展示文本**,不做日期运算(R-12)。这条要**无条件**挂上:
#      task_group_detail 只覆盖集团看板,把这句话挂在那个子查询的 caliber 上,
#      技术组任务就永远看不到它 —— 而规则本来就是为它们立的;
#   2. 集团看板任务的负责人有**两套列**:task 行上的单值 ``lead_owner_name`` /
#      ``project_owner_name``,与明细表里的多值 ``lead_owner_names`` /
#      ``project_owner_names``。真库上 46 条集团任务这两边**全都不一致**,
#      谁在上面谁就被当成答案 —— 有明细行就直接判给多值列,别让模型猜。

TASK_DETAIL_COLUMNS = (
    "t.id", "t.board_id", "t.category_id", "t.task_no", "t.task_name",
    "t.owner_user_id", "t.project_owner_id", "t.project_owner_name",
    "t.lead_owner_id", "t.lead_owner_name", "t.project_group",
    "t.overall_goal", "t.annual_goals", "t.status", "t.workflow_status",
    "t.data_version", "t.sort_order",
    "t.latest_progress_time", "t.published_at", "t.is_deleted",
    "t.created_at", "t.updated_at",
)


def task_detail_row(task_id: int) -> tuple[str, tuple]:
    """任务主行(列集合与字段字典里 task 表的那 22 列一致)。"""
    sql = f"""
SELECT {", ".join(TASK_DETAIL_COLUMNS)}
FROM task t
WHERE t.id = %s
  AND {adm.sql_task_admission("pg", "t")}
"""
    return sql, (int(task_id),)


def task_detail_group_row(task_id: int) -> tuple[str, tuple]:
    """集团板扩展行(task_group_detail 与 task 是 1:1,只有集团看板任务有行)。"""
    sql = """
SELECT g.task_id, g.target_result, g.implementation_measure, g.completion_time,
       g.lead_owner_names, g.lead_owner_ids,
       g.project_owner_names, g.project_owner_ids, g.project_group, g.progress_effect
FROM task_group_detail g
WHERE g.task_id = %s
"""
    return sql, (int(task_id),)


def task_detail_recent_progress(task_id: int, limit: int = 3) -> tuple[str, tuple]:
    """最近几期**已发布**进展(按 version_no 倒序、同号按 id 倒序)。"""
    ts = adm.normalize_ts_sql("p.report_time")
    pdate = adm.normalize_date_sql("p.progress_date")
    sql = f"""
SELECT p.id, p.task_id, p.version_no, p.latest_progress, p.next_work,
       {pdate} AS progress_date, {ts} AS report_time,
       p.is_published, p.review_comment
FROM task_progress p
WHERE p.task_id = %s
  AND {adm.sql_published_progress("pg", "p")}
ORDER BY p.version_no DESC, p.id DESC
LIMIT %s
"""
    return sql, (int(task_id), max(1, int(limit)))


def task_detail_year_goals(task_id: int, limit: int = 5) -> tuple[str, tuple]:
    """该任务的各年度目标(按年度倒序)。"""
    sql = """
SELECT y.id, y.task_id, y.year, y.current_year_goal, y.milestone_summary
FROM task_year_goal y
WHERE y.task_id = %s
ORDER BY y.year DESC
LIMIT %s
"""
    return sql, (int(task_id), max(1, int(limit)))


def task_lookup(token: str) -> tuple[str, tuple]:
    """按 id 或名字定位一条**已发布**任务(与演示实现同一套优先级)。

    纯数字只当 id:**名字兜底会把一个错的任务悄悄顶上来** —— 演示实现曾因
    ``task="2"`` 落到 LIKE 分支、把另一条名字含 "2" 的任务当成了任务 2。
    名字先精确匹配;没有再取**最短**的子串匹配(最短的名字是对用户输入最少加戏的读法)。
    """
    gate = adm.sql_task_admission("pg", "t")
    sql = f"""
SELECT {", ".join(TASK_DETAIL_COLUMNS)}
FROM task t
WHERE {gate}
  AND (t.task_name = %s OR t.task_name ILIKE %s)
ORDER BY length(t.task_name), t.id
LIMIT 1
"""
    return sql, (token, f"%{token}%")


# ---- batch 18: 审批时长(approval_turnaround 的 4 个 scope)--------------------
#
# 一条与其余出口**正好相反**的口径:``pending`` 档**刻意不套发布闸门**。
# 卡在审批里的提交单按定义就还没发布,套上 R-01 会得到一个空的积压队列 ——
# 那不是"没有积压",是把问题问没了。其余三档(已完成轮次)照常带正式任务门。

TURNAROUND_SCOPES = ("summary", "board", "slowest", "pending")

# 审批耗时 = 完成时刻 - 提交时刻,按**两个日期**相减(写成 timestamp 相减得到的是
# interval,date_part 会少一天,与 DATEDIFF 语义不符)。
_TURNAROUND_DAYS = (
    "((s.completed_at)::timestamp::date - (s.submitted_at)::timestamp::date)::int"
)
# 只有两端都非空才是"已完成轮次"。
_TURNAROUND_DONE = "s.completed_at IS NOT NULL AND s.submitted_at IS NOT NULL"


def turnaround_summary() -> tuple[str, tuple]:
    """已完成轮次的耗时汇总(轮次数 / 均值 / 最长)。"""
    sql = f"""
SELECT count(*) AS completed_rounds,
       round(avg({_TURNAROUND_DAYS})::numeric, 1) AS avg_days,
       max({_TURNAROUND_DAYS}) AS max_days
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
WHERE {adm.sql_task_admission("pg", "t")}
  AND {_TURNAROUND_DONE}
"""
    return sql, ()


def turnaround_by_board() -> tuple[str, tuple]:
    """按看板的耗时(看板名 / 轮次数 / 均值)。"""
    sql = f"""
SELECT b.name AS board_name, count(*) AS n,
       round(avg({_TURNAROUND_DAYS})::numeric, 1) AS avg_days
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
JOIN task_board b ON b.id = t.board_id AND {adm.sql_soft_delete("b")}
WHERE {adm.sql_task_admission("pg", "t")}
  AND {_TURNAROUND_DONE}
GROUP BY b.id, b.name, b.sort_order
ORDER BY b.sort_order
"""
    return sql, ()


def turnaround_slowest(limit: int = 8) -> tuple[str, tuple]:
    """最慢的几轮(按耗时降序、并列按 task id 升序)。"""
    sql = f"""
SELECT t.id AS task_id, t.task_name, s.round_no,
       {adm.normalize_ts_sql("s.submitted_at")} AS submitted_at,
       {adm.normalize_ts_sql("s.completed_at")} AS completed_at,
       {_TURNAROUND_DAYS} AS days
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
WHERE {adm.sql_task_admission("pg", "t")}
  AND {_TURNAROUND_DONE}
ORDER BY days DESC, t.id
LIMIT %s
"""
    return sql, (max(1, min(50, int(limit))),)


def turnaround_slowest_ties() -> tuple[str, tuple]:
    """最慢那档并列了几轮。

    只回榜单时榜首看着是唯一第一名:实测最慢那档是 **59 天两轮**(任务 76 与 143)。
    并列数交给服务端数,取舍写进口径。
    """
    sql = f"""
SELECT count(*) AS tied_at_top
FROM (
    SELECT {_TURNAROUND_DAYS} AS days
    FROM task_workflow_submission s
    JOIN task t ON t.id = s.task_id
    WHERE {adm.sql_task_admission("pg", "t")}
      AND {_TURNAROUND_DONE}
) r
WHERE r.days = (
    SELECT max(r2.days) FROM (
        SELECT {_TURNAROUND_DAYS} AS days
        FROM task_workflow_submission s
        JOIN task t ON t.id = s.task_id
        WHERE {adm.sql_task_admission("pg", "t")}
          AND {_TURNAROUND_DONE}
    ) r2
)
"""
    return sql, ()


def turnaround_pending(as_of: str, limit: int = 8) -> tuple[str, tuple]:
    """积压队列:未完成(``completed_at`` 为空)的提交单,按已等天数降序。

    **刻意不套发布闸门**:待审提交单本就尚未发布,加上 R-01 会得到空队列。
    只保留 ``t.is_deleted = 0``(软删任务下的单不该出现在队列里)。
    """
    _check_as_of(as_of)
    sql = f"""
SELECT t.task_name, s.round_no, s.status,
       {adm.normalize_ts_sql("s.submitted_at")} AS submitted_at,
       (%s::date - (s.submitted_at)::timestamp::date)::int AS pending_days
FROM task_workflow_submission s
JOIN task t ON t.id = s.task_id
WHERE {adm.sql_soft_delete("t")}
  AND s.completed_at IS NULL
  AND s.submitted_at IS NOT NULL
ORDER BY pending_days DESC, t.id
LIMIT %s
"""
    return sql, (as_of, max(1, min(50, int(limit))))


# ---- batch 19: 任务聚合(aggregate 的 9 个分组轴)-------------------------------
#
# 三条口径各有一处"反着来":
#
#   * ``workflow_status`` 是**唯一不加发布闸门**的分组 —— 问的就是审批流转状态分布,
#     把 published 当前置条件会只剩一档 128,其余六档(未发布的 22 条)全部消失;
#   * ``category`` 的看板过滤要**同时**落在分类树(``c.board_id``)与任务上:只过滤计数时
#     行清单仍是全部 47 个分类,另一看板的只是变成 cnt=0,与"本看板确实没有任务"长得一样
#     (技术组真值是 28 = 7 个一级 + 21 个二级);
#   * 空分组要保留(R-02):``board`` / ``category`` 用 LEFT JOIN,闸门挂在 **ON** 上。

AGGREGATE_GROUP_BYS = (
    "board",
    "category",
    "primary_category",
    "top_sub_per_primary",
    "status",
    "workflow_status",
    "project_group",
    "owner",
    "name_series",
)

# 业务进度状态(与 workflow_status 是两套词汇,不可互换)。
BUSINESS_STATUS_LABELS = (
    "CASE t.status WHEN 0 THEN '未开始' WHEN 1 THEN '进行中' "
    "WHEN 2 THEN '已完成' WHEN 3 THEN '已停用' ELSE '未知' END"
)

# 项目组:空值归成"(未填)"而不是自成一档空字符串。
_PROJECT_GROUP = "coalesce(nullif(btrim(t.project_group), ''), '(未填)')"
# 牵头领导:同样归并空值(R-11:该栏有不止一种填法,先按填法枚举再计数,不做归一化猜测)。
_LEAD_OWNER = "coalesce(nullif(btrim(t.lead_owner_name), ''), '(未填)')"

# 同名系列:任务名去掉尾部「(N期)」后归并成家族。全角括号按转义序列写出
# (它是任务名的字面量的一部分,写成全角会触发 RUF001)。
SERIES_FAMILY_RE = "\uff08[0-9]+期\uff09$"


def _aggregate_scope(board_code: str | None) -> tuple[str, list[object]]:
    """聚合的准入口径(可选按看板收窄)。"""
    code, hint = adm.check_board_code(board_code) if board_code else (None, None)
    if hint:
        raise ValueError(hint)
    where = [adm.sql_task_admission("pg", "t")]
    params: list[object] = []
    if code:
        where.append("t.board_id = (SELECT id FROM task_board WHERE code = %s AND is_deleted = 0)")
        params.append(code)
    return "\n  AND ".join(where), params


def aggregate_groups(
    group_by: str,
    board_code: str | None = None,
    order_by: str = "",
    ascending: bool = False,
) -> tuple[str, tuple]:
    """按 ``group_by`` 聚合正式任务(LEFT JOIN 保留空分组,R-02/R-08)。

    行数上限**不写进 SQL**:截断由上层加(它还要先数有多少组,才能把"切掉了几个"写进口径)。
    """
    key = (group_by or "").strip().lower()
    if key not in AGGREGATE_GROUP_BYS:
        raise ValueError(f"不支持的 group_by:{group_by};支持 {', '.join(AGGREGATE_GROUP_BYS)}")
    by_rate = (order_by or "").strip().lower() == "finish_rate"
    direction = "ASC" if ascending else "DESC"

    if key == "workflow_status":
        # 唯一不加发布闸门的一档:问的就是审批流转状态分布。
        where = [adm.sql_soft_delete("t")]
        params: list[object] = []
        code, hint = adm.check_board_code(board_code) if board_code else (None, None)
        if hint:
            raise ValueError(hint)
        if code:
            where.append("t.board_id = (SELECT id FROM task_board WHERE code = %s AND is_deleted = 0)")
            params.append(code)
        sql = f"""
SELECT t.workflow_status AS group_name, count(*) AS cnt
FROM task t
WHERE {"\n  AND ".join(where)}
GROUP BY t.workflow_status
ORDER BY cnt DESC, t.workflow_status
"""
        return sql, tuple(params)

    scope, params = _aggregate_scope(board_code)
    if key == "board":
        sql = f"""
SELECT b.name AS group_name, count(t.id) AS cnt
FROM task_board b
LEFT JOIN task t ON t.board_id = b.id AND {scope}
WHERE {adm.sql_soft_delete("b")}
GROUP BY b.id, b.name, b.sort_order
ORDER BY b.sort_order
"""
        return sql, tuple(params)

    if key == "category":
        # 看板过滤要**同时**落在分类树上:只过滤计数会让另一看板的 19 个分类以 cnt=0 出现。
        cat_board = ""
        if board_code:
            cat_board = "\n  AND c.board_id = (SELECT id FROM task_board WHERE code = %s AND is_deleted = 0)"
        sql = f"""
SELECT c.name AS group_name, c.parent_id, count(t.id) AS cnt
FROM task_category c
LEFT JOIN task t ON t.category_id = c.id AND {scope}
WHERE {adm.sql_soft_delete("c")}{cat_board}
GROUP BY c.id, c.name, c.parent_id
ORDER BY cnt DESC, c.id
"""
        tail = (board_code,) if board_code else ()
        return sql, (*params, *tail)

    if key == "primary_category":
        board_filter = ""
        if board_code:
            board_filter = "\n  AND cb.id = (SELECT id FROM task_board WHERE code = %s AND is_deleted = 0)"
        order = (
            f"finish_rate_pct {direction}, pc.id" if by_rate else "cnt DESC, pc.id"
        )
        sql = f"""
SELECT pc.name AS group_name, count(*) AS cnt,
       count(*) FILTER (WHERE t.status = 2) AS finished,
       round(count(*) FILTER (WHERE t.status = 2)::numeric / NULLIF(count(*), 0) * 100, 1)
           AS finish_rate_pct
FROM task t
JOIN task_category c  ON c.id = t.category_id AND {adm.sql_soft_delete("c")}
JOIN task_board    cb ON cb.id = c.board_id   AND {adm.sql_soft_delete("cb")}{board_filter}
JOIN task_category pc ON pc.id = c.parent_id  AND {adm.sql_soft_delete("pc")}
WHERE {adm.sql_task_admission("pg", "t")}
GROUP BY pc.id, pc.name
ORDER BY {order}
"""
        tail = (board_code,) if board_code else ()
        return sql, (*tail,)

    if key == "top_sub_per_primary":
        # 每个一级分类下任务数最多的那一个二级分类:组内并列按 c.id 裁决(与参考实现的
        # ROW_NUMBER 同一套定序键),一组一行,行数即一级分类数。
        sql = f"""
SELECT r.primary_name AS group_name, r.sub_name, r.tasks AS cnt
FROM (
    SELECT pc.name AS primary_name, c.name AS sub_name,
           count(t.id) AS tasks,
           row_number() OVER (PARTITION BY pc.id ORDER BY count(t.id) DESC, c.id) AS rn
    FROM task t
    JOIN task_category c  ON c.id = t.category_id AND {adm.sql_soft_delete("c")}
    JOIN task_category pc ON pc.id = c.parent_id  AND {adm.sql_soft_delete("pc")}
    WHERE {scope}
    GROUP BY pc.id, pc.name, c.id, c.name
) r
WHERE r.rn = 1
ORDER BY r.primary_name
"""
        return sql, tuple(params)

    if key == "status":
        sql = f"""
SELECT {BUSINESS_STATUS_LABELS} AS group_name, count(*) AS cnt
FROM task t
WHERE {scope}
GROUP BY t.status
ORDER BY t.status
"""
        return sql, tuple(params)

    if key == "owner":
        sql = f"""
SELECT {_LEAD_OWNER} AS group_name, count(*) AS cnt
FROM task t
WHERE {scope}
GROUP BY group_name
ORDER BY cnt DESC, group_name
"""
        return sql, tuple(params)

    if key == "name_series":
        # 家族名用正则算;正则作为**参数**传(全角括号 + 结尾锚点,拼进文本要处理转义)。
        sql = f"""
SELECT regexp_replace(t.task_name, %s, '') AS family_name,
       count(*) AS cnt,
       string_agg(t.id::text, ',' ORDER BY t.id) AS task_ids
FROM task t
WHERE {scope}
GROUP BY family_name
ORDER BY cnt DESC, family_name
"""
        return sql, (SERIES_FAMILY_RE, *params)

    # project_group:占比与累计占比必须服务端算。小数位取 **2** 而不是 1:
    # 累计占比要跟阈值比大小,58.59% 舍成 58.6% 再跟 55% 比就会串档。
    share_cols = ""
    if not by_rate:
        share_cols = (
            f"round(count(*)::numeric / NULLIF(sum(count(*)) OVER (), 0) * 100, 2) AS share_pct,\n       "
            f"round((sum(count(*)) OVER (ORDER BY count(*) DESC, {_PROJECT_GROUP}))::numeric\n"
            f"             / NULLIF(sum(count(*)) OVER (), 0) * 100, 2) AS cum_pct,\n       "
        )
    order = f"finish_rate_pct {direction}, group_name" if by_rate else "cnt DESC, group_name"
    sql = f"""
SELECT {_PROJECT_GROUP} AS group_name,
       count(*) AS cnt,
       count(*) FILTER (WHERE t.status = 2) AS finished,
       round(count(*) FILTER (WHERE t.status = 2)::numeric / NULLIF(count(*), 0) * 100, 1)
           AS finish_rate_pct,
       {share_cols}count(DISTINCT nullif(btrim(t.lead_owner_name), ''))    AS lead_owner_count,
       count(DISTINCT nullif(btrim(t.project_owner_name), '')) AS project_owner_count
FROM task t
WHERE {scope}
GROUP BY group_name
ORDER BY {order}
"""
    return sql, tuple(params)


def aggregate_total_groups(sql: str, params: tuple) -> tuple[str, tuple]:
    """本次定序下的**分组总数**(截断前)。

    截断落在上层,所以要先知道总共有几组,才能把"硬切前 N 组、共 M 组"写进口径 ——
    模型看到 5 行就答 5 行,不会因为"还有并列的"而自己补列成 9 行。
    """
    return f"SELECT count(*) AS total_groups FROM (\n{sql}\n) all_groups", params


# ---- batch 20: 集团板统计(group_stats 的 14 个 scope)--------------------------
#
# 集团板这一档全部落在两张专表上:``task_group_detail``(1:1 扩展行)与
# ``task_group_progress_history``(历次成效)。三条口径:
#
#   * **完成时间是展示文本**(R-12):能算日期的只有"标准日期"与"YYYYQn"两种写法,
#     季度取季末日;其余自由文本归一化后为 **NULL** —— 不能猜成 12-31,那是替业务下判断。
#     分档的**判别顺序即优先级**,不能重排:'2026年6月底' 同时命中「含底」与「中文年月」,
#     先判到哪档就算哪档,调换后两档会把 11 拆成 6+5;
#   * **多值负责人按元素切,不用 LIKE**:两种分隔符(顿号与逗号)混用,只按顿号切会把
#     逗号串当成一个人;而 ``LIKE '%名字%'`` 会在不同人之间碰撞(短名是长名的子串);
#   * **零附件的任务要留住**(LEFT JOIN,46 条里 18 条没有附件,那通常正是问句要数的)。

GROUP_STATS_SCOPES = (
    "owners",
    "project_group_raw",
    "completion_time",
    "completion_time_values",
    "completion_time_formats",
    "overdue",
    "field_lengths",
    "attachments",
    "attachment_distribution",
    "history_rounds",
    "separators",
    "owner_widths",
    "effect_consistency",
    "status_effect_conflict",
)

GROUP_BOARD_JOIN = (
    "JOIN task_board gb ON gb.id = t.board_id AND gb.is_deleted = 0 AND gb.code = 'group'"
)

# 完成时间的截止日归一化:只有两种写法能算出日子,其余为 NULL。
_COMPLETION_DEADLINE = (
    "CASE "
    "WHEN d.completion_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' "
    "THEN (d.completion_time)::date "
    "WHEN d.completion_time ~ '^[0-9]{4}Q[1-4]$' THEN "
    "(substr(d.completion_time, 1, 4) || '-' || "
    "(ARRAY['03-31','06-30','09-30','12-31'])[substr(d.completion_time, 6, 1)::int])::date "
    "END"
)

# 完成时间的「写法」分档。判别顺序即优先级,不能重排(见上)。
_COMPLETION_TIME_FORMAT_CASE = (
    "CASE "
    "WHEN d.completion_time ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN '标准日期 YYYY-MM-DD' "
    "WHEN d.completion_time ~ '^[0-9]{4}Q[1-4]$' THEN '季度 YYYYQn' "
    "WHEN d.completion_time LIKE '%%底%%' THEN '模糊表述(含\u201c底\u201d)' "
    "WHEN d.completion_time LIKE '%%年%%月%%日%%' THEN '中文年月日' "
    "WHEN d.completion_time LIKE '%%年%%月%%' THEN '中文年月' "
    "ELSE '其他' END"
)


def _group_scope() -> str:
    """集团板任务的准入口径(只看集团看板)。"""
    return f"{adm.sql_task_admission('pg', 't')}\n  AND gb.id IS NOT NULL"


def group_stats_owners() -> tuple[str, tuple]:
    """牵头人:多值 / 单人 / 未填 三档 + 去重人数。

    ``LIKE '%%,%%'`` 里的 ``%%`` 不是笔误:SQL 文本里的字面 ``%`` 必须写两遍,
    否则 psycopg 会把它当占位符(报 ``only '%s', '%b', '%t' are allowed``)。
    """
    sql = f"""
SELECT count(*) AS tasks,
       count(*) FILTER (WHERE d.lead_owner_ids LIKE '%%,%%')              AS multi_lead,
       count(*) FILTER (WHERE d.lead_owner_ids NOT LIKE '%%,%%'
                          AND coalesce(d.lead_owner_ids, '') <> '')       AS single_lead,
       count(*) FILTER (WHERE coalesce(d.lead_owner_ids, '') = '')        AS no_lead
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
"""
    return sql, ()


def group_stats_distinct_leads() -> tuple[str, tuple]:
    """牵头人逐元素拆分后去重计数(两种分隔符都要切)。"""
    sql = f"""
SELECT count(DISTINCT uid) AS distinct_leads
FROM (
    SELECT unnest({_multivalue_array("d.lead_owner_ids")}) AS uid
    FROM task_group_detail d
    JOIN task t ON t.id = d.task_id
    {GROUP_BOARD_JOIN}
    WHERE {_group_scope()}
      AND coalesce(d.lead_owner_ids, '') <> ''
) x
WHERE uid <> ''
"""
    return sql, ()


def group_stats_project_group_raw(limit: int = 8) -> tuple[str, tuple]:
    """集团明细按专项组分(裸表口径,不加任务闸门)。

    与 ``aggregate group_by=project_group`` 的**任务口径**是两回事:这里一行是明细表的行,
    那里一行是任务。裸表 55 行 / 过闸 46 行,差的 9 行挂在已软删或未发布的任务上。
    """
    sql = f"""
SELECT d.project_group AS grp, count(*) AS rows_,
       count(*) FILTER (WHERE {adm.sql_task_admission("pg", "t")}) AS formal_rows,
       count(*) FILTER (WHERE d.target_result IS NOT NULL AND d.target_result <> '') AS target_filled,
       count(*) FILTER (WHERE d.implementation_measure IS NOT NULL
                          AND d.implementation_measure <> '') AS measure_filled
FROM task_group_detail d
LEFT JOIN task t ON t.id = d.task_id
GROUP BY d.project_group
ORDER BY rows_ DESC, grp
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_raw_tiers() -> tuple[str, tuple]:
    """裸表 / 过闸两个分母(口径对照用)。"""
    sql = f"""
SELECT (SELECT count(*) FROM task_group_detail) AS raw_table,
       (SELECT count(*) FROM task_group_detail d
        JOIN task t ON t.id = d.task_id WHERE {adm.sql_task_admission("pg", "t")}) AS formal_task_gate
"""
    return sql, ()


def group_stats_separators(limit: int = 8) -> tuple[str, tuple]:
    """多值负责人栏的分隔符分档(混着填的:顿号 / 逗号 / 两种并存 / 单人无分隔符)。"""
    sql = f"""
SELECT CASE
         WHEN d.project_owner_names LIKE '%%、%%' AND d.project_owner_names LIKE '%%,%%'
              THEN '两种并存'
         WHEN d.project_owner_names LIKE '%%、%%' THEN '全角顿号'
         WHEN d.project_owner_names LIKE '%%,%%' THEN '半角逗号'
         ELSE '单人无分隔符' END AS separator_kind,
       count(*) AS n
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.project_owner_names IS NOT NULL AND d.project_owner_names <> ''
GROUP BY separator_kind
ORDER BY n DESC, separator_kind
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_owner_widths(limit: int = 8) -> tuple[str, tuple]:
    """每个任务的负责人个数 = 分隔符个数 + 1(顿号与逗号都计入)。"""
    sql = f"""
SELECT t.task_name, d.project_owner_names,
       cardinality({_multivalue_array("d.project_owner_names")}) AS owner_count
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.project_owner_names IS NOT NULL AND d.project_owner_names <> ''
ORDER BY owner_count DESC, t.id
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_completion_time() -> tuple[str, tuple]:
    """完成时间的格式三档:标准日期 / 自由文本 / 空(R-12:只做格式判别)。"""
    sql = f"""
SELECT count(*) AS tasks,
       count(*) FILTER (WHERE d.completion_time ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') AS iso_date,
       count(*) FILTER (WHERE d.completion_time IS NOT NULL AND d.completion_time <> ''
                          AND d.completion_time !~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') AS free_text,
       count(*) FILTER (WHERE d.completion_time IS NULL OR d.completion_time = '') AS blank
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
"""
    return sql, ()


def group_stats_completion_time_values(limit: int = 8) -> tuple[str, tuple]:
    """去重后的 completion_time **原样取值**(不是归纳出来的类别名)。"""
    sql = f"""
SELECT DISTINCT d.completion_time
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.completion_time IS NOT NULL AND d.completion_time <> ''
ORDER BY d.completion_time
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_completion_time_values_total() -> tuple[str, tuple]:
    sql = f"""
SELECT count(DISTINCT d.completion_time) AS total_count
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.completion_time IS NOT NULL AND d.completion_time <> ''
"""
    return sql, ()


def group_stats_completion_time_formats(limit: int = 8) -> tuple[str, tuple]:
    """按**写法**归档(档数是写法种类数,不是去重取值数)。"""
    sql = f"""
SELECT {_COMPLETION_TIME_FORMAT_CASE} AS fmt, count(*) AS cnt
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.completion_time IS NOT NULL AND d.completion_time <> ''
GROUP BY fmt
ORDER BY cnt DESC, fmt
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_completion_time_formats_total() -> tuple[str, tuple]:
    sql = f"""
SELECT count(*) AS total_count
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.completion_time IS NOT NULL AND d.completion_time <> ''
"""
    return sql, ()


def group_stats_overdue(as_of: str, limit: int = 8) -> tuple[str, tuple]:
    """超期:归一化截止日早于快照日且 ``status <> 2``(未完成)。

    只认标准日期与 ``YYYYQn`` 两种写法(季度取季末日),其余为"判不了"而不是"没超期"。
    """
    _check_as_of(as_of)
    sql = f"""
SELECT t.id AS task_id, t.task_name, t.status, d.completion_time,
       {adm.normalize_date_sql(_COMPLETION_DEADLINE)} AS deadline,
       (%s::date - ({_COMPLETION_DEADLINE}))::int AS days_overdue
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND ({_COMPLETION_DEADLINE}) < %s::date
  AND t.status <> 2
ORDER BY deadline, t.id
LIMIT %s
"""
    return sql, (as_of, as_of, max(1, int(limit)))


def group_stats_overdue_unparsable() -> tuple[str, tuple]:
    """判不了的条数:完成时间非空但两种写法都对不上。

    它们**不是"没超期"**,是判不了 —— 混起来会把"无法判断"说成"都没超期"。
    """
    sql = f"""
SELECT count(*) AS unparsable_count
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.completion_time IS NOT NULL AND d.completion_time <> ''
  AND ({_COMPLETION_DEADLINE}) IS NULL
"""
    return sql, ()


def group_stats_field_lengths() -> tuple[str, tuple]:
    """``target_result`` 的字符数统计(``length`` 按字符,不是字节)。"""
    sql = f"""
SELECT count(*) AS tasks,
       round(avg(length(d.target_result))::numeric, 1) AS avg_chars,
       max(length(d.target_result)) AS max_chars,
       min(length(d.target_result)) AS min_chars
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND d.target_result IS NOT NULL AND d.target_result <> ''
"""
    return sql, ()


def group_stats_status_effect_conflict(limit: int = 8) -> tuple[str, tuple]:
    """状态与当期成效自相矛盾:``status = 0``(未开始)却填了非空 ``progress_effect``。"""
    sql = f"""
SELECT t.id AS task_id, t.task_name, t.status,
       left(d.progress_effect, 50) AS effect_head
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
  AND t.status = 0
  AND d.progress_effect IS NOT NULL AND d.progress_effect <> ''
ORDER BY t.id
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_effect_consistency(limit: int = 8) -> tuple[str, tuple]:
    """明细表的当前成效 vs 历史表**最新一期**(``is_published = 1``)的成效,逐字比对。

    ``same`` 按参考实现给 **1 / 0**(不是布尔):口径句里写的就是 "same = 1 一致、0 不一致",
    列的形状跟参考查询走。不一致的排最前(``ORDER BY same``):先看 ``same = 0`` 有几行,
    再下"全部一致"的结论 —— 两段长文本靠模型眼看,46 行里会把不一致的说成一致。
    """
    sql = f"""
SELECT d.task_id, t.task_name, x.version_no,
       (d.progress_effect = x.progress_effect)::int AS same
FROM task_group_detail d
JOIN task t ON t.id = d.task_id
{GROUP_BOARD_JOIN}
JOIN (
    SELECT h.task_id, h.progress_effect, h.version_no,
           row_number() OVER (PARTITION BY h.task_id ORDER BY h.version_no DESC, h.id DESC) AS rn
    FROM task_group_progress_history h
    WHERE h.is_published = 1
) x ON x.task_id = d.task_id AND x.rn = 1
WHERE {_group_scope()}
ORDER BY same, d.task_id
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_attachments(limit: int = 8) -> tuple[str, tuple]:
    """逐任务的附件条数(LEFT JOIN 保留零附件任务,按条数升序)。"""
    sql = f"""
SELECT t.id AS task_id, t.task_name, count(a.id) AS attachments
FROM task t
{GROUP_BOARD_JOIN}
LEFT JOIN task_attachment a ON a.task_id = t.id AND {adm.sql_soft_delete("a")}
WHERE {_group_scope()}
GROUP BY t.id, t.task_name
ORDER BY attachments ASC, t.id
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_attachments_summary() -> tuple[str, tuple]:
    """集团板任务数 + 零附件任务数(自检,与清单同一口径)。"""
    sql = f"""
SELECT count(*) AS tasks,
       count(*) FILTER (WHERE NOT EXISTS (
           SELECT 1 FROM task_attachment a
           WHERE a.task_id = t.id AND {adm.sql_soft_delete("a")}
       )) AS no_attachment
FROM task t
{GROUP_BOARD_JOIN}
WHERE {_group_scope()}
"""
    return sql, ()


def group_stats_attachment_distribution(limit: int = 8) -> tuple[str, tuple]:
    """附件条数的**分布**(每档多少任务),零附件档由 LEFT JOIN 保住。"""
    sql = f"""
SELECT c.attachments, count(*) AS tasks
FROM (
    SELECT t.id, count(a.id) AS attachments
    FROM task t
    {GROUP_BOARD_JOIN}
    LEFT JOIN task_attachment a ON a.task_id = t.id AND {adm.sql_soft_delete("a")}
    WHERE {_group_scope()}
    GROUP BY t.id
) c
GROUP BY c.attachments
ORDER BY c.attachments
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_history_rounds(limit: int = 8) -> tuple[str, tuple]:
    """逐任务的历史期数(LEFT JOIN 保留零期任务;``is_published = 1`` 是行侧闸门)。"""
    sql = f"""
SELECT t.id AS task_id, t.task_name, count(h.id) AS rounds
FROM task t
{GROUP_BOARD_JOIN}
LEFT JOIN task_group_progress_history h ON h.task_id = t.id AND h.is_published = 1
WHERE {_group_scope()}
GROUP BY t.id, t.task_name
ORDER BY rounds DESC, t.id
LIMIT %s
"""
    return sql, (max(1, int(limit)),)


def group_stats_history_rounds_at_least(min_rounds: int) -> tuple[str, tuple]:
    """至少 ``min_rounds`` 期的任务数(边界取等)。

    这里的闸门是**两道**:任务侧已发布(``_group_scope``)且历史行自身
    ``is_published = 1`` —— 404 行过闸 362 行,丢掉行侧那道会把 42 条未审草稿折进来。
    """
    sql = f"""
SELECT count(*) AS tasks
FROM (
    SELECT h.task_id
    FROM task_group_progress_history h
    JOIN task t ON t.id = h.task_id
    {GROUP_BOARD_JOIN}
    WHERE {_group_scope()}
      AND h.is_published = 1
    GROUP BY h.task_id
    HAVING count(*) >= %s
) x
"""
    return sql, (max(1, int(min_rounds)),)
