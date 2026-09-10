"""Formal-source (O2OA PostgreSQL) implementations for the migrated tool scopes.

The demo answers from ``weekly_mock`` over MySQL; the formal source is O2OA's
PostgreSQL.  ``TASK_BOARD_DATA_SOURCE=o2oa`` switches the *migrated* scopes over
to the PG templates in ``_o2oa_templates``; everything else keeps running on the
demo path, so a half-migrated service is still coherent rather than broken.

Two rules every migrated scope follows:

* **same envelope** as ``_store.fetch`` (``ok`` / ``caliber`` / ``snapshot_note``
  / ``snapshot_date`` / ``source_tables`` / ``columns`` / ``rows`` /
  ``row_count`` / ``has_more``) so the agent side needs no change;
* **unmigrated scope or argument returns ``None``** -- the caller then runs the
  demo implementation.  Never silently answer a narrower question than asked.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from typing import Any

import _admission as adm
import _o2oa_templates as tpl

try:  # psycopg is only needed on the formal path
    import _pg
except ImportError:  # pragma: no cover
    _pg = None  # ty: ignore (module-typed name cannot be rebound;演示源不需要 psycopg)

MAX_ROWS = 200

FORMAL_SNAPSHOT_NOTE = "国数正式只读源(O2OA PostgreSQL / task-board 应用),非演示数据"

# 相对时间窗的基准日:正式源是活库,默认可取当天;联调期用 GUOSHU_AS_OF 固定,
# 以便与演示包的快照日(2026-08-15)对同一批题。
DEFAULT_AS_OF = "2026-08-15"

_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
# 与 ``_store._DATE_RE`` 同一形状:调用方给的日期要么是这个格式,要么就是口径错。
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def enabled() -> bool:
    """True when the formal source is selected *and* a driver is available."""
    if _pg is None:
        return False
    return os.environ.get("TASK_BOARD_DATA_SOURCE", "").strip().lower() in ("o2oa", "pg")


def as_of() -> str:
    """基准日:联调期用 GUOSHU_AS_OF 固定,否则取当天(正式活库)。"""
    fixed = os.environ.get("GUOSHU_AS_OF", "").strip()
    return fixed or dt.date.today().isoformat()


def source_tables(sql: str) -> list[str]:
    """表名清单,与 ``_store.source_tables`` 同义(供口径文案与审计使用)。"""
    return sorted({name.lower() for name in _TABLE_RE.findall(sql)})


def envelope(
    *,
    sql: str,
    params: tuple,
    caliber: str,
    limit: int,
    columns_override: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    cap_last_param: bool = False,
) -> dict[str, Any]:
    """执行一条模板 SQL,并包成与演示源一致的信封。

    ``cap_last_param``:清单类模板把**行数上限放在最后一个参数**。这时信封按
    ``limit + 1`` 去查、再截回 ``limit``,用多出来的那一行判定 ``has_more`` ——
    与 ``_store.fetch`` 同一个技巧。少了这一步,SQL 里写死的 ``LIMIT`` 会让
    "刚好取满" 与 "被截断" 长得一模一样(313 行的年度目标会被报成 200 行且
    ``has_more=false``,实测踩过)。
    """
    if _pg is None:  # pragma: no cover - enabled() 已经挡住
        raise RuntimeError("psycopg 不可用,无法访问正式源")
    bounded = max(1, min(MAX_ROWS, int(limit)))
    sql_params = params
    if cap_last_param and params:
        # 多要一行只为判定截断;模板里的 LIMIT 已经把结果集压在 bounded+1 以内
        sql_params = (*params[:-1], bounded + 1)
    conn = _pg.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, sql_params)
            columns = [d.name for d in cur.description] if cur.description else []
            raw = cur.fetchall()
    finally:
        conn.close()
    has_more = len(raw) > bounded
    rows = [dict(zip(columns, r, strict=True)) for r in raw[:bounded]]
    result: dict[str, Any] = {
        "ok": True,
        "caliber": caliber or "无附加口径",
        "snapshot_note": FORMAL_SNAPSHOT_NOTE,
        "snapshot_date": as_of(),
        "source_tables": source_tables(sql),
        "columns": columns_override or columns,
        "rows": rows,
        "row_count": len(rows),
        "has_more": has_more,
    }
    if extra:
        result.update(extra)
    return result


def dispatch(tool: str, **kwargs: Any) -> dict[str, Any] | None:
    """把一次工具调用映射到正式源模板;未迁移的组合返回 ``None``。"""
    if not enabled():
        return None
    handler = _HANDLERS.get(tool)
    if handler is None:
        return None
    try:
        return handler(kwargs)
    except ValueError as exc:
        # 参数不满足口径(缺 year、看板码不在域内……):直接按契约报错,不落到演示路径
        return {"ok": False, "error": {"code": "invalid_argument", "message": str(exc)}}
    except PermissionError as exc:
        # 可选表未在本次授权范围内:显式说明不可答。**不回落演示路径** ——
        # 回落会去连演示 MySQL,把"没有权限"变成"另一个数据源的答案"。
        return {"ok": False, "error": {"code": "table_not_granted", "message": str(exc)}}


# ---- 各工具的映射 -----------------------------------------------------------


def _task_query(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_task_query:检索已发布任务(关键词 / 分类 / 负责人 / 状态 / 项目组 / 看板)。"""
    board = (args.get("board") or "").strip() or None
    status = (args.get("status") or "").strip()
    if status and status not in ("0", "1", "2", "3"):
        return None  # 演示路径会给出 invalid_status,交给它
    limit = int(args.get("limit") or MAX_ROWS)
    sql, params = tpl.task_search(
        board,
        keyword=(args.get("keyword") or "").strip() or None,
        category_name=(args.get("category") or "").strip() or None,
        person=(args.get("owner") or "").strip() or None,
        status=int(status) if status else None,
        project_group=(args.get("project_group") or "").strip() or None,
        limit=limit,
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            f"{adm.BUSINESS_STATUS_NOTE};只列已发布任务(workflow_status = 'published' 且 is_deleted = 0);"
            "负责人按「姓名子串或 OA id 精确」匹配(多值以「、」连接);清单封顶 "
            f"{MAX_ROWS} 行"
        ),
        limit=limit,
        cap_last_param=True,
    )


