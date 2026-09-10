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
) -> dict[str, Any]:
    """执行一条模板 SQL,并包成与演示源一致的信封。"""
    if _pg is None:  # pragma: no cover - enabled() 已经挡住
        raise RuntimeError("psycopg 不可用,无法访问正式源")
    bounded = max(1, min(MAX_ROWS, int(limit)))
    conn = _pg.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [d.name for d in cur.description] if cur.description else []
            raw = cur.fetchmany(bounded + 1)
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
        )
    if scope not in tpl.COVERAGE_SCOPES:
        return None
    sql, params = tpl.coverage_stats(
        scope,
        board_code=board,
        project_group=(args.get("project_group") or "").strip() or None,
        limit=limit,
    )
    return envelope(
        sql=sql,
        params=params,
        caliber=f"scope={scope};正式任务门 = is_deleted = 0 AND workflow_status = 'published'",
        limit=limit,
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


_HANDLERS = {
    "weekly_task_query": _task_query,
    "weekly_progress_coverage": _coverage,
    "weekly_freshness_distribution": _freshness,
}
