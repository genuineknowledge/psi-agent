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
import _fallback
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

    **驱动异常一律包成信封**(``ok: false`` + ``error.code``),不外泄:

    这是**容器实跑抓到的一条**:正式源连不上时,原先异常会一路冒到 MCP 层,
    工具返回的是 ``Error executing tool weekly_health: failed to resolve host ...``
    —— 一句纯文本,不是信封。对调用方有两个后果:

    1. **换数据源就要换读法**:演示源出错给信封、正式源出错给文本,读 ``error.code``
       的代码在正式源上永远拿不到东西;
    2. ``_fallback`` 那条"把连不上翻译成可操作报错"的兜底**碰不到它**(它只认信封里的
       ``store_unreachable``),于是国数生产上真出网络问题时,agent 看到的是一句
       没有 code、没有指路的驱动报错。

    所以这里把 ``_pg.driver_errors()`` 接住,统一成 ``store_unreachable``:
    与演示源连不上是**同一个语义**(数据源不可达),``_fallback`` 那层因此能一视同仁地
    翻译成 ``not_migrated`` 或保留原文。工具名靠 ``error.message`` 里的查询目标
    (``pg://user@host:port/db``,不含口令)定位,不必让每个工具自己包一层。
    """
    if _pg is None:  # pragma: no cover - enabled() 已经挡住
        raise RuntimeError("psycopg 不可用,无法访问正式源")
    bounded = max(1, min(MAX_ROWS, int(limit)))
    sql_params = params
    if cap_last_param and params:
        # 多要一行只为判定截断;模板里的 LIMIT 已经把结果集压在 bounded+1 以内
        sql_params = (*params[:-1], bounded + 1)
    try:
        conn = _pg.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, sql_params)
                columns = [d.name for d in cur.description] if cur.description else []
                raw = cur.fetchall()
        finally:
            conn.close()
    except _pg.driver_errors() as exc:
        raise SourceUnavailableError(
            {
                "ok": False,
                "error": {
                    "code": "store_unreachable",
                    "message": (
                        f"cannot reach {_pg.dsn()}: {type(exc).__name__}: "
                        f"{str(exc).splitlines()[0][:200]}"
                    ),
                },
            }
        ) from exc
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


class SourceUnavailableError(Exception):
    """正式源不可达 —— 携带一个**已经成形的错误信封**,由 ``dispatch`` 原样交出。

    为什么不沿用"返回错误信封"的形式:``envelope`` 的返回值会被处理函数继续加工
    (``result["rows"]`` / ``result["caliber"] += ...``)。返回错误信封时那些加工会
    ``KeyError: 'rows'``,**把刚生成的错误信封顶掉**,又变成一句
    ``Error executing tool ...`` 文本冒到 MCP 层 —— 容器实跑里 31 个工具中有 10 个
    正是这样(``weekly_rank`` / ``weekly_health`` / ``weekly_schema`` / ``weekly_task_detail`` …)。

    改成抛异常,整段加工自然跳过,错误信封原封不动到出口。
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__((payload.get("error") or {}).get("message", "formal source unavailable"))
        self.payload = payload


def ok_envelope(result: dict[str, Any]) -> bool:
    """``envelope`` 的返回值是不是**成功的**信封(有 ``rows`` 可取)。

    ``SourceUnavailableError`` 已经挡住了"库连不上"这一大类;这个函数是给
    **其余错误信封**留的一道闸(例如处理函数自己拼的错误信封,或未来新增的
    非异常错误路径)。用法与 ``_rank`` 里那一处相同:

    ```python
    result = envelope(...)
    if not ok_envelope(result):
        return result
    totals = [row.pop(...) for row in result["rows"]]   # ← 这行才安全
    ```
    """
    return bool(result.get("ok"))


def dispatch(tool: str, **kwargs: Any) -> dict[str, Any] | None:
    """把一次工具调用映射到正式源模板;未迁移的组合返回 ``None``。

    出口统一过一遍 ``_fallback.translate_formal_error``:把"正式源连不上"这个
    ``store_unreachable`` 换成 ``formal_source_unreachable``(与"参数没迁"分开)。
    放在这**一个**地方而不是每个工具里 —— 它是所有正式源调用的唯一漏斗。

    ``SourceUnavailableError`` 也在这里接住:连不上库时,处理函数里那些
    ``result["rows"]`` / ``result["caliber"]`` 的加工代码没有意义,让它们整段跳过 ——
    **一个刚生成的错误信封不许被后续加工顶掉**(详见 ``envelope`` 的说明)。
    """
    if not enabled():
        return None
    handler = _HANDLERS.get(tool)
    if handler is None:
        return None
    try:
        result = handler(kwargs)
    except SourceUnavailableError as exc:
        return _fallback.translate_formal_error(tool, exc.payload)
    except ValueError as exc:
        # 参数不满足口径(缺 year、看板码不在域内……):直接按契约报错,不落到演示路径
        return {"ok": False, "error": {"code": "invalid_argument", "message": str(exc)}}
    except PermissionError as exc:
        # 可选表未在本次授权范围内:显式说明不可答。**不回落演示路径** ——
        # 回落会去连演示 MySQL,把"没有权限"变成"另一个数据源的答案"。
        return {"ok": False, "error": {"code": "table_not_granted", "message": str(exc)}}
    if result is not None and result.get("ok") is False:
        return _fallback.translate_formal_error(tool, result)
    return result


# ---- 各工具的映射 -----------------------------------------------------------


def _task_query(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_task_query:检索已发布任务(关键词 / 分类 / 负责人 / 状态 / 项目组 / 看板)。"""
    board = _board(args)
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
    board = _board(args)
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
    board = _board(args)
    in_flight = bool(args.get("in_flight"))
    within_days = int(args.get("within_days") or 0)
    stale_days = int(args.get("stale_days") or 0)
    recent_days = int(args.get("recent_days") or 0)
    reported_only = bool(args.get("reported_only"))
    by = (args.get("by") or "").strip().lower()
    lag_bands = bool(args.get("lag_bands"))
    raw_task = (args.get("task") or "").strip()
    if raw_task:
        return _freshness_one_task(args, raw_task, limit=limit)

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


_BOARD_CODES = frozenset(adm.BOARD_CODE_DOMAIN)

# 看板名里与"哪个看板"无关的通用词:匹配时**只作字面包含**用,不进"特征片段"。
# 真实库里看板名是「技术组重点任务进展」「集团重点任务调度」,而提问口径是
# 「技术组」「集团看板」—— "看板""重点""任务""调度""进展"这些词两个看板名里都可能有,
# 留着会让两个看板得分打平,那就等于没判。
_BOARD_STOPWORDS = frozenset({"看板", "重点", "任务", "调度", "进展", "工作"})

_board_cache: list[dict[str, Any]] | None = None


def _board_grams(text: str) -> set[str]:
    """取 2 字片段作为"特征"(骨架词本就两字,长度取 2 与库里名字的写法无关)。

    分隔符用转义序列写(全角括号与逗号直接写会触发 RUF001 的 ambiguous unicode)。
    """
    clean = re.sub(r"[\s\uff08\uff09\u3001\uff0c,()/]+", "", text or "")
    return {clean[i : i + 2] for i in range(len(clean) - 1)} - _BOARD_STOPWORDS


def _fetch_boards() -> list[dict[str, Any]]:
    """看板清单(进程内缓存一次)。表是两张、几乎不变,而它只在解析**看板名**时才查。"""
    global _board_cache
    if _board_cache is None:
        sql, params = tpl.schema_boards()
        got = envelope(sql=sql, params=params, caliber="看板清单", limit=MAX_ROWS)
        _board_cache = list(got["rows"])
    return _board_cache


def _board(args: dict[str, Any], key: str = "board") -> str | None:
    """把 ``board=`` 解析成**看板码**。

    国数的问句说的是看板**名字**(真库:``技术组重点任务进展`` / ``集团重点任务调度``;
    提问口径叫「技术组」「集团看板」),而模板层只认码(``tech`` / ``group``)。
    此前给名字一律落到模板的值域校验,得到一句"取值不在值域内(group, tech)"——
    对调用方是死路:它手上的问题本来就只能说名字。

    四级匹配:

    1. 已经是码(``tech`` / ``group``)⇒ 原样返回,不查库;
    2. 名字**精确相等** ⇒ 该看板;
    3. 名字**字面包含**(``token in name``,``技术组`` ⊂ ``技术组重点任务进展``)⇒ 该看板;
    4. ``_board_grams`` **特征片段**重合度唯一最高 ⇒ 该看板。
       这一条是为「集团看板」这类**改述**准备的:它既不等于也不包含库内名字
       (``集团重点任务调度``),只有 ``集团`` 这个片段命中 —— 而 ``看板`` 是通用词,
       留在片段集合里会让两个看板打平。

    多义(并列最高)与完全不匹配都**交给模板层的值域校验**去报错(不在这里猜一个看板):
    猜错看板会把整份答案换成另一个看板的,而错误信息里带着真实值域,调用方改一次就好。
    查库失败时同样退回码路径 —— 那会让模板报值域错,而不是把一次网络抖动升级成
    "这个工具坏了"。
    """
    token = (args.get(key) or "").strip()
    if not token or token in _BOARD_CODES:
        return token or None
    try:
        rows = _fetch_boards()
    except Exception:  # 正式库不可达时退回码路径,由模板报值域错
        return token

    def code_of(row: dict[str, Any]) -> str:
        return str(row["code"])

    exact = [r for r in rows if str(r.get("name") or "").strip() == token]
    if len(exact) == 1:
        return code_of(exact[0])
    literal = [r for r in rows if token in str(r.get("name") or "")]
    if len(literal) == 1:
        return code_of(literal[0])
    if len(rows) > 1 and token:
        wanted = _board_grams(token)
        scored = sorted(
            ((len(wanted & _board_grams(str(r.get("name") or ""))), code_of(r)) for r in rows),
            reverse=True,
        )
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
    return token


def _attachment(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_attachment_query:附件**元数据**清单(storage_path 永不出现)。"""
    limit = int(args.get("limit") or MAX_ROWS)
    board = _board(args)
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


def _freshness_one_task(args: dict[str, Any], raw_task: str, *, limit: int) -> dict[str, Any] | None:
    """``weekly_freshness_distribution task=``:单任务新鲜度 + 漂移核对。

    与分档清单答的**不是同一个问题**:那里是"全库有几个桶",这里是某个任务自己那一行。
    查不到时要说清是**哪一种查不到** —— 库里没有这一行,还是有这一行但不过正式任务门
    (已软删 / 未发布)。两者都是 0 行,但下一步动作完全不同(换 id vs 换任务);
    真库上 104 条软删存活任务里只有 88 条是正式任务,问错一个是常事。
    """
    if args.get("by") or args.get("stale_days") or args.get("recent_days") or args.get("lag_bands"):
        return None  # by / 天数窗与单任务档组合起来语义不清,交给演示路径
    task_id, task_name = _resolve_task(raw_task)
    sql, params = tpl.freshness_task(as_of(), task_id=task_id, task_name=task_name)
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            f"单个任务的新鲜度 + 漂移核对;基准日 {as_of()}(数据快照日,非系统当天);"
            "days_behind = 基准日 - latest_progress_time(按**日期**相减);"
            "actual_latest_report 是真实最新一期已发布进展时间,与任务行的冗余列 "
            "latest_progress_time 不一致即漂移(此时不得用冗余列回答新鲜度);"
            "从未报过进展的任务 days_behind 为 null —— 那是「没有这个天数」,不是 0 天"
        ),
        limit=1,
    )
    if not ok_envelope(result):
        return result
    if result["row_count"]:
        return result
    # 0 行:补一次"这一行到底在不在"的判定(不带 R-01 闸门),把两种 0 行分开
    probe_sql, probe_params = tpl.freshness_task_probe(task_id=task_id, task_name=task_name)
    probe = envelope(sql=probe_sql, params=probe_params, caliber="任务行是否存在(不带发布门)", limit=1)
    row = (probe.get("rows") or [None])[0]
    if row is None:
        return {
            "ok": False,
            "error": {
                "code": "task_not_found",
                "message": (
                    f"库中无此任务:{raw_task}(不是口径过滤掉的,是确实没有这行)"
                ),
            },
        }
    formal = int(row.get("is_deleted") or 0) == 0 and str(row.get("workflow_status")) == "published"
    if formal:  # pragma: no cover - 正式任务却 0 行只可能是并发删除,仍照实说明
        return result
    cause = (
        "已删除(is_deleted = 1)"
        if int(row.get("is_deleted") or 0)
        else f"workflow_status = '{row.get('workflow_status')}'"
    )
    return {
        "ok": False,
        "error": {
            "code": "task_not_formal",
            "message": (
                f"任务 {row.get('id')}「{row.get('task_name')}」存在但不属正式任务:{cause},"
                "未过 R-01(is_deleted = 0 AND workflow_status = 'published')。"
                "本档按正式任务口径取数,故不返回它的新鲜度;"
                "它的提交单 / 审批动作挂在 task_id 外键上,"
                "weekly_submission_query / weekly_workflow_query 按同一个 id 仍可查到。"
                "不要改用按名字搜 —— 同名系列的(N期)是另外几条任务,答的不是这一条"
            ),
        },
    }