def _coverage(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_progress_coverage:具名 scope,以及 scope=text_check 的三条文本规则。"""
    scope = (args.get("scope") or "summary").strip().lower()
    limit = int(args.get("limit") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    if scope == "text_check":
        rule = (args.get("rule") or "").strip().lower()
        if rule not in tpl.TEXT_RULES:
            return None
        # 演示工具的 task= 接受"任务 id 或任务名";这里按同样约定解析,
        # 否则会把"某个任务的冲突"答成"任意任务的冲突"(端到端验证抓到的缺口)。
        raw_task = (args.get("task") or "").strip()
        task_id = int(raw_task) if raw_task.isdigit() else None
        task_name = raw_task if raw_task and task_id is None else None
        sql, params = tpl.text_check(
            rule,
            board_code=board,
            task_id=task_id,
            task_name=task_name,
            keyword=(args.get("keyword") or "").strip() or None,
            all_versions=bool(args.get("all_versions")),
            limit=limit,
        )
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                "规则固化在服务端,模型只需照抄结果;默认扫每任务最新一期已发布进展"
                "(all_versions=true 扫全部已发布轮次);进展正文同时取技术组 task_progress 与"
                "集团组 task_group_progress_history 两张表;文本抽取≠结构化指标,只作待核实清单"
            ),
            limit=limit,
            cap_last_param=True,
        )
    if scope not in tpl.COVERAGE_SCOPES:
        return None
    sql, params = tpl.coverage_stats(
        scope,
        board_code=board,
        project_group=(args.get("project_group") or "").strip() or None,
        group_history_granted=optional_granted("task_group_progress_history"),
        limit=limit,
    )
    listing = scope in ("never_reported", "unpublished_by_task", "pending_review", "version_gaps")
    result = envelope(
        sql=sql,
        params=params,
        caliber=f"scope={scope};正式任务门 = is_deleted = 0 AND workflow_status = 'published'",
        limit=limit,
        cap_last_param=listing,
    )
    if scope == "never_reported":
        # 两个都成立的口径一起给:55 = task_progress 里没有已发布行(含集团板全部 46 条),
        # 9 = 两张表都没报过。只给一个数,另一类问题会被它答掉
        tsql, tparams = tpl.never_reported_totals(
            board_code=board, project_group=(args.get("project_group") or "").strip() or None
        )
        totals = envelope(sql=tsql, params=tparams, caliber="never_reported 两个口径", limit=1)
        first = (totals.get("rows") or [{}])[0]
        result["total"] = first.get("total")
        result["both_empty"] = first.get("both_empty")
        result["caliber"] += (
            ";total = task_progress 里没有已发布进展行的任务数(含集团板 46 条,它们的成效写在"
            "task_group_progress_history);both_empty = 两张表都没报过的('谁真的没报过'),"
            "两个数各自回答不同的问题,问哪个报哪个"
        )
        if not optional_granted("task_group_progress_history"):
            result["caliber"] += ";集团历史表未授权,has_group_history 列不出现"
    return result


def _freshness(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_freshness_distribution:分档 / 任意窗口 / 滞后与活跃清单 / 分组占比 / 漂移。

    演示工具把"分档"与"总览"合并在一个信封里返回,这里同样把两次查询合成一个信封
    (rows = 分档,另给最新进展 / 滞后天数 / 任务总数),调用方无感。
    """
    limit = int(args.get("limit") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    in_flight = bool(args.get("in_flight"))
    within_days = int(args.get("within_days") or 0)
    stale_days = int(args.get("stale_days") or 0)
    recent_days = int(args.get("recent_days") or 0)
    reported_only = bool(args.get("reported_only"))
    by = (args.get("by") or "").strip().lower()
    lag_bands = bool(args.get("lag_bands"))
    if args.get("task"):
        return None  # 单任务档(含任务名解析)交给演示路径

    if args.get("drift"):
        sql, params = tpl.latest_progress_drift(board_code=board, limit=limit)
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                "漂移检查:task.latest_progress_time 是发布时同步的冗余列,可能与真实最新已发布进展不一致;"
                "不一致时不得用该冗余列回答新鲜度。两个方向都算:冗余列偏早(进展比它新)与偏晚都在内,"
                "所以这是漂移清单而不是漏报清单"
            ),
            limit=limit,
            cap_last_param=True,
        )

    if lag_bands:
        # 分档必须在服务端算:把清单交给模型自己数天数分桶,边界那几条必错
        sql, params = tpl.freshness_lag_bands(
            as_of(), group_history_granted=optional_granted("task_group_progress_history"), limit=limit
        )
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                f"基准日 {as_of()};分档为 0-7 / 8-14 / 15-30 / 超过 30 / 无正式进展,"
                "各看板内相加等于该看板正式任务数;按看板各自的正式进展表算:技术组取 "
                "task_progress.progress_date、集团组取 task_group_progress_history.report_time,"
                "都只算 is_published = 1。问某个看板'周报有多陈旧'就报该看板的这几档,"
                "不要只报一个最新时间点"
            ),
            limit=MAX_ROWS,
            cap_last_param=True,
        )

    if by and by not in tpl.STALE_AXES:
        return None  # 未知分组轴交给演示路径报错

    if stale_days or recent_days or by:
        if stale_days and recent_days:
            return None  # 两端同时给:交给演示路径(它按 stale_days 优先)
        if by and not (stale_days or recent_days):
            # 只给 by 会被静默当成"全量分档",分组轴连同问题一起丢掉 —— 明确说该带哪个参数
            return {
                "ok": False,
                "error": {
                    "code": "invalid_argument",
                    "message": (
                        f"by={by} 需要与 stale_days 或 recent_days 同用:分组档回的是各组滞后/活跃的条数与占比,"
                        "必须先有天数才有口径。问「哪个组滞后占比最高」传 stale_days=90,"
                        "问「各组近 N 天活跃度」传 recent_days=90;只要分档桶(30/90/180/从未)请不要传 by。"
                    ),
                },
            }
        days = int(stale_days or recent_days)
        if days <= 0:
            return None
        if recent_days and not by:
            # 不加 status 闸门:问的是"有没有报进展",不是"任务在不在办"
            sql, params = tpl.recent_reporters(as_of(), days, board_code=board, limit=limit)
            return envelope(
                sql=sql,
                params=params,
                caliber=(
                    f"仅 latest_progress_time 落在基准日 {as_of()} 前 {days} 天内的任务;"
                    "不加 status 过滤(问的是有无上报,不是是否在办);按上报时间倒序;"
                    "days_since = 基准日 - latest_progress_time(按日期相减)"
                ),
                limit=limit,
                cap_last_param=True,
            )
        if by:
            recent_end = bool(recent_days)
            sql, params = tpl.stale_grouped(
                as_of(), days, by, recent_end, board_code=board, in_flight_only=in_flight, limit=limit
            )
            result = envelope(
                sql=sql,
                params=params,
                caliber=(
                    "在办即 status IN (0, 1)(0 未开始同样在办);"
                    + (
                        f"活跃 = latest_progress_time 落在近 {days} 天内,其余(含从未上报)算滞后"
                        if recent_end
                        else f"滞后超过 {days} 天,含从未上报(latest_progress_time 为 NULL)"
                    )
                    + ";total 是该组分母,stale_pct = stale_count / total,"
                    "active_count / active_pct 是同一分母下报过进展的那一侧(两者互补,相加为 100);"
                    "占比由服务端算,不要拿滞后条数跟别处的任务数手工相除;"
                    + (
                        f"本次按 recent_days={days} 问的是活跃那一端,已按 active_pct 倒序,首行即活跃占比最高的组"
                        if recent_end
                        else "问「占比最高的组」按 stale_pct 排序的首行答,条数最多的那组未必占比最高"
                    )
                ),
                limit=limit,
                cap_last_param=True,
            )
            tsql, tparams = tpl.stale_group_totals(as_of(), days, by, board_code=board, in_flight_only=in_flight)
            totals = envelope(sql=tsql, params=tparams, caliber="分组合计", limit=1)
            result["totals"] = (totals.get("rows") or [{}])[0]
            return result
        sql, params = tpl.stale_tasks(
            as_of(), days, board_code=board, in_flight_only=True, reported_only=reported_only, limit=limit
        )
        result = envelope(
            sql=sql,
            params=params,
            caliber=(
                f"滞后超过 {days} 天,含从未上报(latest_progress_time 为 NULL);"
                "在办即 status IN (0, 1);从未报过的排在最前,其 days_since 为空"
                + (
                    ";已排除从未上报的任务(它们没有天数可比),行首即最久未上报的那条"
                    if reported_only
                    else ";问「最久没上报的前 N 条」时应加 reported_only=true 把无天数可比的排除"
                )
            ),
            limit=limit,
            cap_last_param=True,
        )
        tsql, tparams = tpl.stale_totals(as_of(), days, board_code=board, in_flight_only=True)
        totals = envelope(sql=tsql, params=tparams, caliber="滞后总数自检", limit=1)
        first = (totals.get("rows") or [{}])[0]
        result["total_count"] = first.get("total_count")
        result["never_reported_count"] = first.get("never_reported_count")
        return result

    if within_days > 0:
        sql, params = tpl.freshness_within(as_of(), within_days, board_code=board)
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                f"基准日 {as_of()} 前 {within_days} 天窗内报过进展的任务数"
                "(相对窗口以基准日为准,不用系统当前时间);days_behind = 基准日 - 最新进展日"
            ),
            limit=1,
        )

    sql, params = tpl.freshness_distribution(as_of(), board_code=board, in_flight_only=in_flight)
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            f"分档以数据基准日 {as_of()} 为准(非系统当前时间);"
            "「从未报进展」按 task.latest_progress_time 是否为空判,与 never_reported 的判据不同;"
            + ("仅统计在办任务(status 0/1)" if in_flight else "含全部正式任务")
        ),
        limit=MAX_ROWS,
    )
    osql, oparams = tpl.freshness_overall(as_of(), board_code=board, in_flight_only=in_flight)
    overall = envelope(sql=osql, params=oparams, caliber="新鲜度总览", limit=1)
    first = (overall.get("rows") or [{}])[0]
    result["newest_progress"] = first.get("newest_progress")
    result["days_behind"] = first.get("days_behind")
    result["task_total"] = first.get("task_total")
    result["bucket_sum"] = sum(int(r.get("task_count") or 0) for r in result["rows"])
    return result


