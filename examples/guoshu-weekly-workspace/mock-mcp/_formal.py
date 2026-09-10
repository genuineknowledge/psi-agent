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
        limit=limit,
    )
    listing = scope in ("never_reported", "unpublished_by_task", "pending_review", "version_gaps")
    return envelope(
        sql=sql,
        params=params,
        caliber=f"scope={scope};正式任务门 = is_deleted = 0 AND workflow_status = 'published'",
        limit=limit,
        cap_last_param=listing,
    )


def _freshness(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_freshness_distribution:分档 / 任意窗口 / 滞后清单 / 漂移检查。

    演示工具把「分档」与「总览」合并在一个信封里返回,这里同样把两次查询合成一个信封
    (rows = 分档,另给最新进展 / 滞后天数 / 任务总数),调用方无感。
    """
    limit = int(args.get("limit") or MAX_ROWS)
    board = (args.get("board") or "").strip() or None
    in_flight = bool(args.get("in_flight"))
    within_days = int(args.get("within_days") or 0)
    stale_days = int(args.get("stale_days") or 0)
    if args.get("by") or args.get("lag_bands") or args.get("reported_only") or args.get("recent_days"):
        return None
    if args.get("task"):
        return None

    if args.get("drift"):
        sql, params = tpl.latest_progress_drift(board_code=board, limit=limit)
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                "漂移检查:task.latest_progress_time 是发布时同步的冗余列,可能与真实最新已发布进展不一致;"
                "不一致时不得用该冗余列回答新鲜度"
            ),
            limit=limit,
            cap_last_param=True,
        )
    if within_days > 0:
        sql, params = tpl.freshness_within(as_of(), within_days, board_code=board)
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                f"基准日 {as_of()} 前 {within_days} 天窗内报过进展的任务数(相对窗口以基准日为准,不用系统当前时间)"
            ),
            limit=limit,
        )
    if stale_days > 0:
        sql, params = tpl.stale_tasks(as_of(), stale_days, board_code=board, in_flight_only=in_flight, limit=limit)
        return envelope(
            sql=sql,
            params=params,
            caliber=(f"最新进展早于基准日 {as_of()} 前 {stale_days} 天的任务;从未报过的也算滞后并排在最前"),
            limit=limit,
            cap_last_param=True,
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
        include_opinion=bool(args.get("can_read_sensitive")),
        granted=optional_granted("task_workflow_action"),
        limit=limit,
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "流水带 t.is_deleted = 0 是正确闸门(1,578 行);再加任务发布门会掉到 1,519;"
            "opinion(审批意见)按权限返回,无权限时不在返回列内(R-04/R-14);"
            "scope=recent 按动作自身时间倒序(默认清单按 task id 排序,答不了「最近谁被驳回」)"
        ),
        limit=limit,
        cap_last_param=scope == "recent",
    )


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
    sql, params = tpl.rank_tasks(
        metric=metric,
        mode=(args.get("mode") or "cut").strip(),
        top=int(args.get("top") or 5),
        ascending=bool(args.get("ascending")),
        group_by=(args.get("group_by") or "").strip() or None,
        board_code=(args.get("board") or "").strip() or None,
        granted_optional=granted,
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "正式任务门 = is_deleted = 0 AND workflow_status = 'published';"
            "cut 硬切前 N 条(并列按 task id),keep_ties 用 RANK() 保留并列(行数通常大于 N),"
            "per_group 每组第一(top 无意义);各度量走 LEFT JOIN,零值任务保留;"
            "project_team_size 数的是 task 行上 project_owner_name 的三种分隔符(、 , ;)"
        ),
        limit=int(args.get("top") or 5) if (args.get("mode") or "cut") == "cut" else MAX_ROWS,
        cap_last_param=(args.get("mode") or "cut") == "cut",
    )


_NEW_HANDLERS_9 = {"weekly_rank": _rank}

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
}

# _NEW_HANDLERS 只是构建期的清单,避免手工漏接线
assert set(_NEW_HANDLERS) <= set(_HANDLERS)