def _year_goal(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_year_goal_query:年度目标行清单(year=0 表示所有年度)。"""
    limit = int(args.get("limit") or MAX_ROWS)
    board = _board(args)
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
    """weekly_owner_roles:某人的主责 / 项目负责人 / 牵头领导 / 去重并集。

    缺 ``person`` 时按契约报 ``invalid_argument``(**不回落演示路径**):参考实现
    就是报 ``invalid_argument: person 不能为空``,回落反而会把一个参数错误变成
    "另一个数据源的答案"(正式源模式下还会先去连一台不存在的演示库)。
    """
    person = (args.get("person") or "").strip()
    if not person:
        raise ValueError("person 不能为空")
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
        board_code=_board(args) or "group",
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
    if not ok_envelope(result):
        return result
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
    """weekly_submission_query:提交单聚合各 scope + **默认明细清单**。

    具名 scope 只迁移**纯聚合**请求:带 task / reporter / status / exclude_status
    的是明细筛选,语义与聚合不同,交给演示路径(返回 None),不猜。

    ``scope`` 为空即**默认清单档**:参考实现是"一张单一行"的明细(带状态分档、
    状态值域与命中总数),此前没迁 —— 于是 ``weekly_submission_query()`` 在正式源
    模式下回落演示库,生产上等于 ``store_unreachable``。现在按参考实现的列集合
    (``SUBMISSION_ROW_COLUMNS``)与筛选全部迁移;``status_mismatch`` 是另一条形状
    (一任务一行、比对两套码值),仍回落。
    """
    scope = (args.get("scope") or "").strip().lower()
    limit = int(args.get("limit") or MAX_ROWS)
    board = _board(args)
    raw_task = (args.get("task") or "").strip()
    task_id, task_name = _resolve_task(raw_task)
    reporter = (args.get("reporter") or "").strip() or None
    raw_status = (args.get("status") or "").strip() or None
    raw_exclude = (args.get("exclude_status") or "").strip() or None

    if args.get("status_mismatch"):
        # 一任务一行(取最新一轮单)与任务的 workflow_status 逐条比 —— 与明细清单是
        # **两种形状**:清单一行是一张单,拿它去数会把同一任务的多轮重复计入。
        # 与 status / exclude_status 组合起来语义不清(那两列筛的是"单的状态",
        # 而本档问的是"两边不一致"),故不迁移那些组合。
        if scope or raw_status or raw_exclude:
            return None
        sql, params = tpl.submission_status_mismatch(limit=limit)
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                "仅 t.is_deleted = 0(**不加任务发布门**:已发布但最新单仍在流程中的任务"
                "正是本题答案,加门会把它们滤掉);"
                "最新一轮 = 该任务 round_no 最大的那张单,一任务一行;"
                "任务 workflow_status 与提交单 status 是**两套码值**"
                "(任务侧 published/pending_*/rejected;单侧 pending_fill/signing/"
                "pending_audit/pending_leader/published/rejected/cancelled),"
                "此处按**字面不等**判定,不代表两边的码值有一一对应;"
                "行数即不一致任务总数,按 task id 升序;"
                "真库实测常为 0 行 —— 0 行表示**当前不存在不一致**,不要读成「查不到」"
            ),
            limit=limit,
            cap_last_param=True,
        )

    if not scope:
        sql, params = tpl.submission_rows(
            board_code=board,
            task_id=task_id,
            task_name=task_name,
            reporter=reporter,
            status=raw_status,
            exclude_status=raw_exclude,
            limit=limit,
        )
        result = envelope(
            sql=sql,
            params=params,
            caliber=(
                "提交单域只加 t.is_deleted = 0(**不加任务发布门**:在途任务的单同样是单);"
                "一行一张单,task_id + round_no 唯一;"
                "按提交时间倒序(最新一轮在最前),时间相同按 id 倒序;"
                "submission_kind 区分 initial / progress;"
                "payload 草稿快照默认不并入正式数据,不返回"
            ),
            limit=limit,
            cap_last_param=True,
        )
        submission_listing_extras(
            result,
            board=board,
            task_id=task_id,
            task_name=task_name,
            reporter=reporter,
            status=raw_status,
            exclude_status=raw_exclude,
        )
        if reporter:
            result["caliber"] += f";仅填报人 {reporter}(id 或姓名去空格后精确匹配)"
        if raw_status or raw_exclude:
            result["caliber"] += status_filter_note(raw_status, raw_exclude, result)
        return result

    if scope not in tpl.SUBMISSION_SCOPES or scope == "rows":
        return None
    for key in ("task", "reporter", "status", "exclude_status"):
        if (args.get(key) or "").strip():
            return None
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


def submission_listing_extras(
    result: dict[str, Any],
    *,
    board: str | None,
    task_id: int | None,
    task_name: str | None,
    reporter: str | None,
    status: str | None,
    exclude_status: str | None,
) -> None:
    """默认清单档的三个配套项:命中总数、状态分档、状态值域。

    与参考实现同一组附加字段(``total_count`` / ``status_breakdown`` / ``status_domain``):
    清单封顶 200 行,"一共几张单"与"各状态各几张"必须由服务端算完再回,否则调用方只能
    拿第一页去数。

    三个配套项必须与清单**同一个范围**(所有筛选条件都传进来):只按任务/看板收窄而漏掉
    ``status``,就会出现"清单 12 行、``total_count`` 28"这种自相矛盾的回包,而调用方
    只会相信自己看到的那个数。实测正是这么踩出来的。

    ``status_domain`` 还兼一个作用:提交单状态值域不含 ``approved``(已发布是 ``published``),
    用 ``approved`` 过滤筛不掉任何行 —— 值域在手上,调用方才分得清"筛完是 0 条"与
    "这个词根本不在值域里"。
    """
    scoped = tpl.submission_scope_where(
        board_code=board,
        task_id=task_id,
        task_name=task_name,
        reporter=reporter,
        status=status,
        exclude_status=exclude_status,
    )
    where_sql, board_join, params = scoped
    scope_sql = tpl.submission_scoped_count_sql(where_sql, board_join)
    total = envelope(sql=f"SELECT count(*) AS n {scope_sql}", params=params, caliber="本档命中的提交单总数", limit=1)
    result["total_count"] = (total.get("rows") or [{}])[0].get("n")
    breakdown = envelope(
        sql=f"SELECT s.status, count(*) AS cnt {scope_sql} GROUP BY s.status ORDER BY cnt DESC, s.status",
        params=params,
        caliber="按提交单状态分档计数(与清单同一范围)",
        limit=MAX_ROWS,
    )
    result["status_breakdown"] = breakdown["rows"]
    domain = envelope(
        sql="SELECT DISTINCT s.status FROM task_workflow_submission s ORDER BY s.status",
        params=(),
        caliber="提交单状态值域(全表,与上面的筛选无关)",
        limit=MAX_ROWS,
    )
    result["status_domain"] = sorted(
        str(row["status"]).strip() for row in domain["rows"] if row.get("status") is not None
    )


def status_filter_note(raw_status: str | None, raw_exclude: str | None, result: dict[str, Any]) -> str:
    """状态过滤的口径提示:给的词不在值域内时**必须显式说明它没筛掉任何行**。

    提交单状态与任务 ``workflow_status`` 不是一套码值,``approved`` 这类词若不在值域内,
    过滤会静默失效(等价于没过滤),不说就会把全量当成筛后结果。
    """
    domain = result.get("status_domain") or []
    unknown = [token for token in (raw_status, raw_exclude) if token and token not in domain]
    if not unknown:
        return ""
    return (
        f";注意 {'、'.join(unknown)} 不在提交单状态值域 {domain} 内,"
        "该过滤条件未筛掉任何行,结果等于未过滤,回答时不要说成「已排除」"
    )


def _workflow(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_workflow_query:审批动作流水(可选表)。意见列按权限返回。

    ``scope`` 为空即**默认明细清单**档:一行一条动作,按 ``task_id, created_at, id``
    排序(某任务的审批轨迹),列集合见 ``tpl.WORKFLOW_ROW_COLUMNS``。它与 ``recent``
    是两道桥:``recent`` 按动作自身时间倒序、答"最近谁被驳回"。
    ``by_task=True`` 是**第三条形状**:一任务一行(``action_count``),答的是任务集合与
    次数 —— 拿流水行数报会把**次数当成任务数**。
    """
    scope = (args.get("scope") or "").strip().lower()
    limit = int(args.get("limit") or MAX_ROWS)
    board = _board(args)
    task_id, task_name = _resolve_task(args.get("task") or "")
    raw_action = (args.get("action") or "").strip() or None
    granted = optional_granted("task_workflow_action")

    if args.get("by_task"):
        if scope:
            return None  # by_task 与具名 scope 组合语义不清,交给演示路径
        sql, params = tpl.workflow_actions_by_task(
            board_code=board,
            task_id=task_id,
            action=raw_action,
            granted=granted,
            limit=limit,
        )
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                "流水带 t.is_deleted = 0 是正确闸门(1,578 行);再加任务发布门会掉到 1,519;"
                "本档**一任务一行**,action_count 是动作**次数**不是任务数 ——"
                "同一任务的一条流水里会出现多轮动作,拿流水行数报会把次数当成任务数"
                "(真库 91 条动作挂在 23 个任务上,最多的那个任务 19 条);"
                "按 action_count 降序、并列按 task id 升序;"
                "带 board= 时一并回 task_name(没有任务名的榜单答不了「哪些任务」),"
                "不带看板时只回 task_id"
            ),
            limit=limit,
            cap_last_param=True,
        )

    if scope not in tpl.WORKFLOW_SCOPES:
        if scope:
            return None
        sql, params = tpl.workflow_action_rows(
            board_code=board,
            task_id=task_id,
            task_name=task_name,
            action=raw_action,
            granted=granted,
            limit=limit,
        )
        result = envelope(
            sql=sql,
            params=params,
            caliber=(
                "流水带 t.is_deleted = 0 是正确闸门(1,578 行);再加任务发布门会掉到 1,519;"
                "本档一行一条动作,按 task_id + 动作时间升序(某任务的审批轨迹);"
                "**不按 round_no 排**:轮次号不等于时间序(存在第 3 轮早于第 2 轮的任务),"
                "按轮次读会把轨迹读反;要「最近谁被驳回」请用 scope=recent(按动作时间倒序);"
                "opinion(审批意见)始终在列里,无权限时值打码成「[按权限不展示]」"
                "(与参考实现的 _scrub 同一语义:藏列会让调用方分不清「没有意见」与「没权限看」)"
            ),
            limit=limit,
            cap_last_param=True,
        )
        if not ok_envelope(result):
            return result
        if not bool(args.get("can_read_sensitive")):
            # 敏感字段打码而不是删列,列集合在两种权限下保持一致
            for row in result["rows"]:
                if "opinion" in row:
                    row["opinion"] = "[按权限不展示]"
        return result

    sql, params = tpl.workflow_actions(
        scope,
        board_code=board,
        task_id=task_id,
        action=raw_action,
        granted=granted,
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
    if not ok_envelope(result):
        return result
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
        board_code=_board(args),
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
    if not ok_envelope(result):
        return result
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
    """weekly_person_stats:人员统计(**14 / 14 scope 全部迁移**)。"""
    scope = (args.get("scope") or "workload").strip().lower()
    if scope not in tpl.PERSON_SCOPES:
        return None  # 未知 scope 交给演示路径(它报 unsupported_scope 并列出值域)
    role = (args.get("role") or "lead_owner").strip() or "lead_owner"
    top = int(args.get("top") or MAX_ROWS)
    board = _board(args)
    group = (args.get("project_group") or "").strip() or None
    sql, params = tpl.person_stats(scope, role=role, project_group=group, board_code=board, top=top)
    caliber = (
        f"{adm.BUSINESS_STATUS_NOTE};按「{tpl.PERSON_ROLES.get(role, ('', role, ''))[1]}」分组;"
        "姓名为空的行不计入人头;workload 是硬切(并列被切掉),workload_top 用 HAVING = MAX 保留并列;"
        "workload_summary 的均值是全局均值;group_roster 数去重后的人(不是任务条数);"
        "id_format / id_variants 只统计有标识的任务;"
        "reporters 走任务闸门 + 进展行发布闸门两道"
    )
    if scope == "reporter_count":
        caliber = (
            f"{adm.BUSINESS_STATUS_NOTE};**与 scope=reporters 同一批行**的去重填报人数"
            "(任务闸门 + p.is_published = 1 两道,一道都不少);"
            "这是**一个数**,不要拿 reporters 的行数顶替 —— 那份清单会被 top 截断"
        )
    if scope in ("reviewers", "self_review"):
        caliber = (
            f"{adm.BUSINESS_STATUS_NOTE};**审核口径刻意不加 p.is_published**:"
            "审过但还没发布的进展同样是审过的,加发布闸门会把「待审已审」整批滤掉;"
            + (
                "按审核人分组,并列按 reviewer_id 定序;0 行表示该口径下没有审核记录,"
                "不要读成「取不到」"
                if scope == "reviewers"
                else "填报人与审核人为**同一 ID**(按 ID 相等判定,不按姓名);此清单即全部自审记录"
            )
        )
    if scope == "id_variants":
        caliber += (
            ";同名多标识检查:**0 行就是答案**(该口径下不存在这种人),"
            "不要据此说「会出现」;ids 是逗号连接的标识清单"
        )
    if scope == "id_longest":
        caliber += (
            ";一行一个**去重后的标识**(不是一行一个任务);"
            "问「最长的是哪一个」传 top=1 按首行答,tied_at_top 是等长标识个数,"
            "据它补一句「另有并列」即可,不要因为存在并列就改写答案行数"
        )
    result = envelope(
        sql=sql,
        params=params,
        caliber=caliber,
        limit=top,
        cap_last_param=scope in _PERSON_LISTING_SCOPES,
    )
    if scope == "workload":
        tsql, tparams = tpl.person_ties(role=role, board_code=board)
        ties = envelope(sql=tsql, params=tparams, caliber="并列自检", limit=1)
        first = (ties.get("rows") or [{}])[0]
        result["top_task_count"] = first.get("top_task_count")
        result["tied_at_top"] = first.get("tied_at_top")
    if scope == "id_longest":
        tsql, tparams = tpl.person_id_ties(board_code=board)
        ties = envelope(sql=tsql, params=tparams, caliber="并列自检", limit=1)
        first = (ties.get("rows") or [{}])[0]
        result["tied_at_top"] = first.get("tied_at_top")
        result["max_id_length"] = first.get("max_id_length")
    return result


_NEW_HANDLERS_10 = {"weekly_person_stats": _person_stats}

_PERSON_LISTING_SCOPES = (
    "workload",
    "single_task",
    "group_roster",
    "workload_top",
    "cross_group",
    "dual_role",
    "id_format",
    "reporters",
    "reviewers",
    "self_review",
    "id_variants",
    "id_longest",
)
"""人员统计里输出**多行**的那些档(其余是单行答案:workload_summary / reporter_count)。

与 ``_ATTACHMENT_LISTING_SCOPES`` 同一个理由:**漏一档不是报错而是静默截断** ——
``envelope`` 会按单行档取 ``limit=1``,分组结果只剩第一行,``has_more`` 还报着 true。
这张表是**补出来**的:``id_format`` 此前没登记(它一行一个标识写法),
于是那一档的分档清单一直只回第一档 —— 由 ``test_person_listing_scopes_cover_every_multi_row_scope``
抓出来(该断言要求这张表 == 全部 scope 减去两个单行档)。
"""


_ATTACHMENT_WHOLE_TABLE_SCOPES = ("zero_attachment", "deleted", "deleted_by_link", "orphan")
"""跨任务 / 全表口径:给 ``task=`` 无意义(参考实现直接报 ``task_not_applicable``)。

``zero_attachment`` 问的是"哪些任务一个附件都没有"(分母是**全部**正式任务),
``deleted`` / ``deleted_by_link`` / ``orphan`` 是对整张附件表的软删与孤儿审计 ——
这三者的答案与"某个任务"无关,静默忽略 ``task=`` 会让调用方以为答案已被收窄。
"""

_MIGRATED_ATTACHMENT_SCOPES = (
    "summary",
    "by_ext",
    "largest",
    "by_uploader",
    "uploader_count",
    "by_link",
    "by_progress",
    "zero_attachment",
    "on_open_submission",
    "by_month",
    "deleted",
    "deleted_by_link",
    "orphan",
)
"""13 档全部迁移(与参考实现的 ``_ATTACHMENT_STATS_SCOPES`` 逐一对应)。"""

_ATTACHMENT_LISTING_SCOPES = (
    "by_ext",
    "largest",
    "by_uploader",
    "by_link",
    "by_progress",
    "zero_attachment",
    "by_month",
    "deleted_by_link",
)
"""输出**多行**的那些档(其余是单行汇总:summary / uploader_count / on_open_submission /
deleted / orphan)。

它同时喂给 ``envelope`` 的两个参数,两者不能分家:清单类档要按 ``limit`` 截断,
而且模板把上限放在**最后一个参数**上(``cap_last_param`` 才能多要一行判"取满还是被截断");
单行汇总档恒为 1。**新增分档时两处(模板与这张表)一起改** —— 漏一档的后果不是报错,
而是 ``limit=1`` 把分组结果静默截成一行:``by_link`` 就曾因此只回「挂在进展 29」,
把「挂在任务本体 1」整档吃掉,``has_more`` 还报着 true。
"""


def _attachment_stats(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_attachment_stats:附件统计的**全部 13 档**(每档一种形状,不互相代答)。"""
    scope = (args.get("scope") or "summary").strip().lower()
    if scope not in _MIGRATED_ATTACHMENT_SCOPES:
        return None  # 未知 scope 交给演示路径(它报 unsupported_scope 并列出值域)
    board = _board(args)
    limit = int(args.get("limit") or MAX_ROWS)
    raw_task = (args.get("task") or "").strip()
    if raw_task and scope in _ATTACHMENT_WHOLE_TABLE_SCOPES:
        # 不静默忽略:调用方以为答案被收窄了,而其实没有
        return {
            "ok": False,
            "error": {
                "code": "task_not_applicable",
                "message": (
                    f"口径 {scope} 是跨任务 / 全表口径,传 task 无意义,不做静默忽略:"
                    "zero_attachment 问的是「哪些任务一个附件都没有」(分母是全部正式任务),"
                    "deleted / deleted_by_link / orphan 是对整张附件表的软删与孤儿审计。"
                    "要单任务的附件量请用 scope=summary 加 task"
                ),
            },
        }
    date_from = (args.get("date_from") or "").strip() or None
    if date_from and scope != "by_month":
        return None  # date_from 只对 by_month 有口径支撑(其余档参考实现也是忽略的)
    task_id, task_name = _resolve_task(raw_task)
    include_informal = bool(args.get("include_informal"))
    sql, params = tpl.attachment_stats(
        board_code=board,
        scope=scope,
        granted=optional_granted("task_attachment"),
        limit=limit,
        task_id=task_id,
        task_name=task_name,
        include_informal=include_informal,
        date_from=date_from,
    )
    caliber = (
        "is_deleted = 0 且关联任务已发布;file_size 单位是字节,原样报出(不要换算成 KB/MB 也不要写「约」);"
        "total_mb 只是同一数值的另一种表示,以字节为准;storage_path 禁止外泄,不在返回字段内;"
    )
    if scope == "by_ext":
        caliber += "by_ext 按扩展名每档一行(ext / n / total_bytes / total_mb),答「哪种文件最多」看 n 的首行"
    elif scope == "summary":
        caliber += "summary 是一行汇总(条数 / 任务数 / 字节 / 均值 / 上传人 / 挂载点 / 四个主扩展名条数)"
    elif scope == "largest":
        caliber += (
            "largest 是**清单**(一行一个文件,按字节倒序),最大的一条即首行;"
            "问「最大的几个文件」用本档,问「一共多大 / 有几个」用 summary"
        )
    elif scope == "by_uploader":
        caliber += (
            "by_uploader 是**按人分档**(一行一个人,upload_count / total_bytes / total_mb),"
            "不是按任务或看板分组,并列按 uploader_id 定序;"
            "拿清单去数人会把同一人的多个文件重复计入"
        )
    elif scope == "uploader_count":
        caliber += "uploader_count 只回去重上传人数(服务端算的一个数),别拿别处的行数代替"
    elif scope == "by_link":
        caliber += (
            "by_link 按挂载去向分档,**优先级 进展 > 提交单 > 任务本体**:一条附件只进一档,"
            "所以各档相加等于总数(同一条附件同时挂了进展与提交单时只算「挂在进展」)"
        )
    elif scope == "by_progress":
        caliber += (
            "by_progress 按**任务 + 期号**聚合,只算已发布进展(p.is_published = 1,与任务闸门是两道);"
            "同一任务可出现多期;attachment_count 是该期挂的附件数,不是任务数"
        )
    elif scope == "zero_attachment":
        caliber += (
            "zero_attachment 列**一个有效附件都没有的正式任务**(NOT EXISTS 判定,附件被软删等于没有);"
            "total_count 是零附件任务数,total_formal_tasks 才是分母 —— 占比用它,"
            "不要拿本次行数当分母"
        )
    elif scope == "on_open_submission":
        caliber += (
            "on_open_submission 只算挂在**在途提交单**上的附件(提交单 status <> 'published');"
            "提交单状态是它自己的一套码值(已发布叫 published),不要拿任务的 workflow_status 判"
        )
    elif scope == "by_month":
        caliber += (
            "by_month 按 upload_time 的**年月**分档并升序"
            + (f",仅 {date_from} 起(闭区间下界)" if date_from else ",未限起始月,含全部历史")
        )
    elif scope == "deleted":
        caliber += (
            "**全表口径,不加任务闸门**:这是关于表本身的问题(软删的行本就挂在不该再被过滤的任务上),"
            "按任务过滤会少算;active + deleted = total_rows"
        )
    elif scope == "deleted_by_link":
        caliber += (
            "**全表口径,不加任务闸门**,且只算已软删附件(a.is_deleted = 1);"
            "link_type 与 by_link 同一套优先级(进展 > 提交单 > 任务本体)"
        )
    else:  # orphan
        caliber += "orphan 数的是**外键悬空**的附件行(任务已不在库里、附件还在);0 行表示没有孤儿"
    if task_id is not None or task_name:
        caliber += (
            f";已按任务收窄({task_name or task_id})并**刻意放开任务发布门** ——"
            "附件挂在 task_id 外键上,正式集之外的任务照样有附件,带着门问只会静默答 0"
        )
    elif include_informal and scope not in _ATTACHMENT_WHOLE_TABLE_SCOPES:
        caliber += (
            ";include_informal=True 已放开任务发布门(全表口径),"
            "并且 **JOIN 换成 LEFT JOIN** —— 光放开闸门还差挂在孤儿附件上的那几行"
        )
    result = envelope(
        sql=sql,
        params=params,
        caliber=caliber,
        # 清单类档按 limit 截断(且模板把上限放在**最后一个参数**上,cap_last_param 才能
        # 多要一行判"取满还是被截断");单行汇总档恒为 1。
        # **这张清单必须与本函数里所有分档一一对应**:漏一档的后果不是报错,而是
        # `limit=1` 把分组结果静默截成一行(by_link 曾因此只回「挂在进展 29」,
        # 而「挂在任务本体 1」那档被吃掉、has_more 还报 true)。
        limit=limit if scope in _ATTACHMENT_LISTING_SCOPES else 1,
        cap_last_param=scope in _ATTACHMENT_LISTING_SCOPES,
    )
    if scope == "zero_attachment":
        # 分母(全部正式任务)与命中总数都提到顶层:占比要用分母,不能拿本次行数当分母;
        # 总数也不能拿行数顶替 —— 清单被 limit 截断时那只是第一页。
        total_sql, total_params = tpl.attachment_zero_total(board_code=board)
        counted = envelope(sql=total_sql, params=total_params, caliber="零附件任务总数", limit=1)
        result["total_count"] = (counted.get("rows") or [{}])[0].get("total_count")
        first = (result["rows"] or [{}])[0]
        result["total_formal_tasks"] = first.get("total_formal_tasks")
        result["caliber"] += (
            f";total_count 是零附件任务数({result['total_count']}),"
            f"total_formal_tasks 是分母({result['total_formal_tasks']}) —— 占比用它,"
            "不要拿本次行数当分母"
        )
    return result


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
    """weekly_progress_range:时间轴上的正式进展(全任务或指定看板)。

    三条轴全部接线(此前只迁了"给定窗口列明细"这一档,其余一律回落):

    * **窗口两端都可以为空** —— 参考实现的文档写的是 "Empty means unbounded",
      所以空串是"这一端不设限",不是"缺参数"。默认档 ``weekly_progress_range()``
      因此能答(它此前是回落的一档);
    * ``date_field``:``progress_date``(所报周期)或 ``report_time``(交上来的时刻)。
      补报时后者晚于前者 —— 拿周期答"什么时候交的"会把补报算到它所属的周期上;
    * ``by`` = month / quarter / task 的分组计数,``peak`` 只回最高的一档。
      month / quarter 不带 peak 时**由服务端给环比列**,见模板层的说明。
    """
    date_field = (args.get("date_field") or "progress_date").strip().lower()
    if date_field not in tpl.PROGRESS_DATE_FIELDS:
        # 值域错由参考实现自己报(它的消息里带可用取值),这里不抢答
        return None
    grouping = (args.get("by") or "").strip().lower()
    if grouping and grouping not in tpl.PROGRESS_GROUPINGS:
        return None
    peak = bool(args.get("peak"))

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
    for label, value in (("date_from", date_from), ("date_to", date_to)):
        if value and not _DATE_RE.match(value):
            # 不校验就会把 '2026/08/01' 直接喂给 PG,报出来的是驱动层的语法错而不是口径错
            raise ValueError(f"{label} 需为 YYYY-MM-DD:{value}")
    if date_from and date_to and date_from > date_to:
        raise ValueError(f"窗口起点晚于终点:{date_from} > {date_to}")
    # 空窗提示只在窗口**真的短**时才有意义:无界窗口 0 行是另一回事(库里确实没有进展),
    # 拿"半月口径"去解释它会把调用方引到错误的下一步。
    span = None
    if date_from and date_to:
        span = (dt.date.fromisoformat(date_to) - dt.date.fromisoformat(date_from)).days

    limit = int(args.get("limit") or MAX_ROWS)
    field_note = (
        "时间轴按 progress_date(所报周期)过滤与排序"
        if date_field == "progress_date"
        else "时间轴按 report_time(交上来的时刻)过滤与排序 —— 补报的行落在这里,"
        "与按周期看不是同一批"
    )
    window_note = (
        f"窗口 [{date_from or '不限'} ~ {date_to or '不限'}],两端都是闭区间"
        if (date_from or date_to)
        else "未限定窗口,覆盖全库正式进展"
    )
    base_caliber = (
        "只取正式展示版本(is_published = 1)且任务已发布;"
        f"{window_note};"
        f"相对窗口以数据基准日 {as_of()} 为基准,不用系统当前时间(last_days=N 时窗口是 "
        f"{as_of()} 往前数 N 天到基准日,含两端);"
        f"{field_note};"
        "lag_days = 上报日 - 周期日(补报更早周期时为正);"
        "集团看板的进展不在这张表里,用 weekly_group_history"
    )

    if grouping:
        return _progress_grouped(
            grouping=grouping,
            peak=peak,
            date_from=date_from,
            date_to=date_to,
            date_field=date_field,
            limit=limit,
            base_caliber=base_caliber,
            span=span,
        )

    sql, params = tpl.progress_range(
        None, date_from, date_to, limit=limit, date_field=date_field
    )
    result = envelope(
        sql=sql,
        params=params,
        caliber=base_caliber,
        limit=limit,
        cap_last_param=True,
        extra={"date_from": date_from, "date_to": date_to, "date_field": date_field},
    )
    # 总数单独查一次(与参考实现同法):明细被 200 行截断后,调用方还原不出真值
    tsql, tparams = tpl.progress_range_totals(
        None, date_from, date_to, date_field=date_field
    )
    totals = envelope(sql=tsql, params=tparams, caliber="窗口总数", limit=1)
    first = (totals.get("rows") or [{}])[0]
    result["total_count"] = first.get("total_rows")
    result["total_tasks"] = first.get("total_tasks")
    if not result["total_count"] and span is not None and span < 15:
        # 0 行有两种完全不同的原因。不点明,task_progress 按月上报这件事会让
        # "最近一周"被答成"没有任务更新进展"——参考实现为此吃了 6 轮返工。
        result["caliber"] += _short_window_hint()
    return result


def _progress_grouped(
    *,
    grouping: str,
    peak: bool,
    date_from: str,
    date_to: str,
    date_field: str,
    limit: int,
    base_caliber: str,
    span: int | None,
) -> dict[str, Any]:
    """``weekly_progress_range`` 的分组档(month / quarter / task,可带 peak)。

    与参考实现的三条判断逐条对齐:

    * month / quarter **不带 peak** → 回环比列(``prev_count`` / ``mom_change``),
      相邻两档由 SQL 的 ``LAG`` 配对,不让调用方拿着计数列表自己错位相减;
    * ``peak=True`` → 只回一行(计数降序、并列取 bucket 升序),SQL 里 ``LIMIT 1``;
    * ``task`` 档按任务名分组,列里不含 ``task_count``。
    """
    momentum = grouping in ("month", "quarter") and not peak
    if momentum:
        sql, params = tpl.progress_momentum(
            None, date_from, date_to, limit=limit, date_field=date_field, by=grouping
        )
    else:
        sql, params = tpl.progress_range_grouped(
            None,
            date_from,
            date_to,
            limit=limit,
            date_field=date_field,
            by=grouping,
            peak=peak,
        )
    label = {"month": "月", "quarter": "季", "task": "任务"}[grouping]
    caliber = base_caliber + f";按 {grouping} 分组计数"
    if momentum:
        caliber += (
            f";prev_count 是上一{label}的条数、mom_change 是本{label}减上一{label},"
            "两列由服务端按时序 LAG 算好,环比直接读 mom_change,不要自己把行错位相减;"
            f"首{label}的 prev_count 与 mom_change 为空是对的(没有上一{label}可比);"
            f"问「降幅最大的那一个{label}」取 mom_change 最小(最负)的那行,不是绝对值最大;"
            f"prev_count 只在本次窗口内取上一{label}:问「某一年的环比」要把窗口限定在该年"
            "(传 date_from / date_to),不限定则首档会取到上一年的数,那是跨年口径,不是该年的环比"
        )
    elif peak:
        # 全角分号写成转义序列:直接写字面量会触发 RUF001,而换半角就不是同一段文案了
        # (与 `_short_window_hint` 同法)。
        caliber += "\uff1b已按计数降序、并列取 bucket 升序,首行即峰值,勿另行比较"
    result = envelope(sql=sql, params=params, caliber=caliber, limit=limit, cap_last_param=True)
    result["date_from"] = date_from
    result["date_to"] = date_to
    result["date_field"] = date_field
    if grouping == "task" and not peak:
        result["group_by"] = "task"
    # 无周期日的已发布进展不进任何时间档。不报出来,调用方看到"各月之和 < 明细总数"
    # 会以为分组漏了行 —— 差额本身就是数据质量信号,显式给出。
    if grouping != "task":
        usql, uparams = tpl.progress_range_unbucketed(None, date_from, date_to)
        ub = envelope(sql=usql, params=uparams, caliber="无周期日自检", limit=1)
        unbucketed = ((ub.get("rows") or [{}])[0]).get("unbucketed_rows") or 0
        if unbucketed:
            result["unbucketed_rows"] = unbucketed
            result["caliber"] += (
                f";另有 {unbucketed} 行正式进展的 progress_date 为空(按 report_time 落在本次窗口内),"
                "它们进不了任何时间档,故各档之和会小于明细行数;"
                "要问那批行改用 weekly_progress_range(不带 by)直接列明细,"
                "但注意明细按 progress_date 卡窗口时也不含它们(比较空周期日恒为假)"
            )
    if not result.get("row_count") and span is not None and span < 15:
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


def _year_goal_stats(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_year_goal_stats:年度目标统计(6 个 scope)。

    两条判据在模板层钉住,这里只负责路由与信封:

    * **缺口类口径永远按正式任务算**(``coverage`` / ``missing`` / ``missing_by_group``):
      ``include_informal`` 对它们无效 —— 放宽分母会把已删除、未发布的任务算进缺口;
    * ``coverage`` 用 EXISTS 而不是 JOIN:没有目标行的任务正是要数的缺口。
    """
    scope = (args.get("scope") or "by_year").strip().lower()
    if scope not in tpl.YEAR_GOAL_STATS_SCOPES:
        return None  # 演示路径会报 unsupported_scope
    board_code = _board(args)  # 看板**名字**(技术组 / 集团看板)在这里解析成码
    year = int(args.get("year") or 0)
    year_to = int(args.get("year_to") or 0)
    top = max(1, min(MAX_ROWS, int(args.get("top") or 8)))
    min_years = max(1, int(args.get("min_years") or 3))
    in_progress_only = bool(args.get("in_progress_only"))
    include_informal = bool(args.get("include_informal"))
    if scope in tpl.YEAR_GOAL_GAP_SCOPES and not year:
        raise ValueError(f"口径 {scope} 需要指定 year")
    if scope == "multi_year" and (not year or not year_to):
        raise ValueError("口径 multi_year 需要 year 与 year_to")
    if scope == "multi_year" and year == year_to:
        raise ValueError(f"multi_year 需要两个不同的年度:{year} 与 {year_to} 相同")

    gap_scope = scope in tpl.YEAR_GOAL_GAP_SCOPES
    whole_table = include_informal and not gap_scope
    if whole_table:
        # 目标表没有孤儿行(全表 387 = INNER JOIN 后 387),所以放开闸门就够。
        base = (
            "全表口径:不加正式任务闸门,统计整张 task_year_goal;"
            "本档 387 条目标,加闸门(任务未删除且 workflow_status = 'published')是 313 条,"
            "差额 74 条挂在非正式任务上;"
            "问「正式任务设了多少目标」请用 include_informal = False(对外周报口径)"
        )
    else:
        base = "is_deleted = 0 AND workflow_status = 'published'"
        if include_informal and gap_scope:
            base += (
                ";本档量的是正式任务的目标缺口,include_informal 对它无效"
                "(放宽分母会把已删除、未发布的任务算进缺口,缺口即失去意义)"
            )
    if board_code:
        base += f";仅看板 {board_code}"

    # span:均值与清单是两条查询,均值另给(分母只含设过目标的任务)
    if scope == "span":
        rows_sql, rows_params = tpl.year_goal_span_rows(
            min_years=min_years,
            board_code=board_code,
            whole_table=whole_table,
            limit=top,
            year=year or None,
        )
        unit = "个年度" if not year else f"条 {year} 年目标"
        result = envelope(
            sql=rows_sql,
            params=rows_params,
            caliber=(
                f"{base};至少 {min_years} {unit}(含 {min_years},边界取等);"
                "按年度数降序、并列按 task id 升序"
            ),
            limit=top,
            cap_last_param=True,
            extra={"scope": scope},
        )
        avg_sql, avg_params = tpl.year_goal_span_avg(
            board_code=board_code, whole_table=whole_table, year=year or None
        )
        avg = envelope(sql=avg_sql, params=avg_params, caliber="span 均值", limit=1)
        result["avg_years_per_task"] = (avg.get("rows") or [{}])[0].get("avg_years")
        result["min_years"] = min_years
        result["caliber"] += (
            ";avg_years_per_task 的分母只含**已设过目标**的任务(没设过的不该拉低均值);"
            "years 是服务端按年度升序拼好的字符串,直接读,不要自己重排"
        )
        if year:
            # 带 year 时"跨度"这个词会误导:跨的是**同一年的条数**,不是前后几个年度。
            # 不点明,调用方会把 year_count=1 读成"这个任务只设过 1 个年度的目标"。
            result["caliber"] += (
                f";本档已限定到 {year} 年,故 year_count 是**该年度内的目标条数**"
                "(不是跨了几个年度),years 也只会有 "
                f"{year} 这一个值;要问跨年度用不带 year 的 span"
            )
        return result

    sql, params = tpl.year_goal_stats(
        scope,
        year=year or None,
        year_to=year_to or None,
        min_years=min_years,
        board_code=board_code,
        whole_table=whole_table,
        in_progress_only=in_progress_only,
        limit=top,
    )
    caliber = base
    listing = scope in ("missing", "missing_by_group", "multi_year", "span")
    if scope == "by_year":
        caliber += ";按年度统计目标条数与涉及任务数"
    elif scope == "coverage":
        caliber += ";分母为全部正式任务,未设目标的任务计入缺口(不能用 JOIN 丢掉)"
    elif scope == "missing":
        caliber += (
            f";{year} 年度无目标行"
            + (";仅在办任务(status IN (0, 1),0 未开始同样在办)" if in_progress_only else ";含全部状态,未按在办过滤")
            + ";status 0 未开始 / 1 进行中 / 2 已完成 / 3 已暂停"
        )
    elif scope == "missing_by_group":
        caliber += f";按专项组统计 {year} 年度目标缺口"
    elif scope == "multi_year":
        caliber += f";{year} 与 {year_to} 两年对照"

    result = envelope(
        sql=sql,
        params=params,
        caliber=caliber,
        limit=top,
        cap_last_param=listing,
        extra={"scope": scope},
    )
    if scope == "missing":
        tsql, tparams = tpl.year_goal_missing_total(
            year, board_code=board_code, in_progress_only=in_progress_only
        )
        total = envelope(sql=tsql, params=tparams, caliber="missing 总数", limit=1)
        result["total_count"] = (total.get("rows") or [{}])[0].get("total_count")
        result["caliber"] += ";total_count 是缺口任务总数(明细被 row cap 截断后仍可引用)"
    elif scope == "multi_year":
        tsql, tparams = tpl.year_goal_multi_year_total(year, year_to, board_code=board_code)
        both = envelope(sql=tsql, params=tparams, caliber="multi_year 总数", limit=1)
        result["tasks_in_both_years"] = (both.get("rows") or [{}])[0].get("tasks")
        result["years"] = [year, year_to]
        result["caliber"] += (
            ";tasks_in_both_years 是两个年度**都设了**目标的任务数;"
            "goal_year_1 / goal_year_2 两列并排给出,不要自己错位对照"
        )
    return result


def _schema(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_schema:看板 / 分类树 / 字段字典。

    这个工具的返回值**没有 columns** —— 它是复合信封(boards / categories /
    table_columns / field_notes),不是一张表。所以 ``envelope`` 那条路用不上,
    三张子表各自跑一次再拼起来(与演示源同形)。
    """
    board_code = _board(args)  # 看板名字(技术组 / 集团看板)在这里解析成码

    bsql, bparams = tpl.schema_boards()
    boards = envelope(sql=bsql, params=bparams, caliber="is_deleted = 0", limit=MAX_ROWS)
    csql, cparams = tpl.schema_categories(board_code)
    categories = envelope(
        sql=csql,
        params=cparams,
        caliber="is_deleted = 0;parent_id 为空是一级分类",
        limit=MAX_ROWS,
        cap_last_param=True,
    )
    cols_sql, cols_params = tpl.schema_columns()
    columns = envelope(
        sql=cols_sql,
        params=cols_params,
        caliber=f"仅 ChatBI 契约覆盖的 12 张表;已排除禁止外泄字段:{', '.join(tpl.BLOCKED_COLUMNS)}",
        limit=MAX_ROWS,
    )
    by_table: dict[str, list[str]] = {}
    for row in columns["rows"]:
        by_table.setdefault(str(row["table_name"]), []).append(str(row["column_name"]))
    return {
        "ok": True,
        "boards": boards["rows"],
        "categories": categories["rows"],
        "table_columns": by_table,
        "field_notes": {
            "formal_task": "is_deleted = 0 AND workflow_status = 'published'",
            "status": "0未开始 / 1进行中 / 2已完成 / 3已停用",
            "completion_time": "展示文本,不可做日期运算(R-12)",
            "owner_multi_value": "分管领导等为多值分隔文本,须去空格后匹配(R-13)",
            "blocked_fields": list(tpl.BLOCKED_COLUMNS),
            "sensitive_fields": ["review_comment", "opinion"],
            "table_columns_note": "已剔除禁止外泄字段,故此清单即可对外引用的全部字段;"
            "只列 ChatBI 契约覆盖的 12 张表,正式库 public 下的其他表不在契约内",
        },
        "caliber": (
            "看板与分类树均带 is_deleted = 0;"
            "字段字典取自 information_schema.columns(列注释经 pg_description),"
            f"已剔除禁止外泄字段 {', '.join(tpl.BLOCKED_COLUMNS)}"
        ),
        "snapshot_note": FORMAL_SNAPSHOT_NOTE,
        "snapshot_date": as_of(),
    }


def _freshness_snapshot(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_freshness:**数据快照日期**(不是新鲜度分布)。

    与 ``weekly_freshness_distribution`` 是两件事:这个工具答"数据更新到什么时候了",
    那个答"各任务多久没报进展了"。E6-01 就是问前者却拿到了后者。

    四个部分:各看板行(含未发布口径)、全库那一对、各看板的**正式**口径、
    导入批次日期。最后一项依赖可选表 ``task_progress_import``:未授权时**保留键**
    但值为 null 并在口径里说明 —— 删掉这个键会让调用方分不清"没有批次表"与
    "批次日期查不到"。
    """
    anchor = as_of()
    sql, params = tpl.freshness_board_latest(anchor)
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            "is_deleted = 0 AND workflow_status = 'published';相对时间须以此快照锚定;"
            "days_behind 是快照日减该看板最新进展时间,由服务端算好;"
            "问「数据更新到什么时候了」两个数都要报:最新时间点,以及它距快照日几天"
            "(overall 里给的是全库那一对,各看板另有自己的一对)"
        ),
        limit=MAX_ROWS,
        extra={"as_of": anchor},
    )
    osql, oparams = tpl.freshness_snapshot_overall(anchor)
    overall = envelope(sql=osql, params=oparams, caliber="全库那一对", limit=1)
    # 总览是一行(不是行数组),与演示源同形:调用方读 result["overall"]["newest"]。
    result["overall"] = (overall.get("rows") or [{}])[0]
    result["as_of"] = anchor

    if optional_granted("task_group_progress_history"):
        psql, pparams = tpl.freshness_published_per_board(anchor, group_history_granted=True)
        published = envelope(
            sql=psql,
            params=pparams,
            caliber=(
                "newest_published_progress 只算 is_published = 1 的正式进展行,"
                "并按看板各取自己的表:技术组 task_progress、集团组 task_group_progress_history;"
                "上面各看板行的 latest_progress 是 task.latest_progress_time,它含未发布行,"
                "两者不等就是发布滞后(技术组 08-09 vs 07-31);"
                "问「(某看板)数据更新到什么时候」答正式口径这一列,不要答 latest_progress"
            ),
            limit=MAX_ROWS,
        )
        result["published_progress"] = published["rows"]
        result["caliber"] += (
            ";newest_published_progress 只算 is_published = 1 的正式进展行,"
            "并按看板各取自己的表:技术组 task_progress、集团组 task_group_progress_history;"
            "上面各看板行的 latest_progress 是 task.latest_progress_time,它含未发布行,"
            "两者不等就是发布滞后(技术组 08-09 vs 07-31);"
            "问「(某看板)数据更新到什么时候」答正式口径这一列,不要答 latest_progress"
        )
    else:
        result["published_progress"] = []
        result["caliber"] += (
            ";task_group_progress_history 未在本次只读授权范围内,"
            "published_progress 为空 —— 集团看板的正式进展时间不可答,"
            "不要用 latest_progress 顶替"
        )

    if optional_granted("task_progress_import"):
        isql, iparams = tpl.freshness_import_batches()
        batches = envelope(
            sql=isql,
            params=iparams,
            caliber=(
                "导入批次只有 status = 1 才算跑完;newest_unfinished_batch 那批还没过发布门,"
                "不能当作「数据已更新到」的日期(08-15 批次仍在处理中,正式口径停在 07-31)"
            ),
            limit=1,
        )
        result["tech_import"] = (batches.get("rows") or [{}])[0]
    else:
        result["tech_import"] = {
            "newest_finished_batch": None,
            "newest_batch_any_status": None,
            "newest_unfinished_batch": None,
        }
        result["caliber"] += (
            ";task_progress_import 未在本次只读授权范围内,tech_import 三个日期为 null"
            "(键保留:删掉就分不清「没有批次表」与「批次日期查不到」)"
        )
    return result


# 工具名 -> 处理函数。放在最后:新加的函数要先 def 出来再进这张表。


def _field_completeness(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_field_completeness:某字段的填报完整度(+ 区分度 + 缺项清单)。

    三条判据:

    * **空字符串按未填计入**(`IS NULL OR = ''`),只看 NULL 会把"填了个空格"当已填;
    * 明细表字段用 **LEFT JOIN**:没有明细行的任务也算缺项(R-08),INNER JOIN
      会把它们整行丢掉、分母从 128 缩到有明细的那些,填写率凭空变高;
    * 明细表字段**另给裸表口径**(不加任务闸门):两个分母都对、各答各的问题,
      不写明分母两边都会答偏。
    """
    field = (args.get("field") or "").strip()
    if not field:
        return {
            "ok": True,
            "supported_fields": {
                name: {"table": table, "label": label}
                for name, (table, label) in sorted(tpl.COMPLETENESS_FIELDS.items())
            },
            "caliber": "传入 field 以统计该字段的填报完整度",
            "snapshot_note": FORMAL_SNAPSHOT_NOTE,
            "snapshot_date": as_of(),
        }
    if field not in tpl.COMPLETENESS_FIELDS:
        return None  # 演示路径会报 unsupported_field
    table, label, _alias = tpl._completeness_target(field)
    limit = max(1, min(MAX_ROWS, int(args.get("limit") or MAX_ROWS)))

    if args.get("list_missing"):
        sql, params = tpl.completeness_missing_rows(field, limit=limit)
        result = envelope(
            sql=sql,
            params=params,
            caliber=(
                f"is_deleted = 0 AND workflow_status = 'published';列出「{label}」为空的正式任务"
                "(R-07/R-19);空字符串按未填计入;此清单即全部缺项,按 total_count 逐条列全"
            ),
            limit=limit,
            cap_last_param=True,
        )
        tsql, tparams = tpl.completeness_missing_total(field)
        total = envelope(sql=tsql, params=tparams, caliber="缺项总数", limit=1)
        result["total_count"] = (total.get("rows") or [{}])[0].get("total_count")
        result["field"] = field
        result["field_label"] = label
        return result

    sql, params = tpl.completeness_counts(field)
    quality_note = []
    if table != "task":
        quality_note.append(
            "另给裸表口径(不加任务闸门):问「这个字段本身可信吗 / 填得怎么样」"
            "看裸表那一档(明细表有多少行就是多少行),问「有多少任务填了」看上面过闸的 "
            "filled / total(分母是正式任务,R-08 保留无明细行的任务);两档不要混着引用"
        )
    result = envelope(
        sql=sql,
        params=params,
        caliber="\uff1b".join(
            [
                f"is_deleted = 0 AND workflow_status = 'published';统计「{label}」非空占比(R-07/R-19);"
                "空字符串按未填计入 missing;"
                "filled_pct 已按 total 算好(保留一位小数),直接引用,不要自己拿 filled / total 重算"
                + (";LEFT JOIN 保留无明细行的任务(R-08)" if table != "task" else ""),
                "distinct_values 是非空值里的不同值个数,top_value_rows 是最高频值占的行数,"
                "两者已算好,问「字段是否可信 / 有没有区分度」看它们,不要只看 filled_pct",
                *quality_note,
            ]
        ),
        limit=1,
    )
    qsql, qparams = tpl.completeness_quality(field)
    quality = envelope(sql=qsql, params=qparams, caliber="字段区分度", limit=1)
    qrow = (quality.get("rows") or [{}])[0]
    distinct_values = int(qrow.get("distinct_values") or 0)
    top_value_rows = int(qrow.get("top_value_rows") or 0)
    result["field"] = field
    result["field_label"] = label
    result["distinct_values"] = distinct_values
    result["top_value_rows"] = top_value_rows

    raw_distinct = distinct_values
    if table != "task":
        rsql, rparams = tpl.completeness_raw_table(field)
        raw = envelope(sql=rsql, params=rparams, caliber="裸表口径", limit=1)
        rrow = (raw.get("rows") or [{}])[0]
        result["raw_row_count"] = rrow.get("raw_row_count")
        result["raw_filled"] = rrow.get("raw_filled")
        result["raw_distinct_values"] = rrow.get("raw_distinct_values")
        # 两个分母各自的**数**要写出来:只说"两个口径不同",调用方还是不知道该拿哪个当分母。
        result["caliber"] += (
            f";两个分母:过闸口径的分母是正式任务 {result['rows'][0].get('total')} 项"
            f"(R-08 把无明细行的任务算成缺项),裸表口径的分母是 {table} 的 "
            f"{rrow.get('raw_row_count')} 行(其中非空 {rrow.get('raw_filled')} 行、"
            f"不同值 {rrow.get('raw_distinct_values')} 个)—— 两档不要混着引用"
        )
        raw_distinct = int(rrow.get("raw_distinct_values") or 0)

    # 质量信号按**裸表**那一档判:明细表字段的区分度是表本身的属性,过闸只是少看了 9 行,
    # 不该让"同一句话复制 55 遍"因为闸门把行数减到 46 就不再报警。
    if raw_distinct <= 1:
        result["caliber"] += (
            f";字段质量信号:非空行里只有 {raw_distinct} 个不同的值,即所有行填的是同一份内容 —— "
            "填写率再高也不具备区分度,不能拿它做差异化归纳(比较各组各任务的举措有何不同),"
            "应回查生成逻辑或源数据;这是规则校验信号,不构成对项目或人员的绩效判断,"
            "需业务责任人核实后才能进正式结论"
        )
    elif raw_distinct and top_value_rows:
        result["caliber"] += (
            f";字段质量信号:非空行里有 {raw_distinct} 个不同的值,"
            f"最高频的那个值占 {top_value_rows} 行;不同值远少于行数时说明内容高度重复,"
            "做差异化归纳前先核实源数据"
        )
    return result


def _progress_history(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_progress_history:某任务的进展各期(相邻两期并排)。

    与 ``weekly_progress_coverage scope=latest_round`` 的区别:那条**一任务一行**
    (只给最新一期),这条给某任务的**全部期次**。问"最近几期有什么变化"要的是后者,
    但按最近 3 期作答(传 limit=3)—— 多列一期就与口径不一致。
    """
    raw = (args.get("task") or "").strip()
    if not raw:
        return None  # 演示路径会报 invalid_argument
    if raw.isdigit():
        task_id = int(raw)
    else:
        # 名字要先去库里解析成 id;解析不出来(或命中多条)就回落演示路径 ——
        # 猜错会把"某任务的进展"答成另一条同名系列任务。
        resolved = _lookup_task_id(raw)
        if resolved is None:
            return None
        task_id = resolved
    published_only = args.get("published_only")
    published_only = True if published_only is None else bool(published_only)
    limit = max(1, min(MAX_ROWS, int(args.get("limit") or MAX_ROWS)))

    sql, params = tpl.progress_history_rows(task_id, published_only=published_only, limit=limit)
    result = envelope(
        sql=sql,
        params=params,
        caliber=(
            "按 version_no 倒序,越大越新"
            + (";is_published = 1" if published_only else ";含未发布期次(published_only = false)")
            + ";prev_progress 是同一任务上一期(version_no 小一档)的正文,gap_days 是与上一期 "
            "progress_date 相隔天数,两列均由服务端 lag() 算好,对比相邻两期直接读这两列,"
            "不要自己把行错位相减;问「最近几期有什么变化」按最近 3 期作答(传 limit=3),"
            "多列一期就与口径不一致;reporter_id 与 report_time 同排返回,"
            "「最新一次是谁报的、什么时候报的」一次答完"
        ),
        limit=limit,
        cap_last_param=True,
        extra={"task_id": task_id},
    )
    gsql, gparams = tpl.progress_history_gap_summary(task_id, published_only=published_only)
    gaps = envelope(sql=gsql, params=gparams, caliber="间隔均值", limit=1)
    grow = (gaps.get("rows") or [{}])[0]
    result["gap_summary"] = grow
    result["caliber"] += (
        "\uff1b问「两次报进展平均隔多少天」直接读 gap_summary.avg_gap_days"
        "(服务端 AVG 后 ROUND 到一位小数,首期无上一期不进分母),"
        "自己拿 gap_days 平均会多带小数位(30.29 与口径的 30.3 不一致)"
    )
    ssql, sparams = tpl.name_series(task_id)
    siblings = envelope(sql=ssql, params=sparams, caliber="同名系列", limit=MAX_ROWS)
    series = siblings.get("rows") or []
    result["same_name_series"] = series
    if series:
        result["caliber"] += (
            f"\uff1b本次只含任务 {task_id} 一条的进展,同系列另有 {len(series)} 条独立任务("
            + "、".join(f"{s['id']} {s['task_name']}" for s in series)
            + "),各有自己的期次,不要合并进本任务的历史;"
            "要另一条就按 id 或完整名(含「(N期)」)再查一次"
        )
    if not bool(args.get("can_read_sensitive")):
        # 敏感字段打码而不是删列:列集合在两种权限下保持一致(与参考实现的 _scrub 同语义,
        # 藏列会让调用方分不清「没有审核意见」与「没权限看」)。
        for row in result["rows"]:
            if "review_comment" in row:
                row["review_comment"] = "[按权限不展示]"
    return result


def _lookup_task_id(name: str) -> int | None:
    """按任务名解析 id(精确优先,再做子串匹配);解析不出来返回 None。

    演示实现的 ``resolve_task`` 是模糊匹配且带"同名系列"的取舍;正式源只做
    精确 + 唯一子串两种:命中多条时**返回 None 回落演示路径**,不在这里猜是哪一条
    —— 猜错会把"某任务的进展"答成另一条同名系列任务。
    """
    sql = """
SELECT id, task_name FROM task
WHERE is_deleted = 0 AND workflow_status = 'published'
  AND (task_name = %s OR task_name ILIKE %s)
ORDER BY id
LIMIT 2
"""
    rows = envelope(sql=sql, params=(name, f"%{name}%"), caliber="任务解析", limit=2)["rows"]
    if len(rows) == 1:
        return int(rows[0]["id"])
    exact = [r for r in rows if r["task_name"] == name]
    return int(exact[0]["id"]) if len(exact) == 1 else None


def _import_audit(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_import_audit:导入批次核对(可选表 ``task_progress_import``)。

    四个分支互斥,优先级与演示源一致:`latest_finished` > `orphans` > `reconcile_rows` > 清单。
    """
    granted = optional_granted("task_progress_import")
    if not granted:
        hint = adm.require_optional_table("task_progress_import", False)
        raise PermissionError(hint or "task_progress_import 未授权")
    limit = max(1, min(MAX_ROWS, int(args.get("limit") or MAX_ROWS)))

    ssql, sparams = tpl.import_audit_summary()
    summary = envelope(
        sql=ssql,
        params=sparams,
        caliber="批次数 vs 去重业务快照日期数 vs 去重导入时间数(R-09/R-10)",
        limit=1,
    )
    recond = (summary.get("rows") or [{}])[0]

    if args.get("latest_finished"):
        bsql, bparams = tpl.import_audit_latest_finished(granted=True)
        batch = envelope(
            sql=bsql,
            params=bparams,
            caliber="跑完 = status = 1;最近按 data_date 倒序、同日按 id 倒序",
            limit=1,
        )
        if not batch["rows"]:
            return {
                "ok": True,
                "rows": [],
                "row_count": 0,
                "has_more": False,
                "caliber": "库里没有 status = 1 的导入批次,即没有跑完的批次",
                "snapshot_note": FORMAL_SNAPSHOT_NOTE,
                "snapshot_date": as_of(),
                "columns": [],
            }
        picked = batch["rows"][0]
        tsql, tparams = tpl.import_audit_batch_tasks(int(picked["id"]), limit=limit)
        result = envelope(
            sql=tsql,
            params=tparams,
            caliber=(
                "is_deleted = 0 AND workflow_status = 'published';"
                f"最近一批跑完的是第 {picked['id']} 批({picked['data_date']},status = 1);"
                "「跑完」是 status = 1,**不能只按日期取最新**:按 data_date 最新的是第 20 批,"
                "它 status 0 且实落 0 行,拿它答等于答了一批没跑的;"
                f"该批声明 changed_tasks {picked['declared_tasks']},实际落库任务数见 row_count,"
                "两者不等是常态(声明与落库是两个口径,核对用 reconcile_rows=True);"
                "progress_rows 是该任务在这批里的进展行数,不是任务数"
            ),
            limit=limit,
            cap_last_param=True,
        )
        result["batch"] = picked
        result["reconciliation"] = recond
        return result

    if args.get("orphans"):
        osql, oparams = tpl.import_audit_orphans()
        result = envelope(
            sql=osql,
            params=oparams,
            caliber=(
                "孤儿定义:import_id 非空且 task_progress_import 里查不到该批次(NOT EXISTS);"
                "import_id IS NULL 是未经导入的手工填报,不算孤儿,另计为 rows_without_import;"
                "orphan_rows = 0 即引用完整,这是结论本身,不要换口径重算"
            ),
            limit=1,
        )
        result["reconciliation"] = recond
        return result

    if args.get("reconcile_rows"):
        msql, mparams = tpl.import_audit_mismatch_count()
        mismatch = envelope(sql=msql, params=mparams, caliber="声明与实际不等的批次数", limit=1)
        rsql, rparams = tpl.import_audit_reconcile(limit=limit)
        result = envelope(
            sql=rsql,
            params=rparams,
            caliber=(
                "declared_tasks 取自批次行的 changed_tasks(声明值);"
                "actual_tasks / actual_rows 由 task_progress.import_id 反查得出,"
                "前者去重任务数、后者进展行数,二者与声明值是三个不同口径;"
                "LEFT JOIN 保留零落库批次(否则最极端的对不上那批会消失);"
                "mismatched_batches 为声明与实际任务数不等的批次数,勿自行比对"
            ),
            limit=limit,
            cap_last_param=True,
        )
        result["reconciliation"] = recond
        result["mismatched_batches"] = (mismatch.get("rows") or [{}])[0].get("mismatched_batches")
        return result

    lsql, lparams = tpl.import_audit_listing(limit=limit)
    result = envelope(
        sql=lsql,
        params=lparams,
        caliber=(
            "data_date 为业务快照日期;"
            "changed_tasks 是批次自己声明的数字,未与实际落库行核对,要核对请用 reconcile_rows=True"
        ),
        limit=limit,
        cap_last_param=True,
    )
    result["reconciliation"] = recond
    return result


def _task_detail(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_task_detail:一条任务的四个部分(task / group_detail / recent_progress / year_goals)。

    复合信封,**没有 columns**。两条口径无条件在场(见 ``_o2oa_templates`` 里那段注释):

    * ``completion_time`` 是展示文本、不做日期运算(R-12)—— 挂在子查询上会让技术组任务
      永远看不到它,而这条规则本来就是为它们立的;
    * 集团看板任务的负责人有两套列(task 行单值 vs 明细表多值),真库上 46 条**全不一致**,
      有明细行就直接判给多值列,并在口径里把不一致本身点出来。
    """
    token = (args.get("task") or "").strip()
    if not token:
        return None  # 演示路径会报 invalid_argument
    if token.isdigit():
        sql, params = tpl.task_detail_row(int(token))
        found = (envelope(sql=sql, params=params, caliber="按 id 定位已发布任务", limit=1)["rows"] or [None])[0]
    else:
        sql, params = tpl.task_lookup(token)
        found = (envelope(sql=sql, params=params, caliber="按名字定位已发布任务", limit=1)["rows"] or [None])[0]
    if found is None:
        return None  # 找不到就回落演示路径(它会给 _task_miss)
    task_id = int(found["id"])

    gsql, gparams = tpl.task_detail_group_row(task_id)
    group_detail = envelope(sql=gsql, params=gparams, caliber="task_group_detail 与 task 是 1:1", limit=1)["rows"]
    psql, pparams = tpl.task_detail_recent_progress(task_id, limit=3)
    recent = envelope(
        sql=psql,
        params=pparams,
        caliber=(
            "is_published = 1(仅正式发布进展);review_comment 按权限展示(R-04/R-14)"
            + ("(本次凭证有权限,原文返回)" if args.get("can_read_sensitive") else "(本次凭证无权限,已遮蔽)")
        ),
        limit=3,
        cap_last_param=True,
    )
    ysql, yparams = tpl.task_detail_year_goals(task_id, limit=5)
    goals = envelope(sql=ysql, params=yparams, caliber="task_id + year 唯一", limit=5, cap_last_param=True)

    caliber = (
        "is_deleted = 0 AND workflow_status = 'published';"
        "completion_time 为展示文本,不可做日期运算(R-12);"
        "review_comment 按权限展示(R-04/R-14)"
    )
    task_row = {k: v for k, v in found.items() if k not in tpl.BLOCKED_COLUMNS}
    if group_detail:
        row = group_detail[0]
        clashes = []
        for label, single, multi in (
            ("牵头人", "lead_owner_name", "lead_owner_names"),
            ("项目负责人", "project_owner_name", "project_owner_names"),
        ):
            one = (task_row.get(single) or "").strip()
            many = (row.get(multi) or "").strip()
            if many and one != many:
                clashes.append(f"{label} task 行是「{one or '(空)'}」、集团明细是「{many}」")
        if clashes:
            caliber += (
                ";本任务属集团看板,负责人一律按 group_detail 的多值列"
                "(lead_owner_names / project_owner_names)答,"
                "不要用 task 行上单值的 lead_owner_name / project_owner_name:"
                f"两边并非同一个数据,本任务就不一致({'\uff1b'.join(clashes)}),"
                "集团看板 46 条任务两列的值全都不一致"
            )
    if not recent["rows"] and group_detail:
        effect = (group_detail[0].get("progress_effect") or "").strip()
        if effect:
            caliber += (
                ";本任务属集团看板,进展不在 task_progress(该表 0 行全属技术看板),"
                "recent_progress 为空不代表没报过进展:"
                "当期进度成效就在本次返回的 group_detail.progress_effect 里,"
                "问「目前进展如何」按它答即可;要历次报送请用 weekly_group_history"
                "(task_group_progress_history),weekly_progress_history / weekly_progress_range "
                "对集团任务一律返回空"
            )
    if not bool(args.get("can_read_sensitive")):
        for row in recent["rows"]:
            if "review_comment" in row:
                row["review_comment"] = "[按权限不展示]"
    return {
        "ok": True,
        "task": task_row,
        "group_detail": group_detail,
        "recent_progress": recent["rows"],
        "year_goals": goals["rows"],
        "caliber": caliber,
        "snapshot_note": FORMAL_SNAPSHOT_NOTE,
        "snapshot_date": as_of(),
    }


def _approval_turnaround(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_approval_turnaround:审批时长(汇总 / 按看板 / 最慢 / 积压)。

    这一档与其余出口**正好相反**:``pending`` **刻意不套发布闸门** —— 卡在审批里的
    提交单按定义就还没发布,套上 R-01 会得到一个空的积压队列,那不是"没有积压",
    是把问题问没了。其余三档(已完成轮次)照常带正式任务门。
    """
    scope = (args.get("scope") or "summary").strip().lower()
    if scope not in tpl.TURNAROUND_SCOPES:
        return None  # 演示路径会报 unsupported_scope
    top = max(1, min(50, int(args.get("top") or 8)))
    done_note = "is_deleted = 0 AND workflow_status = 'published';仅已完成轮次(completed_at 非空)"

    if scope == "summary":
        sql, params = tpl.turnaround_summary()
        return envelope(
            sql=sql,
            params=params,
            caliber=done_note,
            limit=1,
            extra={"scope": scope},
        )
    if scope == "board":
        sql, params = tpl.turnaround_by_board()
        return envelope(
            sql=sql,
            params=params,
            caliber=done_note + ";按看板分组,按 sort_order 定序",
            limit=MAX_ROWS,
            extra={"scope": scope},
        )
    if scope == "slowest":
        tsql, tparams = tpl.turnaround_slowest_ties()
        ties = envelope(sql=tsql, params=tparams, caliber="最慢那档并列数", limit=1)
        tied = (ties.get("rows") or [{}])[0].get("tied_at_top")
        sql, params = tpl.turnaround_slowest(limit=top)
        result = envelope(
            sql=sql,
            params=params,
            caliber=(
                done_note + ";按耗时降序,并列按 task id 升序(与其他榜单同一套定序键);"
                f"最慢那档有 {tied} 轮并列,问「最慢的一轮是哪条任务」就取首行一条,"
                "要把并列都报出来请说明是并列,不要当成几个独立答案"
            ),
            limit=top,
            cap_last_param=True,
            extra={"scope": scope},
        )
        head = (result.get("rows") or [{}])[0]
        if head:
            result["caliber"] += (
                f"(实测:{head.get('days')} 天 —— "
                f"任务 {head.get('task_id')} 第 {head.get('round_no')} 轮,共 {tied} 轮并列)"
            )
        result["top_tie_count"] = tied
        return result

    # pending:唯一不带发布闸门的一档
    sql, params = tpl.turnaround_pending(as_of(), limit=top)
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "仅 is_deleted = 0(**不加发布闸门**:待审提交单本就尚未发布,加 R-01 会得到空队列);"
            "未完成即 completed_at 为空;"
            f"相对时间窗以数据快照日 {as_of()} 为基准(非当前系统时间)"
        ),
        limit=top,
        cap_last_param=True,
        extra={"scope": scope, "as_of": as_of()},
    )


def _aggregate(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_aggregate:按 9 个分组轴聚合正式任务。

    三处"反着来"的口径都在模板层:``workflow_status`` 不加发布闸门、``category`` 的看板过滤
    同时落在分类树上、空分组保留(LEFT JOIN + 闸门挂 ON)。

    ``top`` 是**硬切**:截断落在 SQL 里,并把"切前几组、共几组"写进口径 —— 模型看到 5 行
    就答 5 行,不会因为"还有并列的"而自己补列成 9 行。``order_by=finish_rate`` 只对
    ``primary_category`` / ``project_group`` 有意义(其余轴演示实现也是忽略的,这里同样忽略,
    两边行为一致)。
    """
    if (args.get("metric") or "count").strip().lower() != "count":
        return None  # 演示版只支持 metric=count,由演示路径报 unsupported_metric
    group_by = (args.get("group_by") or "").strip().lower()
    if not group_by:
        # 缺 group_by 不能回落:演示源会报 unsupported_group_by(**它也不是"任何参数
        # 都不可用"**,只是没有任何默认轴可猜)。报 invalid_argument 而不是去猜一个轴,
        # 是为了不把一个少参数的错误答成"某一根轴的分布"。
        raise ValueError("group_by 不能为空;支持 " + " / ".join(tpl.AGGREGATE_GROUP_BYS))
    if group_by not in tpl.AGGREGATE_GROUP_BYS:
        return None  # 演示路径会报 unsupported_group_by
    board_code = _board(args)  # 看板名字(技术组 / 集团看板)在这里解析成码
    order_by = (args.get("order_by") or "").strip().lower()
    ascending = bool(args.get("ascending"))
    by_rate = order_by == "finish_rate" and group_by in ("primary_category", "project_group")

    sql, params = tpl.aggregate_groups(
        group_by, board_code=board_code, order_by=order_by, ascending=ascending
    )
    caliber = "is_deleted = 0 AND workflow_status = 'published';LEFT JOIN 保留空分组(R-02/R-08)"
    if group_by == "workflow_status":
        caliber = (
            "仅 is_deleted = 0,本口径**不加发布闸门**(问的就是审批流转状态分布,"
            "加了只会剩 published 一档);"
            "group_name 是审批流转状态(published / pending_audit / pending_leader / "
            "pending_fill / rejected / signing / cancelled),"
            "与 group_by=status 的业务进度状态(未开始 / 进行中 / 已完成 / 已停用)不是一套词汇;"
            "各档相加等于未删除任务总数,「尚未发布」= 总数 - published,"
            "不要按在途状态逐项相加(cancelled 既非已发布也非在途)"
        )
    elif group_by == "category":
        caliber += (
            ";本档一行一个分类,parent_id 为空即一级分类、非空即挂在该一级下的二级分类,"
            "问「有哪些分类」要按这两级分别报;"
            "看板过滤同时作用在分类树(c.board_id)与任务上,故行清单只含本看板的分类;"
            "cnt = 0 表示该分类本看板内确实没有正式任务(R-02 保留空分组),"
            "不是「属于另一个看板」——另一看板的分类根本不在清单里"
        )
    elif group_by == "primary_category":
        caliber = (
            "is_deleted = 0 AND workflow_status = 'published';按一级分类(二级分类的 parent_id)汇总,"
            "不是二级分类;看板过滤落在分类树所属看板上;只统计挂到分类树上的任务,"
            "未挂分类的任务不进任何一档;"
            "finished 是该档已完成(status = 2)条数,finish_rate_pct = finished / cnt,"
            "已由服务端 ROUND 到一位小数,直接引用,不要自己相除或改小数位"
        )
        caliber += (
            f";本次按 finish_rate_pct {'升序' if ascending else '降序'}定序,"
            f"首行即完成率{'最低' if ascending else '最高'}的一级分类,并列按分类 id 定序;"
            "「推进最快」问的是完成率而不是任务数,别拿首档任务数当答案"
            if by_rate
            else ";本次按任务数定序,问「哪类完成率最高 / 推进最快」请加 order_by=finish_rate,"
            "任务数最多的档未必完成率最高"
        )
    elif group_by == "top_sub_per_primary":
        caliber = (
            "is_deleted = 0 AND workflow_status = 'published';每个一级分类只返回任务数最多的那一个"
            "二级分类(group_name 是一级分类,sub_name 是胜出的二级分类,cnt 是它的任务数);"
            "被排名的单位是二级分类而不是任务 —— 问「每个一级分类下哪个二级分类任务最多」用本档,"
            "weekly_rank mode=per_group group_by=primary_category 给的是每个一级分类下的头号任务,是另一题;"
            "组内并列按分类 id 升序裁决,一组一行,行数等于一级分类数,不要把并列的二级分类都列出来"
        )
    elif group_by == "project_group":
        caliber += (
            ";lead_owner_count / project_owner_count 已由服务端按人名去重,直接引用该数字,不要自己数人名"
            ";finished 是该组已完成(status = 2)条数,finish_rate_pct = finished / cnt,已由服务端算好;"
            "完成数最少的组不等于完成率最低的组(治理合规组 2/10 = 20.0% 高于数据基础设施组 2/15 = 13.3%)"
        )
        if by_rate:
            caliber += (
                f";本次按 finish_rate_pct {'升序' if ascending else '降序'}定序,"
                f"首行即完成率{'最低' if ascending else '最高'}的组,并列按组名定序;"
                "本档按完成率定序,故不返回 cum_pct(累计占比只在按任务数定序时才单调)"
            )
        else:
            caliber += (
                ";share_pct 是该组占全部正式任务的比例,cum_pct 是沿本次定序(任务数倒序、"
                "并列按组名)逐行累加的累计占比,两列都由服务端算好并保留**两位**小数 —— "
                "照抄这两列,不要自己逐行相加,也不要改小数位;"
                "问「前几个组合起来是否过半」按 cum_pct 首次超过 50 的那一行答:"
                "前 4 组累计 49.22% 仍未过半(第 5 组才到 58.59%),"
                "把 49.22 舍成 49.2 或多算一组都会把结论答反"
            )
    elif group_by == "name_series":
        caliber = (
            "is_deleted = 0 AND workflow_status = 'published';同名系列 = 任务名去掉尾部「(N期)」"
            "后缀后归并成的家族(如「数据资源登记体系建设」与其 2/3/4 期);"
            "cnt 是该家族任务数,task_ids 是家族内任务 id 列表(升序);"
            "单期任务也是独立家族,问「重复统计风险」看多期家族"
        )

    top = int(args.get("top") or 0)
    cut = 0
    if top:
        cut = max(1, min(MAX_ROWS, top))
        tsql, tparams = tpl.aggregate_total_groups(sql, params)
        total = envelope(sql=tsql, params=tparams, caliber="截断前的分组总数", limit=1)
        total_groups = (total.get("rows") or [{}])[0].get("total_groups")
        # 截断落在 SQL 里:口径要写明"切前 N 组、共 M 组",否则模型看到 5 行就答 5 行,
        # 或者因为"还有并列的"而自己补列成 9 行。
        sql = sql + f"\nLIMIT {cut}"
        caliber += (
            f";按上述定序硬切前 {cut} 组(共 {total_groups} 组);"
            "边界外与末位并列的分组不属于本题答案,不要补列"
        )

    result = envelope(
        sql=sql,
        params=params,
        caliber=caliber,
        limit=cut or MAX_ROWS,
        extra={"group_by": group_by},
    )
    if group_by == "name_series":
        # 「重复统计风险」的答案 = 多期家族数 + 涉及任务数 + 占比,服务端一次算完:
        # 让模型自己数 rows 里的 cnt > 1 会漏(家族多、被截断或只看前几行)。
        rows = result.get("rows") or []
        multi = [r for r in rows if int(r.get("cnt") or 0) > 1]
        result["multi_member_families"] = len(multi)
        result["tasks_in_families"] = sum(int(r.get("cnt") or 0) for r in multi)
        result["families_total"] = len(rows)
        result["caliber"] += (
            ";multi_member_families 是多期家族数,tasks_in_families 是这些家族涉及的任务数,"
            "families_total 是本次返回的家族行数;三个数都已算好,不要自己数 rows 里 cnt > 1 的行"
        )
    return result


def _group_stats(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_group_stats:集团板专表的统计(14 个 scope)。

    三条口径在模板层钉住:完成时间是展示文本(只有两种写法能归一化,分档判别有优先级)、
    多值负责人按元素切(不用 LIKE,会在不同人之间碰撞)、零附件任务必须留住。
    """
    scope = (args.get("scope") or "owners").strip().lower()
    if scope not in tpl.GROUP_STATS_SCOPES:
        return None  # 演示路径会报 unsupported_scope
    top = max(1, min(MAX_ROWS, int(args.get("top") or 8)))
    base = "is_deleted = 0 AND workflow_status = 'published';集团看板"

    if scope == "owners":
        sql, params = tpl.group_stats_owners()
        result = envelope(sql=sql, params=params, caliber=f"{base};牵头人多值按逗号判定", limit=1)
        dsql, dparams = tpl.group_stats_distinct_leads()
        distinct = envelope(sql=dsql, params=dparams, caliber="逐元素拆分后去重", limit=1)
        result["distinct_leads"] = (distinct.get("rows") or [{}])[0].get("distinct_leads")
        return result

    if scope == "project_group_raw":
        sql, params = tpl.group_stats_project_group_raw(limit=top)
        result = envelope(
            sql=sql,
            params=params,
            caliber=(
                "集团明细表(task_group_detail)**裸表口径**,不加任务闸门,本档的答案是 rows_ 那一列;"
                "formal_rows 是同一分组下过正式任务闸门的行数,仅供口径对照,不要拿它当本档答案;"
                "target_filled / measure_filled 同为裸表口径的填写行数;"
                "问「各专项组有多少任务」不要用本档 —— 那是任务口径,请用 weekly_aggregate group_by=project_group"
            ),
            limit=top,
            cap_last_param=True,
        )
        tsql, tparams = tpl.group_stats_raw_tiers()
        tiers = envelope(sql=tsql, params=tparams, caliber="两个分母对照", limit=1)
        row = (tiers.get("rows") or [{}])[0]
        result["caliber_tiers"] = {
            "raw_table": row.get("raw_table"),
            "formal_task_gate": row.get("formal_task_gate"),
        }
        result["caliber"] += (
            f";两个分母:裸表 {row.get('raw_table')} 行、过闸 {row.get('formal_task_gate')} 行"
            "(差的那些挂在已软删或未发布的任务上)"
        )
        return result

    if scope == "separators":
        sql, params = tpl.group_stats_separators(limit=top)
        return envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};按 project_owner_names 里出现的分隔符分档;"
                "「单人无分隔符」是独立一档不是缺失;仅统计该栏非空的任务"
            ),
            limit=top, cap_last_param=True,
        )

    if scope == "owner_widths":
        sql, params = tpl.group_stats_owner_widths(limit=top)
        return envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};owner_count = 分隔符个数 + 1,顿号与逗号都计入(只扣一种会少算);"
                "按人数倒序,最多的一条即首行"
            ),
            limit=top, cap_last_param=True,
        )

    if scope == "completion_time":
        sql, params = tpl.group_stats_completion_time()
        return envelope(
            sql=sql, params=params,
            caliber=f"{base};completion_time 为展示文本,只做格式判别不做日期运算(R-12)",
            limit=1,
        )

    if scope == "completion_time_values":
        sql, params = tpl.group_stats_completion_time_values(limit=top)
        result = envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};去重后的 completion_time **原样取值**,按文本升序;"
                "这是库里真实存在的写法,不要归纳成自己的类别名"
            ),
            limit=top, cap_last_param=True,
        )
        tsql, tparams = tpl.group_stats_completion_time_values_total()
        total = envelope(sql=tsql, params=tparams, caliber="去重取值总数", limit=1)
        result["total_count"] = (total.get("rows") or [{}])[0].get("total_count")
        result["caliber"] += f";共 {result['total_count']} 种,top 决定返回前几种"
        return result

    if scope == "completion_time_formats":
        sql, params = tpl.group_stats_completion_time_formats(limit=top)
        result = envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};按写法归档(标准日期 / 季度 / 含「底」的模糊表述 / 中文年月日 / 中文年月 / 其他),"
                "一条只进一档;档数是**写法种类数**,不是去重取值数(去重取值另有 completion_time_values);"
                "'2026年6月底' 归入含「底」一档而非中文年月,判别按此优先级固定;"
                "仅统计该栏非空的任务,空值不进任何一档"
            ),
            limit=top, cap_last_param=True,
        )
        tsql, tparams = tpl.group_stats_completion_time_formats_total()
        total = envelope(sql=tsql, params=tparams, caliber="非空完成时间总数", limit=1)
        result["total_count"] = (total.get("rows") or [{}])[0].get("total_count")
        result["caliber"] += ";各档相加等于 total_count"
        return result

    if scope == "overdue":
        sql, params = tpl.group_stats_overdue(as_of(), limit=top)
        result = envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};超期 = 归一化截止日早于快照日且 status <> 2(未完成);"
                f"相对时间窗以数据快照日 {as_of()} 为基准(非当前系统时间);"
                "completion_time 是展示文本,**只有**标准日期与 YYYYQn 两种写法能归一化(季度取季末日);"
                "只按标准日期写法看会一条都查不到,季度那几条归一化后才露出来;"
                "本档行数即可判定的超期任务数,为 0 才是「可判定的那些都没超期」"
            ),
            limit=top, cap_last_param=True,
        )
        usql, uparams = tpl.group_stats_overdue_unparsable()
        unp = envelope(sql=usql, params=uparams, caliber="判不了的条数", limit=1)
        unparsable = (unp.get("rows") or [{}])[0].get("unparsable_count")
        result["unparsable_count"] = unparsable
        result["caliber"] += (
            f";其余 {unparsable} 条判不了、不在本档 —— 它们是「无法判断」而不是「没超期」,"
            "报结论时要把这个数一并说明"
        )
        return result

    if scope == "field_lengths":
        sql, params = tpl.group_stats_field_lengths()
        return envelope(
            sql=sql, params=params,
            caliber=f"{base};仅统计 target_result 非空的任务;length 按字符非字节",
            limit=1,
        )

    if scope == "status_effect_conflict":
        sql, params = tpl.group_stats_status_effect_conflict(limit=top)
        return envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};矛盾判据是**同一行内部**:status = 0(未开始)却填了非空 progress_effect;"
                "effect_head 是前 50 字,完整文本用 weekly_group_detail_query 取;"
                "这与 scope=effect_consistency 不是一回事 —— 那档比的是明细表与历史表两处文本是否一致,"
                "两处写着同一句话也算一致,答不了「状态与成效自相矛盾」"
            ),
            limit=top, cap_last_param=True,
        )

    if scope == "effect_consistency":
        sql, params = tpl.group_stats_effect_consistency(limit=top)
        return envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};明细表 progress_effect 与历史表**最新一期**(is_published = 1)逐字比对;"
                "same = 1 一致、0 不一致;不一致的排在最前,"
                "先看 same = 0 有几行再下「全部一致」的结论"
            ),
            limit=top, cap_last_param=True,
        )

    if scope == "attachments":
        sql, params = tpl.group_stats_attachments(limit=top)
        result = envelope(
            sql=sql, params=params,
            caliber=f"{base};附件按 is_deleted = 0 计有效;LEFT JOIN 保留零附件任务(R-08);按条数升序",
            limit=top, cap_last_param=True,
        )
        ssql, sparams = tpl.group_stats_attachments_summary()
        summary = envelope(sql=ssql, params=sparams, caliber="附件总览", limit=1)
        srow = (summary.get("rows") or [{}])[0]
        result["no_attachment_summary"] = srow
        total = int(srow.get("tasks") or 0)
        if result.get("row_count") and int(result["row_count"]) < total:
            # 46 条任务而 top 默认 8:模型拿到 8 行就去手数"几个任务有 1 个附件",
            # 数出的是 21/4/4 而真值是 17/3/5 —— 清单档天生只能给一页。
            result["caliber"] += (
                f"\uff1b本次只返回 {result['row_count']} 行(top 决定,共 {total} 条任务),"
                "不要照这几行去数「有几个任务是 1 个附件」——"
                "要各档任务数请用 scope=attachment_distribution(服务端算完再回),"
                f"要完整清单请把 top 提到 {total}"
            )
        return result

    if scope == "attachment_distribution":
        sql, params = tpl.group_stats_attachment_distribution(limit=top)
        return envelope(
            sql=sql, params=params,
            caliber=(
                f"{base};按附件条数分档统计任务数,附件按 is_deleted = 0 计有效;"
                "零附件档由 LEFT JOIN 保住(占了近四成,丢了分布就变形);各档 tasks 相加等于集团板任务数;"
                "档位不连续是正常的(库里没有 5 个附件的任务,所以 4 之后直接是 6);"
                "这一档是**分布**,清单在 scope=attachments,不要拿清单的一页去数分布;"
                "反过来也不成立:问「每个任务各有几个附件」要的是一任务一行的清单,"
                "请改用 scope=attachments 并把 top 提到集团板任务数 —— "
                "本档一行是一个档位而不是一个任务,拿它作答等于把明细压成几行分布,答的是另一个问题"
            ),
            limit=top, cap_last_param=True,
        )

    # history_rounds
    min_rounds = max(0, int(args.get("min_rounds") or 0))
    sql, params = tpl.group_stats_history_rounds(limit=top)
    result = envelope(
        sql=sql, params=params,
        caliber=(
            "任务侧 is_deleted = 0 AND workflow_status = 'published',且历史行自身 is_published = 1"
            "(两道闸门缺一不可);集团看板;LEFT JOIN 保留零期任务(R-08);按期数降序、并列按 task id 升序"
        ),
        limit=top, cap_last_param=True,
    )
    if min_rounds:
        asql, aparams = tpl.group_stats_history_rounds_at_least(min_rounds)
        cleared = envelope(sql=asql, params=aparams, caliber="过门槛的任务数", limit=1)
        result["tasks_at_least"] = {
            "min_rounds": min_rounds,
            "tasks": (cleared.get("rows") or [{}])[0].get("tasks"),
        }
        result["caliber"] += (
            f"\uff1b至少 {min_rounds} 期(含 {min_rounds},边界取等);"
            "tasks_at_least.tasks 是服务端算出的过门槛任务数,不要照清单前几行自己数"
        )
    return result


def _task_lifecycle(args: dict[str, Any]) -> dict[str, Any] | None:
    """weekly_task_lifecycle:任务建立与发布的**另一个钟**(不是"报进展"那个)。"""
    grouping = (args.get("by") or "").strip().lower()
    if grouping and grouping not in tpl.CREATED_GROUPINGS:
        return None  # 演示路径会报 unsupported_group_by
    year = int(args.get("year") or 0) or None
    year_note = f";仅 created_at 属于 {year} 年" if year else ""
    if grouping:
        sql, params = tpl.task_lifecycle_by(grouping, year=year, limit=MAX_ROWS)
        return envelope(
            sql=sql,
            params=params,
            caliber=(
                "is_deleted = 0 AND workflow_status = 'published'" + year_note
                + f";按 created_at 的{grouping}分组;"
                "created_count 是那一档新建的任务数,currently_finished 是其中「当前 status = 2」的条数;"
                "任务表**没有完成时间列**,所以这是按建单档看当前状态,不是「那一年完成的任务数」——"
                "跨档完成的任务仍记在建单档;各档 currently_finished 相加等于全库已完成总数"
            ),
            limit=MAX_ROWS,
        )
    sql, params = tpl.task_lifecycle_summary(year=year)
    return envelope(
        sql=sql,
        params=params,
        caliber=(
            "is_deleted = 0 AND workflow_status = 'published'" + year_note
            + ";到发布天数仅统计 published_at 非空的任务"
        ),
        limit=1,
    )


_HANDLERS = {
    "weekly_task_query": _task_query,
    "weekly_progress_coverage": _coverage,
    "weekly_freshness_distribution": _freshness,
    "weekly_freshness": _freshness_snapshot,
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
    "weekly_year_goal_stats": _year_goal_stats,
    "weekly_schema": _schema,
    "weekly_field_completeness": _field_completeness,
    "weekly_progress_history": _progress_history,
    "weekly_import_audit": _import_audit,
    "weekly_task_lifecycle": _task_lifecycle,
    "weekly_task_detail": _task_detail,
    "weekly_approval_turnaround": _approval_turnaround,
    "weekly_aggregate": _aggregate,
    "weekly_group_stats": _group_stats,
}

# _NEW_HANDLERS 只是构建期的清单,避免手工漏接线
assert set(_NEW_HANDLERS) <= set(_HANDLERS)