def optional_granted(name: str) -> bool:
    """四张可选表是否在本次只读授权范围内。

    默认**未授权**:国数方说明里必开的是 8 张表,四张可选表(附件 / 集团历史 /
    审批动作 / 导入批次)按是否补开而定;宁可显式说明"不可答",也不去查一张
    没有权限的表然后报权限错。
    环境变量 ``TASK_BOARD_GRANTED_OPTIONAL_TABLES`` 用逗号列已补开的表名。
    """
    raw = os.environ.get("TASK_BOARD_GRANTED_OPTIONAL_TABLES", "")
    granted = {item.strip() for item in raw.split(",") if item.strip()}
    return name in granted


def _resolve_task(raw: str) -> tuple[int | None, str | None]:
    """把工具的 task= 参数(id 或名字)解析成 task_id / task_name。"""
    value = (raw or "").strip()
    if not value:
        return None, None
    return (int(value), None) if value.isdigit() else (None, value)


def _attachment(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_attachment_query:附件**元数据**清单(storage_path 永不出现)。"""
    limit = int(args.get("limit") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    task_id, _task_name = _resolve_task(args.get("task") or "")
    sql, params = tpl.attachment_list(
        board_code=board, task_id=task_id, granted=optional_granted("task_attachment"), limit=limit
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "is_deleted = 0;storage_path 禁止外泄,不在返回字段内;"
            "file_size 单位是字节,原样报出,不要换算成 KB/MB 也不要写「约」;"
            "附件内容读不到,只能说明「存在附件《文件名》」"
        ),
        limit=limit,
        cap_last_param=True,
    )


def _year_goal(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_year_goal_query:年度目标行清单(year=0 表示所有年度)。"""
    limit = int(args.get("limit") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    year = int(args.get("year") or 0)
    task_id, _task_name = _resolve_task(args.get("task") or "")
    sql, params = tpl.year_goal_rows(board_code=board, year=year or None, task_id=task_id, limit=limit)
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "task_year_goal 一 (task, year) 一行;只列已发布任务;"
            + (f"year={year}" if year else "未限定年份,即该范围内所有年度的目标行")
        ),
        limit=limit,
        cap_last_param=True,
    )


def _milestone(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_milestone_query:里程碑清单(必须支持按任务收窄)。"""
    limit = int(args.get("limit") or MAX_ROWS)
    raw_year = (args.get("year") or "").strip()
    raw_status = (args.get("status") or "").strip()
    if raw_status and raw_status not in ("0", "1"):
        return None  # 演示路径会报 invalid_status
    task_id, _task_name = _resolve_task(args.get("task") or "")
    sql, params = tpl.milestone_list(
        None,
        year=raw_year or None,
        status=int(raw_status) if raw_status else None,
        task_id=task_id,
        limit=limit,
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "关联任务已发布且成果项 is_deleted = 0;status 0=未完成 1=已完成;"
            "「某任务有哪些里程碑」必须带 task=,否则答案落在整个看板的第一页"
        ),
        limit=limit,
        cap_last_param=True,
    )


_NEW_HANDLERS = {
    "weekly_attachment_query": _attachment,
    "weekly_year_goal_query": _year_goal,
    "weekly_milestone_query": _milestone,
}


def _owner_roles(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_owner_roles:某人的主责 / 项目负责人 / 牵头领导 / 去重并集。"""
    person = (args.get("person") or "").strip()
    if not person:
        return None  # 演示路径会给出 invalid_argument
    sql, params = tpl.owner_roles(person)
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "正式任务门 = is_deleted = 0 AND workflow_status = 'published';"
            "多值列去空格后精确匹配(R-13),id 与姓名都接受;any_role 是三角色去重并集"
        ),
        limit=1,
    )


def _group_detail(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_group_detail_query:集团板扩展表明细。"""
    limit = int(args.get("limit") or MAX_ROWS)
    task_id, _task_name = _resolve_task(args.get("task") or "")
    raw_status = (args.get("status") or "").strip()
    if raw_status and raw_status not in ("0", "1", "2", "3"):
        return None
    non_empty = tuple(column.strip() for column in (args.get("non_empty") or "").split(",") if column.strip())
    contains = (args.get("contains") or "").strip() or None
    contains_field = (args.get("field") or "").strip() or None
    fields = tuple(name.strip() for name in (args.get("fields") or "").split(",") if name.strip())
    sql, params = tpl.group_detail_list(
        board_code="group" if not args.get("board") else (args.get("board") or "").strip(),
        task_id=task_id,
        status=int(raw_status) if raw_status else None,
        non_empty=non_empty,
        contains=contains,
        contains_field=contains_field,
        fields=fields,
        limit=limit,
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "task_group_detail 与 task 是 1:1,只有集团看板任务有行;"
            "completion_time 是展示文本,按文本匹配、不做日期运算(R-12);"
            "status 在 task 上,「状态与成效矛盾」必须两边一起判"
        ),
        limit=limit,
        cap_last_param=True,
    )


def _health(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_health:逐表精确行数(正式源版本)。"""
    sql, params = tpl.table_row_counts()
    result = envelope(
        sql=sql,
        params=params,
        caliber="逐表 count(*);未授权或不存在的表返回 NULL(四张可选表可能在授权范围外)",
        limit=200,
    )
    rows = result["rows"]
    present = [r for r in rows if r["row_count"] is not None]
    result["store"] = _pg.dsn() if _pg is not None else "unknown"
    result["table_count"] = len(present)
    result["total_rows"] = sum(int(r["row_count"]) for r in present)
    result["row_counts"] = {r["table_name"]: r["row_count"] for r in rows}
    return result


_NEW_HANDLERS_6 = {
    "weekly_owner_roles": _owner_roles,
    "weekly_group_detail_query": _group_detail,
    "weekly_health": _health,
}


def _submission(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_submission_query:提交单聚合各 scope。

    只迁移**纯聚合**请求:带 task / reporter / status / exclude_status / status_mismatch
    的是明细筛选,语义与聚合不同,交给演示路径处理(返回 None),不猜。
    """
    scope = (args.get("scope") or "").strip().lower()
    if scope not in tpl.SUBMISSION_SCOPES:
        return None
    for key in ("task", "reporter", "status", "exclude_status"):
        if (args.get(key) or "").strip():
            return None
    if args.get("status_mismatch"):
        return None
    limit = int(args.get("limit") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    sql, params = tpl.submission_stats(scope, board_code=board, limit=limit)
    listing = scope in ("pending_review", "unpublished_by_task")
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            f"scope={scope};提交单域只加 t.is_deleted = 0(462 = 470 - 8 个软删任务下的单),"
            "不带任务发布门;看板在 task 上,按看板提问从任务侧下推;"
            "动作流水里的 rejected 是动作条数,不能当驳回率的分子"
        ),
        limit=limit,
        cap_last_param=listing,
    )


def _workflow(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_workflow_query:审批动作流水(可选表)。意见列按权限返回。"""
    scope = (args.get("scope") or "").strip().lower()
    if scope not in tpl.WORKFLOW_SCOPES:
        # 默认清单按 task id 排序、by_task 是逐任务聚合:都不迁移,交给演示路径
        return None
    limit = int(args.get("limit") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    task_id, _task_name = _resolve_task(args.get("task") or "")
    raw_action = (args.get("action") or "").strip() or None
    sql, params = tpl.workflow_actions(
        scope,
        board_code=board,
        task_id=task_id,
        action=raw_action,
        granted=optional_granted("task_workflow_action"),
        limit=limit,
    )
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            "流水带 t.is_deleted = 0 是正确闸门(1,578 行);再加任务发布门会掉到 1,519;"
            "opinion(审批意见)始终在列里,无权限时值打码成「[按权限不展示]」"
            "(与参考实现的 _scrub 同一语义:藏列会让调用方分不清「没有意见」与「没权限看」);"
            "scope=recent 按动作自身时间倒序(默认清单按 task id 排序,答不了「最近谁被驳回」)"
        ),
        limit=limit,
        cap_last_param=scope == "recent",
    )
    if not bool(args.get("can_read_sensitive")):
        # 敏感字段打码而不是删列,列集合在两种权限下保持一致
        for row in result["rows"]:
            if "opinion" in row:
                row["opinion"] = "[按权限不展示]"
    return result


_NEW_HANDLERS_7 = {
    "weekly_submission_query": _submission,
    "weekly_workflow_query": _workflow,
}


def _scale(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_scale:规模 / 完整度 / 强度三种横截面。"""
    sql, params = tpl.scale_cross_section(
        by=(args.get("by") or "board"),
        mode=(args.get("mode") or "totals"),
        year=int(args.get("year") or 2026),
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "正式任务门 = is_deleted = 0 AND workflow_status = 'published';"
            "totals 的三张子表同时 JOIN,故每个计数都按主键去重(各组里程碑相加应等于全库总数,"
            "比总数大就是被 JOIN 放大了);completeness 的 has_* 是「有该项的任务数」而非子表条数;"
            "intensity 的分母是任务数且保留零期任务"
        ),
        limit=MAX_ROWS,
    )


_NEW_HANDLERS_8 = {"weekly_scale": _scale}


def _rank(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_rank:任务排名(cut / keep_ties / per_group)。"""
    metric = (args.get("metric") or "progress_rounds").strip()
    optional = tuple(tpl.RANK_METRICS.get(metric, ("", "", "", "", None))[4:5])
    optional_table = optional[0] if optional else None
    granted = (optional_table,) if (optional_table and optional_granted(optional_table)) else ()
    mode = (args.get("mode") or "cut").strip()
    top = int(args.get("top") or 5)
    sql, params = tpl.rank_tasks(
        metric=metric,
        mode=mode,
        top=top,
        ascending=bool(args.get("ascending")),
        group_by=(args.get("group_by") or "").strip() or None,
        board_code=(args.get("board") or "").strip() or None,
        granted_optional=granted,
    )
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            "正式任务门 = is_deleted = 0 AND workflow_status = 'published';"
            "cut 硬切前 N 条(并列按 task id),keep_ties 用 RANK() 保留并列(行数通常大于 N),"
            "per_group 每组第一(top 无意义);各度量走 LEFT JOIN,零值任务保留;"
            "project_team_size 数的是 task 行上 project_owner_name 的三种分隔符(、 , ;),"
            "零负责人(空串或 NULL)的任务不算 1 人团队,已排除;"
            "cut 档的 total_count 是符合口径的任务总数(不是本次行数),"
            "tied_at_top 是与首行同值的任务数(升序时即最小值那一侧的并列)——"
            "硬切在并列上的取舍是任意的,并列数大于 1 时不要把它念成唯一的第一名"
        ),
        limit=top if mode == "cut" else MAX_ROWS,
        cap_last_param=mode == "cut",
    )
    if mode == "cut":
        # 演示源的 cut 分支把「符合口径的任务总数」放在**顶层**(不是行内列):
        # 两边键位必须一致,否则同一个问题换数据源就得换字段读,而模型只会读它
        # 在演示源下学会的那个键。SQL 里仍由 count(*) OVER () 一次算出,不多查一遍。
        # tie_count 同理提成 tied_at_top(演示源没有这一项,是**加法**,不改键位)。
        totals = [row.pop("total_count", None) for row in result["rows"]]
        ties = [row.pop("tie_count", None) for row in result["rows"]]
        result["columns"] = [c for c in result["columns"] if c not in ("total_count", "tie_count")]
        result["total_count"] = totals[0] if totals else None
        result["tied_at_top"] = ties[0] if ties else None
    return result


_NEW_HANDLERS_9 = {"weekly_rank": _rank}


def _person_stats(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_person_stats:人员统计(9 个已迁移 scope)。"""
    scope = (args.get("scope") or "workload").strip().lower()
    if scope not in tpl.PERSON_SCOPES:
        return None  # 其余 scope(id_variants/id_longest/reviewers/self_review...)交给演示路径
    role = (args.get("role") or "lead_owner").strip() or "lead_owner"
    top = int(args.get("top") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    group = (args.get("project_group") or "").strip() or None
    sql, params = tpl.person_stats(scope, role=role, project_group=group, board_code=board, top=top)
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            f"{adm.BUSINESS_STATUS_NOTE};按「{tpl.PERSON_ROLES.get(role, ('', role, ''))[1]}」分组;"
            "姓名为空的行不计入人头;workload 是硬切(并列被切掉),workload_top 用 HAVING = MAX 保留并列;"
            "workload_summary 的均值是全局均值;group_roster 数去重后的人(不是任务条数);"
            "id_format 只统计有标识的任务;reporters 走任务闸门 + 进展行发布闸门两道"
        ),
        limit=top,
        cap_last_param=scope
        in ("workload", "single_task", "group_roster", "workload_top", "cross_group", "dual_role", "reporters"),
    )
    if scope == "workload":
        tsql, tparams = tpl.person_ties(role=role, board_code=board)
        ties = envelope(sql=tsql, params=tparams, caliber="并列自检", limit=1)
        first = (ties.get("rows") or [{}])[0]
        result["top_task_count"] = first.get("top_task_count")
        result["tied_at_top"] = first.get("tied_at_top")
    return result


_NEW_HANDLERS_10 = {"weekly_person_stats": _person_stats}


def _attachment_stats(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_attachment_stats:附件汇总 / 按扩展名分档(两种形状,不互相代答)。"""
    scope = (args.get("scope") or "summary").strip().lower()
    if scope not in ("summary", "by_ext"):
        return None  # 其余 scope(largest/by_uploader/by_month/deleted/orphan...)交给演示路径
    if (args.get("date_from") or "").strip():
        return None
    board = (args.get("board") or "").strip() or None
    limit = int(args.get("limit") or MAX_ROWS)
    sql, params = tpl.attachment_stats(
        board_code=board, scope=scope, granted=optional_granted("task_attachment"), limit=limit
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "is_deleted = 0 且关联任务已发布;file_size 单位是字节,原样报出(不要换算成 KB/MB 也不要写「约」);"
            "total_mb 只是同一数值的另一种表示,以字节为准;storage_path 禁止外泄,不在返回字段内;"
            + (
                "by_ext 按扩展名每档一行(ext / n / total_bytes / total_mb),答「哪种文件最多」看 n 的首行"
                if scope == "by_ext"
                else "summary 是一行汇总(条数 / 任务数 / 字节 / 均值 / 上传人 / 挂载点 / 四个主扩展名条数)"
            )
        ),
        limit=limit if scope == "by_ext" else 1,
        cap_last_param=scope == "by_ext",
    )


def _group_history(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_group_history:集团板进展历史(可选表,8 个 scope)。"""
    by = (args.get("by") or "").strip().lower()
    if by and by not in tpl.GROUP_HISTORY_SCOPES[1:]:
        return None  # 未知分组名交给演示路径报错
    scope = by or "rows"
    task_id, _task_name = _resolve_task(args.get("task") or "")
    limit = int(args.get("limit") or MAX_ROWS)
    granted = optional_granted("task_group_progress_history")
    window: dict[str, Any] = {
        "date_from": (args.get("date_from") or "").strip(),
        "date_to": (args.get("date_to") or "").strip(),
        "last_days": int(args.get("last_days") or 0),
        "last_months": int(args.get("last_months") or 0),
        "as_of": as_of(),
        "granted": granted,
    }
    task_id_arg: dict[str, Any] = {
        "task_id": task_id,
        "version_no": int(args.get("version_no") or 0) or None,
    }
    latest_only = bool(args.get("latest_only"))
    sql, params = tpl.group_history(
        scope,
        task_id=task_id_arg["task_id"],
        version_no=task_id_arg["version_no"],
        latest_only=latest_only,
        limit=limit,
        **window,
    )
    caliber = (
        "集团板的进展在本表里,task_progress 一行都没有(所以进展类工具对集团任务返回空,入口在这里);"
        "两道闸门必须同时成立:任务正式(workflow_status = 'published')且行 is_published = 1 ——"
        "少任何一道都会把 42 条未审草稿算进来(演示数据 404 行 = 已发布 362 + 草稿 42)"
    )
    if scope == "linkage":
        caliber += (
            ";本档**故意不过**行闸门:问的是挂接率,分母是表内全部 404 行,"
            "linked_rows 按 workflow_submission_id 非空判定(0 即这张表不挂提交单,不是查不到)"
        )
    if scope == "lag":
        caliber += (
            f";lag_days = 基准日 {as_of()} 减最后一次上报日(MAX(report_time),不是最早一期),"
            "只含报过进展的任务,从未报过的不在榜上"
        )
    note = tpl.group_history_window_note(
        as_of(), window["date_from"], window["date_to"], window["last_days"], window["last_months"]
    )
    if note:
        caliber += ";" + note
    if latest_only:
        caliber += ";仅各任务最新一期已发布版本"
    result = envelope(
        sql=sql,
        params=params,
        caliber=caliber,
        limit=limit,
        cap_last_param=scope != "linkage",
    )
    if scope in ("rows", "lag"):
        # 计数题必须活过 200 行封顶:已发布 362 行会被报成"200 行且还有更多"。
        # task=/version_no 必须一起传:总数与明细同一口径是这条自检的全部意义
        totals_sql, totals_params = tpl.group_history_totals(
            task_id=task_id_arg["task_id"],
            version_no=task_id_arg["version_no"],
            latest_only=latest_only,
            **window,
        )
        totals = envelope(sql=totals_sql, params=totals_params, caliber="总数自检", limit=1)
        first = (totals.get("rows") or [{}])[0]
        result["total_count"] = first.get("total_rows")
        result["total_tasks"] = first.get("total_tasks")
    return result


def _group_owner(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_group_owner_query:集团板多值负责人(元素级精确匹配)。"""
    limit = int(args.get("limit") or MAX_ROWS)
    sql, params = tpl.group_owner(
        person=(args.get("person") or "").strip() or None,
        role=(args.get("role") or "lead"),
        limit=limit,
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "仅集团看板(task_board.code = 'group')且任务已发布;负责人是多值文本,"
            "匹配按元素精确(先统一顿号与逗号再切数组),不用 LIKE —— 短名会在长名里碰撞;"
            "lead 与 project 是不同角色、不同列,不可互换"
        ),
        limit=limit,
        cap_last_param=True,
    )


_NEW_HANDLERS_11 = {
    "weekly_attachment_stats": _attachment_stats,
    "weekly_group_history": _group_history,
    "weekly_group_owner_query": _group_owner,
}
# weekly_task_ranking 的 metric 名 -> (rank_tasks 的 metric 名, 该工具自己的中文标签)
# 标签照抄工具侧的地图而不是 RANK_METRICS 的:progress 在 `weekly_rank` 叫
# 「已发布进展期数」,在这个工具里叫「正式进展版本数」,同一个数在不同工具下
# 带不同标签是既有契约,统一改会让另一边的回答换词。
_RANKING_METRICS = {
    "attachments": ("attachments", "附件数"),
    "progress": ("progress_rounds", "正式进展版本数"),
    "milestones": ("milestones", "里程碑数"),
    "submissions": ("submissions", "审批提交单数"),
}


def _task_ranking(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_task_ranking:按子表条数排名(INNER JOIN,只列有记录的任务)。"""
    metric = (args.get("metric") or "attachments").strip()
    chosen = _RANKING_METRICS.get(metric)
    if chosen is None:
        return None
    mapped, label = chosen
    top = max(1, min(50, int(args.get("top") or 5)))
    optional = tpl.RANK_METRICS[mapped][4]
    granted = (optional,) if (optional and optional_granted(optional)) else ()
    sql, params = tpl.rank_tasks(metric=mapped, mode="cut", top=top, granted_optional=granted, shape="count")
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            "正式任务门 = is_deleted = 0 AND workflow_status = 'published';"
            "本档只列**有该子表记录**的任务(零条目的任务不参赛,与参考查询的 INNER JOIN 一致),"
            "按条数降序、并列按 task id 升序硬切前 N 条;"
            "tied_at_top 是与首行条数相同的任务数——并列数大于 1 时,榜首只是并列里的第一条,"
            "不要念成唯一的第一名(并列全列请用 weekly_rank 的 mode=keep_ties);"
            "附件度量需要 task_attachment 授权"
        ),
        limit=top,
        cap_last_param=True,
        extra={"metric": metric, "metric_label": label},
    )
    # 并列自检提到顶层:行内多一列会被当成分页信息,而它是"这个名次站了几个人"。
    ties = [row.pop("tie_count", None) for row in result["rows"]]
    result["columns"] = [c for c in result["columns"] if c != "tie_count"]
    result["tied_at_top"] = ties[0] if ties else None
    return result


def _progress_range(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_progress_range:时间轴上的正式进展(全任务或指定看板)。"""
    if (args.get("by") or "").strip() or args.get("peak"):
        return None  # 分档/峰值交给演示路径
    if (args.get("date_field") or "progress_date").strip() != "progress_date":
        return None
    date_from = (args.get("date_from") or "").strip()
    date_to = (args.get("date_to") or "").strip()
    last_days = int(args.get("last_days") or 0)
    if last_days < 0:
        # 演示源的 date_window 对非正数报 invalid_argument;负天数会反算出 lo > hi,
        # 静默返回 0 行 —— 那不是"窗口内没有进展",是窗口本身不成立。
        raise ValueError(f"last_days 必须为正数:{last_days}")
    if last_days:
        # 相对窗口以数据基准日为准,不用系统时间(与新鲜度同一口径)
        end = dt.date.fromisoformat(as_of())
        date_to = date_to or end.isoformat()
        date_from = (end - dt.timedelta(days=last_days)).isoformat()
    if not date_from or not date_to:
        return None
    for label, value in (("date_from", date_from), ("date_to", date_to)):
        if not _DATE_RE.match(value):
            # 不校验就会把 '2026/08/01' 直接喂给 PG,报出来的是驱动层的语法错而不是口径错
            raise ValueError(f"{label} 需为 YYYY-MM-DD:{value}")
    if date_from > date_to:
        raise ValueError(f"窗口起点晚于终点:{date_from} > {date_to}")
    limit = int(args.get("limit") or MAX_ROWS)
    sql, params = tpl.progress_range(None, date_from, date_to, limit=limit)
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            "只取正式展示版本(is_published = 1)且任务已发布;窗口两端都是闭区间;"
            f"相对窗口以数据基准日 {as_of()} 为基准,不用系统当前时间(last_days=N 时窗口是 "
            f"{as_of()} 往前数 N 天到基准日,含两端);"
            "lag_days = 上报日 - 周期日(补报更早周期时为正);"
            "集团看板的进展不在这张表里,用 weekly_group_history"
        ),
        limit=limit,
        cap_last_param=True,
        extra={"date_from": date_from, "date_to": date_to},
    )
    # 总数单独查一次(与参考实现同法):明细被 200 行截断后,调用方还原不出真值
    tsql, tparams = tpl.progress_range_totals(None, date_from, date_to)
    totals = envelope(sql=tsql, params=tparams, caliber="窗口总数", limit=1)
    first = (totals.get("rows") or [{}])[0]
    result["total_count"] = first.get("total_rows")
    result["total_tasks"] = first.get("total_tasks")
    span = (dt.date.fromisoformat(date_to) - dt.date.fromisoformat(date_from)).days
    if not result["total_count"] and span < 15:
        # 0 行有两种完全不同的原因。不点明,task_progress 按月上报这件事会让
        # "最近一周"被答成"没有任务更新进展"——参考实现为此吃了 6 轮返工。
        result["caliber"] += _short_window_hint()
    return result


def _short_window_hint() -> str:
    """短窗口 0 行时的口径补充,数字现查(不写死演示数据的那批日期)。"""
    sql, params = tpl.published_progress_recency(None)
    recency = envelope(sql=sql, params=params, caliber="短窗口自检", limit=1)
    first = (recency.get("rows") or [{}])[0]
    latest = first.get("latest_progress_date") or "未知"
    rows = first.get("published_rows") or 0
    fsql, fparams = tpl.freshness_within(as_of(), 7)
    fresh = envelope(sql=fsql, params=fparams, caliber="近 7 天更新自检", limit=1)
    # 列名跟参考实现走:就近 7 天这一档叫 task_count(freshness_within 已改造)
    recent = ((fresh.get("rows") or [{}])[0]).get("task_count") or 0
    gap = "半月上下"
    if latest != "未知":
        gap = f"{(dt.date.fromisoformat(as_of()) - dt.date.fromisoformat(latest)).days} 天"
    return (
        # 全角分号按转义序列写出:它是拼接语气的一部分(与参考实现的提示同形),
        # 直接写字面量会触发 RUF001,而换成半角就不是同一段文案了。
        f"\uff1b本次窗口内 0 行不是「没人报进展」:进展行按月上报,全库正式进展 {rows} 行,"
        f"最大 progress_date 是 {latest}(距基准日 {gap}),任何短于半月的窗口在这张表上必然为空。"
        "问「最近一周哪些任务更新了进展」问的是任务上的 latest_progress_time(逐条更新),"
        f"请改用 weekly_freshness_distribution recent_days=7(得 {recent} 条);"
        "也不要退而报「最新一批进展」的期数,那是另一个问题"
    )


def _milestone_stats(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_milestone_stats:里程碑统计(6 个 scope x 10 个维度)。

    三条判据的由来见 ``tpl.milestone_stats`` 上方那段注释。``per_task`` 在演示源里
    是**三个信封**(逐任务清单 + 总览 + 并列数)拼出来的,这里同样合成一个 ——
    ``summary`` 是总览那一行(不是行数组),``top_tie_count`` 是并列档条数。

    参数名跟工具契约走:行数上限叫 ``top``(不是 ``limit``)。
    """
    scope = (args.get("scope") or "summary").strip().lower()
    if scope not in tpl.MILESTONE_STATS_SCOPES:
        return None  # 演示路径会报 unsupported_scope
    by = (args.get("by") or "category").strip().lower()
    if scope == "by_dimension" and by not in tpl.MILESTONE_DIMENSIONS:
        return None  # 演示路径会报 unsupported_group_by
    kind = (args.get("kind") or "task_done_milestones_open").strip().lower()
    if scope == "mismatch" and kind not in tpl.MILESTONE_MISMATCH_KINDS:
        return None
    year = int(args.get("year") or 0) or None
    category = (args.get("category") or "").strip() or None
    min_total = max(0, int(args.get("min_total") or 0))
    top = max(1, min(MAX_ROWS, int(args.get("top") or 8)))

    gate_note = "m.is_deleted = 0 且关联任务满足 is_deleted = 0 AND workflow_status = 'published'"
    status_note = "m.status 是 0/1 两值码:1 已完成、0 未完成,「已完成」只认 status = 1"
    span = []
    if year:
        span.append(f"仅 {year} 年度里程碑")
    if category:
        span.append(f"仅类别「{category}」")
    span_note = (";" + ";".join(span)) if span else ""

    if scope == "per_task":
        # 逐任务清单:LEFT JOIN,零里程碑任务保留为 0(inner join 会把它们整行抹掉,
        # 而覆盖率的分母正是它们),年度/类别条件挂在 JOIN 的 ON 上,见模板注释。
        sql, params = tpl.milestone_per_task_rows(year, category, limit=top)
        result = envelope(
            sql=sql,
            params=params,
            caliber=(
                f"is_deleted = 0 AND workflow_status = 'published';LEFT JOIN 保留零里程碑任务;"
                f"{status_note}{span_note}"
            ),
            limit=top,
            cap_last_param=True,
            extra={"scope": scope},
        )
        ssql, sparams = tpl.milestone_per_task_summary(year, category)
        summary = envelope(sql=ssql, params=sparams, caliber="per_task 总览", limit=1)
        # 总览是一行(不是行数组),与演示源同形:调用方读 result["summary"]["coverage_pct"]。
        result["summary"] = (summary.get("rows") or [{}])[0]
        tsql, tparams = tpl.milestone_per_task_ties(year, category)
        ties = envelope(sql=tsql, params=tparams, caliber="per_task 并列档", limit=1)
        tied_at_top = (ties.get("rows") or [{}])[0].get("tied_at_top")
        result["top_tie_count"] = tied_at_top
        head = (result.get("rows") or [{}])[0]
        top_n = head.get("milestones")
        top_task = f"任务 {head.get('task_id')} {head.get('task_name')}" if head else "首行"
        result["caliber"] += (
            ";按里程碑数降序、并列按 task id 升序(与其他榜单同一套定序键);"
            f"最多那档有 {tied_at_top} 条任务并列(各 {top_n} 个),"
            f"问「最多的是哪条」取首行一条({top_task}),"
            "要把并列都报出来请说明是并列,不要当成几十个独立答案;"
            "零里程碑任务不等于「从没建过里程碑」:里程碑被全部软删的任务(scope=fully_deleted)"
            "在这里同样一条不剩,两个问句会指向同一批任务"
        )
        return result

    sql, params = tpl.milestone_stats(
        scope, by=by, year=year, category=category, min_total=min_total, kind=kind, limit=top
    )
    caliber = f"{gate_note};{status_note}{span_note}"
    if scope == "deleted":
        # 这是唯一不套任务闸门的 scope:问的是表本身被软删了多少行,
        # 按任务过滤会少算。有删的任务 23 条、删干净的只有 3 条 —— 两个数
        # 回答的是不同问题,`deleted` 那三个数答不了 fully_deleted 的问句。
        caliber = (
            "全表口径(不加任务闸门):这是关于表的问题,按任务过滤会少算;"
            "问「哪些任务的里程碑被全部删掉了」用 scope=fully_deleted,"
            "这三个数答不了那个问题(有删的任务共 23 条,全删的只有 3 条)"
        )
    elif scope == "fully_deleted":
        caliber = (
            "is_deleted = 0 AND workflow_status = 'published';「全部删掉」按 NOT EXISTS 未删里程碑判,"
            "不是「删过里程碑」——删过的任务有 23 条,删干净的只有这 3 条;"
            "deleted_milestones 是该任务被删的里程碑数;"
            "行数为 0 才是「没有任务被全删」,这是结论本身,不要换口径重算"
        )
    elif scope == "by_dimension":
        dimension = tpl.MILESTONE_DIMENSIONS[by][1]
        caliber += f";按{dimension}分组"
        if min_total:
            caliber += f";仅保留计数不少于 {min_total} 的分组(边界取等)"
        if by == "primary_category":
            caliber += (
                ";一级分类取 t.category_id 的父级(任务分类只到二级,往上跳一层),"
                "与里程碑自己的 m.category 文本不是同一个维度 —— 问「哪个一级分类完成率最高」"
                "必须用这个轴,用 by=category 会答成里程碑类别;本轴按 finish_rate_pct 降序,"
                "首行即最高;小样本分类会把比率抬高,要设门槛请加 min_total"
            )
        elif by == "project_group":
            caliber += (
                ";项目组取 t.project_group(任务上的列),与 by=group_name 的里程碑承担组短名"
                "不是一个轴,取值集合都不一样(这里是 11 个项目组,那里是 区域组/安全组 等 6 个短名)"
                "—— 问「哪些项目组的里程碑完成比例高 / 低」用本轴;本轴按 finish_rate_pct 降序,"
                "首行最高、末行最低;这只是填报状态,不能当项目组绩效"
            )
    elif scope == "mismatch":
        label = "任务已完成但里程碑未全完成" if kind == "task_done_milestones_open" else "里程碑全完成但任务仍在办"
        quantifier = (
            "存在量词(有任一里程碑未完成即入选):限定年度只会漏掉矛盾,跨年度的未完成里程碑"
            "是更硬的矛盾,默认不限年度的 6 项才是全量"
            if kind == "task_done_milestones_open"
            else "全称量词(全部里程碑都已完成才入选):限定年度会放宽条件而非收紧,"
            "限 2026 得 22 项、不限得 8 项,多出的那些尚有 2025 年里程碑未完成,"
            "答「进行中但里程碑都完成了」要说明是哪一种"
        )
        span_extra = "" if year else "比对该任务的全部年度里程碑(2025 与 2026)"
        caliber = ";".join([caliber, f"{label}(task.status 2 已完成 / 1 进行中)", span_extra, quantifier])
    return envelope(
        sql=sql,
        params=params,
        caliber=caliber,
        limit=top,
        cap_last_param=scope in ("by_dimension", "fully_deleted", "mismatch"),
        extra={"scope": scope},
    )


_NEW_HANDLERS_12 = {
    "weekly_task_ranking": _task_ranking,
    "weekly_progress_range": _progress_range,
    "weekly_milestone_stats": _milestone_stats,
}

_HANDLERS = {
    "weekly_task_query": _task_query,
    "weekly_progress_coverage": _coverage,
    "weekly_freshness_distribution": _freshness,
    "weekly_attachment_query": _attachment,
    "weekly_year_goal_query": _year_goal,
    "weekly_milestone_query": _milestone,
    "weekly_owner_roles": _owner_roles,
    "weekly_group_detail_query": _group_detail,
    "weekly_health": _health,
    "weekly_submission_query": _submission,
    "weekly_workflow_query": _workflow,
    "weekly_scale": _scale,
    "weekly_rank": _rank,
    "weekly_person_stats": _person_stats,
    "weekly_attachment_stats": _attachment_stats,
    "weekly_group_history": _group_history,
    "weekly_group_owner_query": _group_owner,
    "weekly_task_ranking": _task_ranking,
    "weekly_progress_range": _progress_range,
    "weekly_milestone_stats": _milestone_stats,
}

# _NEW_HANDLERS 只是构建期的清单,避免手工漏接线
assert set(_NEW_HANDLERS) <= set(_HANDLERS)
