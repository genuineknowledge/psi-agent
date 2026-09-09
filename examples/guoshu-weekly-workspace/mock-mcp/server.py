"""Mock weekly-report MCP service for the demo.

This stands in for the 入口组 service until it exists.  It speaks the *same*
semantic tool contract described in chapter 三 of the plan, so switching to the
real service is a `GUOSHU_WEEKLY_MCP_URL` change with no agent-side edits.

Run:
    python server.py --port 18900
"""

# ruff: noqa: RUF001, RUF003  中文口径文案里的全角标点是给模型看的字面量, 不能换成半角。
# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _db
import _store as store
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("guoshu-weekly-mock")

BEARER_TOKEN = os.environ.get("GUOSHU_WEEKLY_MOCK_TOKEN", "demo-token")
SENSITIVE_TOKEN = os.environ.get("GUOSHU_WEEKLY_MOCK_ADMIN_TOKEN", "demo-admin-token")


def _caller_may_read_sensitive(ctx: Context | None) -> bool:
    """Decide sensitive-field access from the caller's bearer token.

    R-04/R-14 say approval opinions are returned *by permission* -- blanket
    redaction fails the requirement just as surely as blanket exposure does, and
    it also makes the capability untestable.  The decision is taken here, from
    the transport's Authorization header, because that is the one input the model
    cannot influence: nothing a user or a prompt says can widen this.

    In production the header maps to an OA identity and a row-level policy; the
    demo has two fixed tokens so the two branches are both exercisable.
    """
    if ctx is None:
        return False
    request = getattr(ctx.request_context, "request", None)
    if request is None:
        return False
    headers = getattr(request, "headers", None) or {}
    raw = headers.get("authorization") or headers.get("Authorization") or ""
    token = raw.removeprefix("Bearer").removeprefix("bearer").strip()
    return bool(token) and token == SENSITIVE_TOKEN


def _days_between(lo: str, hi: str) -> int:
    """Span of an inclusive YYYY-MM-DD window in days, 0 if either side is unparseable."""
    try:
        return (date.fromisoformat(hi[:10]) - date.fromisoformat(lo[:10])).days
    except ValueError:
        return 0


def _progress_mom(
    store_: Any,
    select: str,
    where: str,
    params: dict[str, Any],
    caliber: str,
    grouping: str,
    limit: int,
) -> dict[str, Any]:
    """Wrap a month/quarter bucket count with its previous bucket and the delta.

    "环比变化" is a question about adjacent PAIRS, not about a list of counts. A
    caller handed only the per-bucket counts has to line the rows up itself, and
    misreading which bucket is the previous one is exactly how K3-03 reported
    2026-06 as 61 when it is 48. LAG runs server-side over the bucket order so
    the pairing is not a judgement call.

    The first bucket's prev_count and change are NULL on purpose: there is no
    earlier bucket to compare against, and filling it with 0 would invent a
    100% drop.
    """
    label = "月" if grouping == "month" else "季"
    inner = f"SELECT {select} FROM task_progress p JOIN task t ON t.id = p.task_id WHERE {where} GROUP BY bucket"
    return store_.fetch(
        # mom_change 不叫 change:change 是 MySQL 保留字,裸用会语法错。
        "SELECT bucket, progress_count, task_count, "
        "LAG(progress_count) OVER (ORDER BY bucket) AS prev_count, "
        "progress_count - LAG(progress_count) OVER (ORDER BY bucket) AS mom_change "
        f"FROM ({inner}) buckets ORDER BY bucket",
        params,
        caliber=caliber
        + (
            f"；prev_count 是上一{label}的条数、mom_change 是本{label}减上一{label}，"
            "两列由服务端按时序 LAG 算好，环比直接读 mom_change，不要自己把行错位相减；"
            f"首{label}的 prev_count 与 mom_change 为空是对的（没有上一{label}可比）；"
            "问「降幅最大的是哪个月」取 mom_change 最小（最负）的那行，不是绝对值最大；"
            f"prev_count 只在本次窗口内取上一{label}：问「2026 年的环比」要把窗口限定在该年"
            "（传 date_from=2026-01-01），不限定则首档会取到上一年的数（2026-01 拿到 2025-12 的 58），"
            "那是跨年口径，不是该年的环比"
        ),
        limit=limit,
    )


def _task_miss(task: str) -> dict[str, Any]:
    """Build the formal-task miss error, saying WHICH kind of miss it is.

    The bare 「未匹配到正式任务」 is a dead end: it reads the same whether the id
    does not exist or exists outside the formal set, and it does not say that
    other tools still answer. Both cost rounds -- M2-01 burned its whole budget
    arbitrating between this error, a name search that landed on the later-phase
    siblings, and the submission tools that answered about the task fine.
    """
    reason = store.task_miss_reason(task)
    if reason.get("kind") == "not_formal":
        deleted = int(reason.get("is_deleted") or 0)
        cause = "已删除（is_deleted = 1）" if deleted else f"workflow_status = '{reason.get('workflow_status')}'"
        return {
            "ok": False,
            "error": {
                "code": "task_not_formal",
                "message": (
                    f"任务 {reason.get('task_id')}「{reason.get('task_name')}」存在但不属正式任务："
                    f"{cause}，未过 R-01（is_deleted = 0 AND workflow_status = 'published'）。"
                    "本工具按正式任务口径取数，故不返回它；"
                    "它的提交单、审批动作、附件挂在 task_id 外键上，"
                    "weekly_submission_query / weekly_workflow_query / weekly_attachment_query "
                    "按 task 传同一个 id 仍可查到。"
                    "不要改用按名字搜——同名系列的（N期）是另外几条任务，答的不是这一条"
                ),
            },
        }
    if reason.get("kind") == "absent":
        return {
            "ok": False,
            "error": {
                "code": "task_not_found",
                "message": f"库中无此任务 id：{task}（不是口径过滤掉的，是确实没有这行）",
            },
        }
    return {"ok": False, "error": {"code": "task_not_found", "message": f"未匹配到正式任务：{task}"}}


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(code: str, message: str) -> str:
    return _dump({"ok": False, "error": {"code": code, "message": message}})


def _guard(func_name: str, work) -> str:
    try:
        return _dump(work())
    except store.QueryError as exc:
        return _error(exc.code, str(exc))
    except Exception as exc:
        return _error("internal_error", f"{func_name}: {type(exc).__name__}")


@mcp.tool()
def weekly_schema(board: str = "") -> str:
    """List boards, category trees and the field dictionary with caliber notes.

    Args:
        board: Optional board code (tech/group) or name to scope the category tree.
    """

    def work() -> dict[str, Any]:
        boards = store.fetch(
            "SELECT id, name, code, sort_order FROM task_board WHERE is_deleted = 0 ORDER BY sort_order, id",
            caliber="is_deleted = 0",
        )
        params: dict[str, Any] = {}
        where = "c.is_deleted = 0"
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {"ok": False, "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"}}
            where += " AND c.board_id = %(bid)s"
            params["bid"] = board_id
        categories = store.fetch(
            "SELECT c.id, c.board_id, c.parent_id, c.name, c.sort_order "
            f"FROM task_category c WHERE {where} ORDER BY c.board_id, c.parent_id, c.sort_order",
            params,
            caliber="is_deleted = 0；parent_id 为空是一级分类",
        )
        # Column lists answer "which fields does table X have" without the agent
        # having to reverse-engineer them from a sample row (it guessed 8 of 9
        # that way and missed is_deleted).  Blocked fields are filtered out here,
        # so they are absent from the schema as well as from the data.
        columns = store.fetch(
            "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
            "DATA_TYPE AS data_type, COLUMN_COMMENT AS comment "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %(db)s AND COLUMN_NAME NOT IN %(blocked)s "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            {"db": _db.DB_NAME, "blocked": tuple(sorted(store.BLOCKED_FIELDS))},
            caliber=f"已排除禁止外泄字段：{', '.join(sorted(store.BLOCKED_FIELDS))}",
            limit=store.MAX_ROWS,
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
                "formal_task": store.FORMAL_TASK_CALIBER,
                "status": "0未开始 / 1进行中 / 2已完成 / 3已停用",
                "completion_time": "展示文本，不可做日期运算（R-12）",
                "owner_multi_value": "分管领导等为多值分隔文本，须去空格后匹配（R-13）",
                "blocked_fields": sorted(store.BLOCKED_FIELDS),
                "sensitive_fields": sorted(store.SENSITIVE_FIELDS),
                "table_columns_note": "已剔除禁止外泄字段，故此清单即可对外引用的全部字段",
            },
            "snapshot_note": store.SNAPSHOT_NOTE,
            "snapshot_date": store.AS_OF,
        }

    return _guard("weekly_schema", work)


@mcp.tool()
def weekly_task_query(
    board: str = "",
    category: str = "",
    status: str = "",
    owner: str = "",
    keyword: str = "",
    project_group: str = "",
    limit: int = 200,
) -> str:
    """Query formal tasks. Applies R-01 (is_deleted=0 AND published) server-side.

    Args:
        board: Board code (tech/group) or name.
        category: Category name, matched loosely.
        status: Business status 0/1/2/3, empty for all.
        owner: Owner or lead name; multi-value columns are matched per R-13.
        keyword: Substring of the task name.
        project_group: 专项组 name, matched exactly. 专项组 is its own column and
            is not a category or a board -- filtering it through ``category`` or
            ``keyword`` silently returns the wrong set.
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        where = [store.formal_task_clause()]
        params: dict[str, Any] = {}
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {"ok": False, "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"}}
            where.append("t.board_id = %(bid)s")
            params["bid"] = board_id
        if category.strip():
            where.append("t.category_id IN (SELECT id FROM task_category WHERE is_deleted = 0 AND name LIKE %(cat)s)")
            params["cat"] = f"%{category.strip()}%"
        if status.strip():
            if status.strip() not in {"0", "1", "2", "3"}:
                return {
                    "ok": False,
                    "error": {"code": "invalid_status", "message": "status 只能是 0/1/2/3"},
                }
            where.append("t.status = %(st)s")
            params["st"] = int(status.strip())
        if owner.strip():
            # All six owner columns: three ids and three names. Covering only the
            # name columns for lead/project silently dropped tasks where the
            # person is recorded by id alone (task 150 holds lead_owner_id
            # 'u3208' with lead_owner_name empty) -- 7 rows instead of 8.
            #
            # Ids match exactly; names match as substrings because they are
            # multi-value text. R-13: strip spaces on both sides first.
            token = owner.strip().replace(" ", "")
            where.append(
                "(REPLACE(IFNULL(t.owner_user_id,''),' ','') = %(own_exact)s "
                "OR REPLACE(IFNULL(t.project_owner_id,''),' ','') = %(own_exact)s "
                "OR REPLACE(IFNULL(t.lead_owner_id,''),' ','') = %(own_exact)s "
                "OR REPLACE(IFNULL(t.project_owner_name,''),' ','') LIKE %(own)s "
                "OR REPLACE(IFNULL(t.lead_owner_name,''),' ','') LIKE %(own)s)"
            )
            params["own_exact"] = token
            params["own"] = f"%{token}%"
        if keyword.strip():
            where.append("t.task_name LIKE %(kw)s")
            params["kw"] = f"%{keyword.strip()}%"
        if project_group.strip():
            # 专项组是 task 上的独立一列，精确匹配。没有这个过滤器时，
            # 「某组的牵头人都有谁」只能整表拉回来自己筛，而清单封顶 200 行，
            # 模型要么答成全库人名要么说数据被截断。
            where.append("t.project_group = %(pg)s")
            params["pg"] = project_group.strip()

        clause = " AND ".join(where)
        caliber = store.FORMAL_TASK_CALIBER
        if project_group.strip():
            caliber += f"；专项组 = {project_group.strip()}（t.project_group 精确匹配，非分类也非看板）"
        total = store.scalar(
            f"SELECT COUNT(*) FROM task t WHERE {clause}",
            params,
            caliber=caliber,
        )
        rows = store.fetch(
            "SELECT t.id, t.task_no, t.task_name, t.board_id, t.category_id, t.status, "
            "t.project_owner_name, t.lead_owner_name, t.project_group, "
            "t.latest_progress_time, t.published_at "
            f"FROM task t WHERE {clause} ORDER BY t.board_id, t.sort_order, t.id",
            params,
            caliber=caliber,
            limit=limit,
        )
        rows["total_count"] = total["value"]
        # 本工具回的 lead_owner_name / project_owner_name 是 task 行上的单值列。
        # 集团看板 46 条任务这两列与集团明细表的多值列全都不一致（任务 101 这里是
        # 「陈志远」，明细表是「刘海涛,韩雪峰」），而返回里此前没有任何提示，
        # 问「某个集团任务的牵头人是谁」照这两列答就答错了人（R8-02）。
        # 提示只在结果真含集团看板任务时才挂，免得给技术看板的问答添噪声。
        group_hits = [r for r in (rows.get("rows") or []) if str(r.get("board_id")) == "2"]
        if group_hits:
            rows["group_board_owner_note"] = (
                f"结果里有集团看板任务（board_id = 2，共 {len(group_hits)} 条）。"
                "本工具的 lead_owner_name / project_owner_name 是 task 行上的单值列，"
                "集团看板这两列与集团明细表的多值列 lead_owner_names / project_owner_names "
                "全部不一致。问集团看板任务的牵头人／项目负责人请改用 "
                "weekly_group_detail_query 取多值列，或 weekly_task_detail 看它同时返回的两套列，"
                "不要照本工具这两列作答。"
            )
        return rows

    return _guard("weekly_task_query", work)


@mcp.tool()
def weekly_task_detail(task: str, ctx: Context | None = None) -> str:
    """Fetch one formal task with its group detail, latest progress and year goal.

    Args:
        task: Task id or task name (fuzzy).
    """
    may_read = _caller_may_read_sensitive(ctx)

    def work() -> dict[str, Any]:
        found = store.resolve_task(task)
        if found is None:
            return _task_miss(task)
        task_id = int(found["id"])
        detail = store.fetch(
            "SELECT * FROM task_group_detail WHERE task_id = %(tid)s",
            {"tid": task_id},
            caliber="completion_time 为展示文本，不可做日期运算（R-12）",
            limit=1,
        )
        progress = store.fetch(
            "SELECT id, task_id, version_no, latest_progress, next_work, progress_date, "
            "report_time, is_published, review_comment "
            "FROM task_progress WHERE task_id = %(tid)s AND is_published = 1 "
            "ORDER BY version_no DESC, id DESC",
            {"tid": task_id},
            caliber="is_published = 1（仅正式发布进展）；review_comment 按权限展示"
            + ("（本次凭证有权限，原文返回）" if may_read else "（本次凭证无权限，已遮蔽）"),
            can_read_sensitive=may_read,
            limit=3,
        )
        goal = store.fetch(
            "SELECT * FROM task_year_goal WHERE task_id = %(tid)s ORDER BY year DESC",
            {"tid": task_id},
            caliber="task_id + year 唯一",
            limit=5,
        )
        # 集团看板的进展不在 task_progress 里（那张表 0 行全属技术看板），而在
        # task_group_detail.progress_effect 与 task_group_progress_history。
        # 空的 recent_progress 不说明这一点，就会被当成「这任务没报过进展」，
        # 于是模型在 progress_history / milestone_stats 之间来回试——Q1-02 那 6 轮
        # 13 次调用就是这么耗掉的，而答案其实已经在本次返回的 group_detail 里。
        # 有 group_detail 行本身就等于「这是集团看板任务」——那张表只覆盖集团看板，
        # 所以不必再回查 board.code。
        # 本工具一次返回两套负责人列：task 行上的单值 lead_owner_name /
        # project_owner_name，和 group_detail 里的多值 lead_owner_names /
        # project_owner_names。集团看板 46 条任务两边的值都不一样（101 号任务
        # task 行写「陈志远」，集团明细写「刘海涛,韩雪峰」），谁在上面谁就被当成
        # 答案——R8-02 就是照 task 行答了「陈志远」。哪一列对不该让模型猜，
        # 有 group_detail 行就直接判给多值列。
        owners = ""
        if detail["rows"]:
            row = detail["rows"][0]
            pairs = [
                ("牵头人", "lead_owner_name", "lead_owner_names"),
                ("项目负责人", "project_owner_name", "project_owner_names"),
            ]
            clashes = []
            for label, single, multi in pairs:
                one = (found.get(single) or "").strip()
                many = (row.get(multi) or "").strip()
                if many and one != many:
                    clashes.append(f"{label} task 行是「{one or '(空)'}」、集团明细是「{many}」")
            if clashes:
                owners = (
                    "；本任务属集团看板，负责人一律按 group_detail 的多值列"
                    "（lead_owner_names / project_owner_names）答，"
                    "不要用 task 行上单值的 lead_owner_name / project_owner_name："
                    f"两边并非同一个数据，本任务就不一致（{'；'.join(clashes)}），"
                    "集团看板 46 条任务两列的值全都不一致"
                )
        extra = ""
        if not progress["rows"] and detail["rows"]:
            effect = (detail["rows"][0].get("progress_effect") or "").strip()
            if effect:
                extra = (
                    "；本任务属集团看板，进展不在 task_progress（该表 0 行全属技术看板），"
                    "recent_progress 为空不代表没报过进展："
                    "当期进度成效就在本次返回的 group_detail.progress_effect 里，"
                    "问「目前进展如何」按它答即可；"
                    "要历次报送请用 weekly_group_history（task_group_progress_history），"
                    "weekly_progress_history / weekly_progress_range 对集团任务一律返回空"
                )
        return {
            "ok": True,
            "task": {k: v for k, v in found.items() if k not in store.BLOCKED_FIELDS},
            "group_detail": detail["rows"],
            "recent_progress": progress["rows"],
            "year_goals": goal["rows"],
            # R-12 must be stated unconditionally: task_group_detail only covers
            # the group board, so hanging this note off that sub-query's caliber
            # silently dropped it for every tech-board task -- exactly the case
            # the rule exists to guard.
            "caliber": (
                f"{store.FORMAL_TASK_CALIBER}；"
                "completion_time 为展示文本，不可做日期运算（R-12）；"
                "review_comment 按权限展示（R-04/R-14）" + owners + extra
            ),
            "snapshot_note": "演示数据（weekly_mock 自建库），非集团真实周报",
            "snapshot_date": store.AS_OF,
        }

    return _guard("weekly_task_detail", work)


@mcp.tool()
def weekly_progress_history(
    task: str, published_only: bool = True, limit: int = 200, ctx: Context | None = None
) -> str:
    """Return progress versions for one task, newest first (version_no desc).

    Args:
        task: Task id or name.
        published_only: True keeps only is_published=1 rows (formal progress).
        limit: Max rows, capped at 200.
    """
    may_read = _caller_may_read_sensitive(ctx)

    def work() -> dict[str, Any]:
        task_id = store.resolve_task_id(task)
        if task_id is None:
            return _task_miss(task)
        where = "task_id = %(tid)s"
        caliber = "按 version_no 倒序，越大越新"
        if published_only:
            where += " AND is_published = 1"
            caliber += "；is_published = 1"
        # 「这几期有什么变化」要的是相邻两期并排，不是各期原文。只给各期让模型
        # 自己错位对照，它会把上一期的正文抄串行,或干脆不给对照列。prev_progress
        # 与 gap_days 由服务端用 LAG 算好,按 version_no 升序取前一期。
        # reporter_id 与 report_time 同排返回：问「最新一次进展是谁报的、什么时候报的」
        # 本是一问，此前这里只给 report_time，填报人得另查 weekly_submission_query，
        # 两次调用的行集不一定对齐（提交单按轮次、进展按期号），容易把人和时间配错。
        rows = store.fetch(
            "SELECT id, task_id, version_no, latest_progress, next_work, progress_date, "
            "report_time, reporter_id, is_published, review_comment, "
            "LAG(latest_progress) OVER (ORDER BY version_no) AS prev_progress, "
            "DATEDIFF(progress_date, LAG(progress_date) OVER (ORDER BY version_no)) AS gap_days "
            f"FROM task_progress WHERE {where} ORDER BY version_no DESC, id DESC",
            {"tid": task_id},
            caliber=caliber + "；prev_progress 是同一任务上一期（version_no 小一档）的正文，"
            "gap_days 是与上一期 progress_date 相隔天数，两列均由服务端 LAG 算好，"
            "对比相邻两期直接读这两列，不要自己把行错位相减；"
            "问「最近几期有什么变化」按最近 3 期作答（传 limit=3），"
            "多列一期就与口径不一致"
            + ("；review_comment 原文返回（本次凭证有敏感字段权限）" if may_read else "；review_comment 已按权限遮蔽"),
            can_read_sensitive=may_read,
            limit=limit,
        )
        # 「平均隔多少天」的均值也由服务端 ROUND 到一位小数。让模型拿 gap_days
        # 自己平均,结果是 30.285714,报成 30.29 而口径是 30.3——与完成率 24.22
        # vs 24.2 同一族的毛病:小数位由谁定。首期 gap 为 NULL 不进分母。
        gaps = store.fetch(
            "SELECT ROUND(AVG(gap_days), 1) AS avg_gap_days, COUNT(gap_days) AS gap_count "
            "FROM (SELECT DATEDIFF(progress_date, LAG(progress_date) OVER (ORDER BY version_no)) "
            f"AS gap_days FROM task_progress WHERE {where}) g WHERE gap_days IS NOT NULL",
            {"tid": task_id},
            limit=1,
        )
        if gaps.get("rows"):
            rows["gap_summary"] = gaps["rows"][0]
            rows["caliber"] += (
                "；问「两次报进展平均隔多少天」直接读 gap_summary.avg_gap_days"
                "（服务端 AVG 后 ROUND 到一位小数，首期无上一期不进分母），"
                "自己拿 gap_days 平均会多带小数位（30.29 与口径的 30.3 不一致）"
            )
        # 同名系列（2期/3期/4期）是各自独立的任务，各有自己的进展。按裸名解析
        # 只会落到其中一条，这是对的；但只被告知「这里有 14 期」的调用方无从
        # 知道系列存在，答「这个任务的进展历史」时就容易把整个系列铺开。
        # 把兄弟任务显式回报，让这个取舍看得见。
        siblings = store.name_series(task_id)
        if siblings:
            rows["same_name_series"] = siblings
            rows["caliber"] += (
                f"；本次只含任务 {task_id} 一条的进展，"
                f"同系列另有 {len(siblings)} 条独立任务（"
                + "、".join(f"{s['id']} {s['task_name']}" for s in siblings)
                + "），各有自己的期次，不要合并进本任务的历史；"
                "要另一条就按 id 或完整名（含「（N期）」）再查一次"
            )
        return rows

    return _guard("weekly_progress_history", work)


@mcp.tool()
def weekly_aggregate(
    group_by: str,
    board: str = "",
    metric: str = "count",
    top: int = 0,
    order_by: str = "",
    ascending: bool = False,
) -> str:
    """Aggregate formal tasks. Uses LEFT JOIN so empty groups still appear (R-02/R-08).

    Args:
        group_by: One of board / category / primary_category / status /
            project_group / owner / name_series (同名系列任务家族, 去掉
            trailing "(N期)" suffix before grouping).
        board: Optional board code or name to scope the aggregation.
        metric: Only "count" is supported in the demo.
        top: When > 0, hard-cut to that many groups after the ordering. "任务最多
            的前 5 个分类" means exactly 5 rows even though 9 categories tie at 5
            tasks -- the cut is the answer, not a truncation to apologise for.
        order_by: ``finish_rate`` re-orders the project_group breakdown by
            completion rate instead of task count. "完成率最低的 3 个组" cannot be
            read off a count-ordered list, and the group with the fewest finished
            tasks is not the one with the lowest rate.
        ascending: True with order_by=finish_rate puts the LOWEST rate first.
    """

    def work() -> dict[str, Any]:
        if metric != "count":
            return {
                "ok": False,
                "error": {"code": "unsupported_metric", "message": "演示版仅支持 metric=count"},
            }
        params: dict[str, Any] = {}
        # R-02: the caliber goes on the ON clause so zero-task groups survive.
        scope = store.formal_task_clause()
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {"ok": False, "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"}}
            scope += " AND t.board_id = %(bid)s"
            params["bid"] = board_id

        if group_by == "board":
            sql = (
                "SELECT b.name AS group_name, COUNT(t.id) AS cnt FROM task_board b "
                f"LEFT JOIN task t ON t.board_id = b.id AND {scope} "
                "WHERE b.is_deleted = 0 GROUP BY b.id, b.name ORDER BY b.sort_order"
            )
        elif group_by == "category":
            # board 必须同时落在分类树上（c.board_id），不能只落在任务的 ON 子句里：
            # 只过滤计数时，行清单仍是全部 47 个分类，另一看板的 19 个只是变成
            # cnt=0，和「本看板确实没有任务的分类」长得一模一样。问「技术组下面
            # 有哪些分类」于是答出 47 条（真值 28：7 个一级 + 21 个二级）。
            cat_board = ""
            if board.strip():
                cat_board = "AND c.board_id = %(bid)s"
            sql = (
                "SELECT c.name AS group_name, c.parent_id, COUNT(t.id) AS cnt FROM task_category c "
                f"LEFT JOIN task t ON t.category_id = c.id AND {scope} "
                f"WHERE c.is_deleted = 0 {cat_board} "
                "GROUP BY c.id, c.name, c.parent_id ORDER BY cnt DESC, c.id"
            )
        elif group_by == "primary_category":
            # 任务只挂到二级分类，一级分类得经 parent_id 上跳一层。按 category
            # 分组会返回 47 个二级分类，那是另一个问题的答案。
            # 看板过滤落在分类树所属看板上（c.board_id），与 task.board_id 是两条路径。
            board_filter = ""
            if board.strip():
                board_filter = "AND cb.id = %(bid)s"
            # 完成率与分母同排返回，和 project_group 一档同一个理由：只给 cnt，
            # 模型得自己定小数位、自己对齐分子分母，K5-01/K5-02 两题都是这么算错的
            # （一题把 45.5% 答成 67.5%，一题把两档的高低答反）。率一律服务端 ROUND。
            sql = (
                "SELECT pc.name AS group_name, COUNT(*) AS cnt, "
                "SUM(t.status = 2) AS finished, "
                "ROUND(SUM(t.status = 2) / COUNT(*) * 100, 1) AS finish_rate_pct "
                "FROM task t "
                "JOIN task_category c ON c.id = t.category_id AND c.is_deleted = 0 "
                "JOIN task_board cb ON cb.id = c.board_id AND cb.is_deleted = 0 "
                f"{board_filter} "
                "JOIN task_category pc ON pc.id = c.parent_id AND pc.is_deleted = 0 "
                f"WHERE {store.formal_task_clause()} "
                "GROUP BY pc.id, pc.name ORDER BY cnt DESC, pc.id"
            )
            if (order_by or "").strip().lower() == "finish_rate":
                direction = "ASC" if ascending else "DESC"
                sql = sql.replace(
                    "ORDER BY cnt DESC, pc.id",
                    f"ORDER BY finish_rate_pct {direction}, pc.id",
                )
        elif group_by == "top_sub_per_primary":
            # 「每个一级分类下任务数最多的二级分类」排的是分类而不是任务：
            # weekly_rank 的 per_group 一组给一个任务，答不了这题。分组轴是一级
            # 分类，被排名的单位是它下面的二级分类，度量是各二级分类的任务数。
            # 组内并列按 c.id 裁决（与 gold 的 ROW_NUMBER 同一套定序键），
            # 一组一行，行数即一级分类数。
            sql = (
                "SELECT primary_name AS group_name, sub_name, tasks AS cnt FROM ("
                "SELECT pc.name AS primary_name, c.name AS sub_name, COUNT(t.id) AS tasks, "
                "ROW_NUMBER() OVER (PARTITION BY pc.id ORDER BY COUNT(t.id) DESC, c.id) AS rn "
                "FROM task t "
                "JOIN task_category c ON c.id = t.category_id AND c.is_deleted = 0 "
                "JOIN task_category pc ON pc.id = c.parent_id AND pc.is_deleted = 0 "
                f"WHERE {scope} "
                "GROUP BY pc.id, pc.name, c.id, c.name) r "
                "WHERE r.rn = 1 ORDER BY r.primary_name"
            )
        elif group_by == "status":
            sql = (
                "SELECT CASE t.status WHEN 0 THEN '未开始' WHEN 1 THEN '进行中' "
                "WHEN 2 THEN '已完成' WHEN 3 THEN '已停用' ELSE '未知' END AS group_name, "
                f"COUNT(*) AS cnt FROM task t WHERE {scope} GROUP BY t.status ORDER BY t.status"
            )
        elif group_by == "workflow_status":
            # 唯一不加发布闸门的分组：问的就是审批流转状态分布，把 published 当
            # 前置条件会只剩一档 128，其余六档（未发布的 22 条）全部消失。
            # 与 group_by=status 是两套词汇：这里是审批流转（published /
            # pending_audit / ...），那里是业务进度（未开始 / 进行中 / ...）。
            wf_scope = "t.is_deleted = 0"
            if board.strip():
                wf_scope += " AND t.board_id = %(bid)s"
            sql = (
                "SELECT t.workflow_status AS group_name, COUNT(*) AS cnt FROM task t "
                f"WHERE {wf_scope} GROUP BY t.workflow_status ORDER BY cnt DESC, t.workflow_status"
            )
        elif group_by == "project_group":
            # 组里「几个人牵头」必须由服务端去重，交给模型自己数人名会数错。
            # 完成率与分母同排返回：问「完成率最低的 3 个组」时，只给 cnt 的话
            # 模型得再去别处取每组已完成数，两次闸门不同一就全错；且完成数最少
            # 的组不等于完成率最低的组（治理合规组 2/10=20.0% 高于数据基础设施
            # 组 2/15=13.3%，两组已完成都是 2 条）。
            by_rate = (order_by or "").strip().lower() == "finish_rate"
            # 占比与累计占比必须服务端算：累计要 SUM() OVER (ORDER BY ...) 的窗口，
            # 模型拿分组明细凑不出来，只能自己逐行相加，L5-01/L5-02 就是这么错的。
            # 小数位取 2 而不是 1：累计占比要跟阈值比大小，58.59% 舍成 58.6% 再跟
            # 55% 比就会串档，而 L5-02 问「前几个组合起来过半吗」的正解正是
            # 「前 4 组累计 49.22%，未过半」——一位小数会把结论答反。
            share_cols = (
                "ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 2) AS share_pct, "
                "ROUND(SUM(COUNT(*)) OVER (ORDER BY COUNT(*) DESC, "
                "IFNULL(NULLIF(TRIM(t.project_group),''),'(未填)')) "
                "/ SUM(COUNT(*)) OVER () * 100, 2) AS cum_pct, "
                if not by_rate
                else ""
            )
            sql = (
                "SELECT IFNULL(NULLIF(TRIM(t.project_group),''),'(未填)') AS group_name, "
                "COUNT(*) AS cnt, "
                "SUM(t.status = 2) AS finished, "
                "ROUND(SUM(t.status = 2) / COUNT(*) * 100, 1) AS finish_rate_pct, "
                f"{share_cols}"
                "COUNT(DISTINCT NULLIF(TRIM(t.lead_owner_name),'')) AS lead_owner_count, "
                "COUNT(DISTINCT NULLIF(TRIM(t.project_owner_name),'')) AS project_owner_count "
                f"FROM task t WHERE {scope} GROUP BY group_name ORDER BY cnt DESC, group_name"
            )
            if by_rate:
                # 「完成率最低/最高的几个组」要按率定序，默认的按任务数排给不出。
                # 这一档不返回 cum_pct：累计是沿「任务数倒序」这一条序列累加的，
                # 换成按完成率排之后它在页面上不再单调，留着就是个假信号。
                direction = "ASC" if ascending else "DESC"
                sql = sql.replace(
                    "ORDER BY cnt DESC, group_name",
                    f"ORDER BY finish_rate_pct {direction}, group_name",
                )
        elif group_by == "owner":
            # R-11: 分管领导栏存在多种填法，先按填法枚举再计数，不做归一化猜测。
            sql = (
                "SELECT IFNULL(NULLIF(TRIM(t.lead_owner_name),''),'(未填)') AS group_name, "
                f"COUNT(*) AS cnt FROM task t WHERE {scope} GROUP BY group_name ORDER BY cnt DESC, group_name"
            )
        elif group_by == "name_series":
            # 「同名系列（分期）任务」：任务名去掉尾部的（2期）/（3期）… 后
            # 归并成家族。问「有多少分期项目可能被重复统计」用它：多期家族数、
            # 涉及任务数、占正式任务比例一次给全；家族内按 task id 升序。
            sql = (
                "SELECT REGEXP_REPLACE(t.task_name, '（[0-9]+期）$', '') AS family_name, "
                "COUNT(*) AS cnt, "
                "GROUP_CONCAT(t.id ORDER BY t.id SEPARATOR ',') AS task_ids "
                f"FROM task t WHERE {scope} "
                "GROUP BY family_name ORDER BY cnt DESC, family_name"
            )
        else:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_group_by",
                    "message": "group_by 支持 board / category / primary_category / "
                    "top_sub_per_primary / status / workflow_status / project_group / owner / name_series",
                },
            }
        caliber = f"{store.FORMAL_TASK_CALIBER}；LEFT JOIN 保留空分组（R-02/R-08）"
        if group_by == "workflow_status":
            caliber = (
                "仅 is_deleted = 0，本口径不加发布闸门（问的就是审批流转状态分布，"
                "加了只会剩 published 一档）；"
                "group_name 是审批流转状态（published / pending_audit / pending_leader / "
                "pending_fill / rejected / signing / cancelled），"
                "与 group_by=status 的业务进度状态（未开始 / 进行中 / 已完成 / 已停用）不是一套词汇；"
                "各档相加等于未删除任务总数 total_tasks，"
                "「尚未发布」= total_tasks - published，不要按在途状态逐项相加（cancelled 既非已发布也非在途）；"
                "published_pct 已由服务端算好，直接引用"
            )
        elif group_by == "category":
            caliber += (
                "；本档一行一个二级/一级分类，parent_id 为空即一级分类、非空即挂在该一级下的二级分类，"
                "问「有哪些分类」要按这两级分别报，不要只报一个混合条数；"
                "看板过滤同时作用在分类树（c.board_id）与任务上，"
                "故行清单只含本看板的分类：技术组 28 个（7 个一级 + 21 个二级）、集团组 19 个（5 + 14）；"
                "cnt = 0 表示该分类本看板内确实没有正式任务（R-02 保留空分组），"
                "不是「属于另一个看板」——另一看板的分类根本不在清单里"
            )
        elif group_by == "primary_category":
            caliber = (
                f"{store.FORMAL_TASK_CALIBER}；按一级分类（二级分类的 parent_id）汇总，"
                "不是二级分类；看板过滤落在分类树所属看板上；"
                "只统计挂到分类树上的任务，未挂分类的任务不进任何一档；"
                "finished 是该档已完成（status = 2）条数，finish_rate_pct = finished / cnt，"
                "已由服务端 ROUND 到一位小数，直接引用，不要自己相除或改小数位；"
                "各档 cnt 相加等于挂了分类的正式任务总数"
            )
            if (order_by or "").strip().lower() == "finish_rate":
                caliber += (
                    f"；本次按 finish_rate_pct {'升序' if ascending else '降序'}定序，"
                    f"首行即完成率{'最低' if ascending else '最高'}的一级分类，并列按分类 id 定序；"
                    "「推进最快」问的是完成率而不是任务数，别拿首档任务数当答案"
                )
            else:
                caliber += (
                    "；本次按任务数定序，问「哪类完成率最高/推进最快」请加 order_by=finish_rate，"
                    "任务数最多的档未必完成率最高"
                )
        elif group_by == "top_sub_per_primary":
            caliber = (
                f"{store.FORMAL_TASK_CALIBER}；每个一级分类只返回任务数最多的那一个二级分类"
                "（group_name 是一级分类，sub_name 是胜出的二级分类，cnt 是它的任务数）；"
                "被排名的单位是二级分类而不是任务——问「每个一级分类下哪个二级分类任务最多」用本档，"
                "weekly_rank mode=per_group group_by=primary_category 给的是每个一级分类下的头号任务，是另一题；"
                "组内并列按分类 id 升序裁决，一组一行，行数等于一级分类数，不要把并列的二级分类都列出来"
            )
        elif group_by == "project_group":
            caliber += (
                "；lead_owner_count / project_owner_count 已由服务端按人名去重，直接引用该数字，不要自己数人名"
                "；finished 是该组已完成（status = 2）条数，finish_rate_pct = finished / cnt，"
                "已由服务端算好，不要拿别处取的完成数手工相除；"
                "完成数最少的组不等于完成率最低的组（治理合规组 2/10=20.0% 高于数据基础设施组 2/15=13.3%）"
            )
            if not by_rate:
                caliber += (
                    "；share_pct 是该组占全部正式任务的比例，cum_pct 是沿本次定序"
                    "（任务数倒序、并列按组名）逐行累加的累计占比，两列都由服务端算好并保留两位小数——"
                    "照抄这两列，不要自己逐行相加，也不要改小数位；"
                    "问「前几个组合起来是否过半」按 cum_pct 首次超过 50 的那一行答："
                    "前 4 组累计 49.22% 仍未过半（第 5 组才到 58.59%），"
                    "所以正解是「否，前 4 组占 49.22%」，把 49.22 舍成 49.2 或多算一组都会把结论答反"
                )
            else:
                caliber += "；本档按完成率定序，故不返回 cum_pct（累计占比只在按任务数定序时才单调）"
            if (order_by or "").strip().lower() == "finish_rate":
                caliber += (
                    f"；本次按 finish_rate_pct {'升序' if ascending else '降序'}定序，"
                    f"首行即完成率{'最低' if ascending else '最高'}的组，并列按组名定序"
                )
            else:
                caliber += "；本次按任务数定序，问「完成率最低/最高的几个组」请加 order_by=finish_rate"
        elif group_by == "name_series":
            caliber = (
                f"{store.FORMAL_TASK_CALIBER}；同名系列 = 任务名去掉尾部「（N期）」后缀后"
                "归并成的家族（如「数据资源登记体系建设」与其 2/3/4 期）；"
                "cnt 是该家族任务数，task_ids 是家族内任务 id 列表（升序）；"
                "单期任务也是独立家族，问「重复统计风险」看多期家族"
            )
        cut = 0
        if top:
            try:
                cut = max(1, min(store.MAX_ROWS, int(top)))
            except TypeError, ValueError:
                return {"ok": False, "error": {"code": "invalid_argument", "message": "top 必须是整数"}}
            # 截断落在 SQL 里，并在 caliber 写明并列被切掉了几个：模型看到 5 行
            # 就答 5 行，不会因为「还有并列的」而补列成 9 行。
            tied_total = store.scalar(
                f"SELECT COUNT(*) FROM ({sql}) all_groups",
                params,
            )
            sql += f" LIMIT {cut}"
            caliber += (
                f"；按上述定序硬切前 {cut} 组（共 {tied_total['value']} 组）；"
                "边界外与末位并列的分组不属于本题答案，不要补列"
            )
        result = store.fetch(sql, params, caliber=caliber, limit=cut or store.MAX_ROWS)
        result["group_by"] = group_by
        if group_by == "name_series":
            # 「重复统计风险」的答案 = 多期家族数 + 涉及任务数 + 占比，服务端一次算完：
            # 让模型自己数 rows 里的 cnt > 1 会漏（家族多、截断或只看前几行）。
            rows_list = result.get("rows") or []
            multi = [r for r in rows_list if int(r.get("cnt") or 0) > 1]
            result["multi_member_families"] = len(multi)
            result["tasks_in_families"] = sum(int(r.get("cnt") or 0) for r in multi)
            result["families_total"] = len(rows_list)
            result["caliber"] += (
                "；问「有多少分期项目可能被重复统计」报 multi_member_families / "
                "tasks_in_families / 占比（= tasks_in_families / 正式任务总数），"
                "不要拿全部家族数 families_total 当答案——单期家族不算重复统计风险"
            )
        if cut:
            result["total_groups"] = tied_total["value"]
        if group_by == "workflow_status":
            # 「未发布几条」「已发布占比」和分档是同一题的三面，一次给全：分开问
            # 会让模型拿在途各档相加去凑未发布，而 cancelled 两边都不属于。
            wf_where = "t.is_deleted = 0" + (" AND t.board_id = %(bid)s" if board.strip() else "")
            # 「还有多少流程要继续推动」不等于「未发布多少条」：退回和已取消都不在
            # 推动之列（退回已回到填报方手上、已取消不再推进），把它们算进去就成了
            # 22 或 21。活跃待办只数 pending_audit/pending_leader/pending_fill/
            # signing 四档 = 18。这个加法必须在服务端做：让模型拿分档自己相加，它
            # 多半会把 rejected 一起加上（基线正是这么答出 21 的）。
            active_codes = "('pending_audit', 'pending_leader', 'pending_fill', 'signing')"
            totals = store.fetch(
                "SELECT COUNT(*) AS total_tasks, "
                "SUM(t.workflow_status = 'published') AS published_tasks, "
                "SUM(t.workflow_status <> 'published') AS unpublished_tasks, "
                "ROUND(SUM(t.workflow_status = 'published') / COUNT(*) * 100, 1) AS published_pct, "
                f"SUM(t.workflow_status IN {active_codes}) AS active_pending_tasks, "
                "SUM(t.workflow_status = 'rejected') AS rejected_tasks, "
                "SUM(t.workflow_status = 'cancelled') AS cancelled_tasks "
                f"FROM task t WHERE {wf_where}",
                params,
                limit=1,
            )
            result["totals"] = totals["rows"][0] if totals["rows"] else {}
            # 分档只给条数，「等领导审批的是哪几个任务」就没有任何工具答得上：
            # weekly_task_query 硬挂 R-01 正式任务闸门（is_deleted = 0 AND
            # published），未发布的 22 条它一条也不返回，而这里恰恰只回了个数。
            # 结果模型退回填报表按「审批中的填报单」作答，答成 15 条和 9 条
            # （那是填报单不是任务）。把这 22 条的名字随分档一起给出，问数和
            # 问名字落在同一次调用里。
            #
            # 这不是放宽闸门：本清单固定 workflow_status <> 'published'，与正式
            # 任务集互不相交，weekly_task_query 那侧一个字没改。
            pending = store.fetch(
                "SELECT t.id, t.task_no, t.task_name, t.board_id, t.workflow_status "
                f"FROM task t WHERE {wf_where} AND t.workflow_status <> 'published' "
                "ORDER BY FIELD(t.workflow_status, 'pending_audit', 'pending_leader', "
                "'pending_fill', 'signing', 'rejected', 'cancelled'), t.board_id, t.id",
                params,
                limit=store.MAX_ROWS,
            )
            result["unpublished_task_list"] = pending["rows"]
            result["caliber"] += (
                "；totals.active_pending_tasks 是「仍需继续推动」的活跃待办，只含"
                "待审核 pending_audit、待领导审批 pending_leader、待填报 pending_fill、"
                "会签 signing 四档，退回 rejected 与已取消 cancelled 不计入"
                "（退回已回到填报方、已取消不再推进）；"
                "问「有多少流程需要继续推动／审批积压怎样」直接引用这个数，"
                "不要拿分档自己相加，也不要用 unpublished_tasks 顶替——那个数含退回和已取消"
                "；unpublished_task_list 是这些未发布任务的清单（id / task_no / task_name / "
                "workflow_status），问「哪些任务在等领导审批／哪些在会签」按 workflow_status "
                "在这份清单里筛，它已是全量不会截断；这些任务不在正式任务集内，"
                "weekly_task_query 按 R-01 一条也不会返回，别把填报表里「审批中的填报单」"
                "当成任务清单作答——填报单与任务不是一回事，条数也不同"
            )
        if group_by == "status":
            # 「完成率是多少」与分档是同一题的两面。分档只给条数,模型要自己
            # 31 / 128 相除,结果是 24.21875,报出来成了 24.22%,而口径是保留
            # 一位小数的 24.2%。率一律由服务端 ROUND 算好(里程碑与 project_group
            # 两路早就这么做),这里补齐,不让模型手算再自己定小数位。
            st_where = "t.is_deleted = 0 AND t.workflow_status = 'published'" + (
                " AND t.board_id = %(bid)s" if board.strip() else ""
            )
            totals = store.fetch(
                "SELECT COUNT(*) AS total_tasks, SUM(t.status = 2) AS finished_tasks, "
                "ROUND(SUM(t.status = 2) / COUNT(*) * 100, 1) AS finish_rate_pct "
                f"FROM task t WHERE {st_where}",
                params,
                limit=1,
            )
            result["totals"] = totals["rows"][0] if totals["rows"] else {}
            caliber += (
                "；完成率已由服务端算好放在 totals.finish_rate_pct（已完成 status = 2 占正式任务的比例，"
                "保留一位小数），直接引用该数字——自己拿分档条数相除会多带小数位，"
                "31 / 128 手算成 24.22% 而口径是 24.2%"
            )
            result["caliber"] = caliber
        return result

    return _guard("weekly_aggregate", work)


@mcp.tool()
def weekly_scale(by: str = "board", mode: str = "totals", year: int = 2026) -> str:
    """Cross-section formal tasks over several child tables at once, de-duplicated.

    Answers "what is the scale of each board / group" and "how complete is the
    data" in one row per group. Every child count is COUNT(DISTINCT ...): joining
    three child tables at once multiplies the rows, so a plain COUNT reports
    milestones x attachments rather than either one.

    Args:
        by: Grouping axis: board / project_group / primary_category.
        mode: ``totals`` tasks plus milestone / attachment / goal counts.
            ``completeness`` how many tasks HAVE a goal / milestone / progress
            (task counts, not child-row counts -- a different question).
            ``intensity`` published progress rows and rows per task.
        year: Which year the annual-goal column looks at. Only used by
            ``totals`` and ``completeness``.
    """

    def work() -> dict[str, Any]:
        axis_key = (by or "board").strip().lower()
        chosen = _SCALE_AXES.get(axis_key)
        if chosen is None:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_by",
                    "message": f"不支持的分组轴：{by}；支持 {', '.join(sorted(_SCALE_AXES))}",
                },
            }
        mode_key = (mode or "totals").strip().lower()
        if mode_key not in _SCALE_MODES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_mode",
                    "message": f"不支持的口径：{mode}；支持 {', '.join(_SCALE_MODES)}",
                },
            }
        axis, order, extra = chosen
        clause = store.formal_task_clause()
        params: dict[str, Any] = {"yr": int(year)}
        base = store.FORMAL_TASK_CALIBER

        if mode_key == "intensity":
            # 分母是任务数而不是进展行数。LEFT JOIN 让零期任务留在分母里，
            # 否则「人均期数」会被抬高（inner_join_drops_zero）。
            return store.fetch(
                f"SELECT {axis} AS bucket, COUNT(DISTINCT t.id) AS tasks, COUNT(p.id) AS progress_rows, "
                "ROUND(COUNT(p.id) / COUNT(DISTINCT t.id), 2) AS rows_per_task "
                f"FROM task t {extra} "
                "LEFT JOIN task_progress p ON p.task_id = t.id AND p.is_published = 1 "
                f"WHERE {clause} GROUP BY {axis} ORDER BY rows_per_task DESC, bucket",
                caliber=(
                    f"{base} 且进展行 p.is_published = 1（两道闸门）；"
                    "rows_per_task = 已发布进展行数 / 任务数，分母含零期任务（LEFT JOIN 保留）；"
                    "该均值由服务端算出，不要拿返回行自己除"
                ),
                limit=store.MAX_ROWS,
            )

        if mode_key == "completeness":
            # 「有目标/有里程碑/有进展的各占多少」问的是任务数，不是子表行数。
            # 用 SUM(EXISTS ...) 而不是 COUNT(DISTINCT 子表键)：后者在子表全空时
            # 也能给 0，但语义要靠 JOIN 保住，多张子表一起 JOIN 就又放大了。
            return store.fetch(
                f"SELECT {axis} AS bucket, COUNT(*) AS tasks, "
                "SUM(EXISTS (SELECT 1 FROM task_year_goal g "
                "WHERE g.task_id = t.id AND g.year = %(yr)s)) AS has_goal, "
                "SUM(EXISTS (SELECT 1 FROM task_milestone m "
                "WHERE m.task_id = t.id AND m.is_deleted = 0)) AS has_milestone, "
                "SUM(EXISTS (SELECT 1 FROM task_progress p "
                "WHERE p.task_id = t.id AND p.is_published = 1)) AS has_progress "
                f"FROM task t {extra} WHERE {clause} GROUP BY {axis} ORDER BY {order}",
                params,
                caliber=(
                    f"{base}；has_* 是「有该项的任务数」，不是子表条数（子表条数问 mode=totals）；"
                    f"年度目标按 {int(year)} 年；分母 tasks 为该组全部正式任务"
                ),
                limit=store.MAX_ROWS,
            )

        # totals：三张子表一起 JOIN，每个计数都必须 DISTINCT。
        # 不 DISTINCT 时 milestones 会被 attachments 的行数乘一遍
        # （技术组 294 会算成 1363，fan_out_double_count）。
        return store.fetch(
            f"SELECT {axis} AS bucket, COUNT(DISTINCT t.id) AS tasks, "
            "COUNT(DISTINCT g.task_id) AS with_year_goal, "
            "COUNT(DISTINCT m.id) AS milestones, COUNT(DISTINCT a.id) AS attachments "
            f"FROM task t {extra} "
            "LEFT JOIN task_year_goal g ON g.task_id = t.id AND g.year = %(yr)s "
            "LEFT JOIN task_milestone m ON m.task_id = t.id AND m.is_deleted = 0 "
            "LEFT JOIN task_attachment a ON a.task_id = t.id AND a.is_deleted = 0 "
            f"WHERE {clause} GROUP BY {axis} ORDER BY {order}",
            params,
            caliber=(
                f"{base}；里程碑与附件各自再加自己的 is_deleted = 0；"
                "四个维度一次 JOIN，故每个计数都按主键去重（COUNT(DISTINCT ...)），"
                "各组 milestones 相加等于全库里程碑总数，若比总数大即是被 JOIN 放大了；"
                f"with_year_goal 是「设了 {int(year)} 年度目标的任务数」"
            ),
            limit=store.MAX_ROWS,
        )

    return _guard("weekly_scale", work)


@mcp.tool()
def weekly_milestone_query(task: str = "", year: str = "", status: str = "", limit: int = 200) -> str:
    """List milestones, joined back to task to re-check the formal-task caliber (R-17).

    Args:
        task: Task id or name. Empty covers every formal task -- so "任务 19 有哪些
            里程碑" must pass it, otherwise the answer is the first page of the
            whole board and the row set silently belongs to a different question.
        year: Four-digit year, empty for all.
        status: 0 未完成 / 1 已完成, empty for all.
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        where = ["m.is_deleted = 0", store.formal_task_clause()]
        params: dict[str, Any] = {}
        scoped = False
        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            where.append("m.task_id = %(tid)s")
            params["tid"] = task_id
            scoped = True
        if year.strip():
            if not year.strip().isdigit():
                return {"ok": False, "error": {"code": "invalid_year", "message": "year 须为数字"}}
            where.append("m.year = %(yr)s")
            params["yr"] = int(year.strip())
        if status.strip():
            if status.strip() not in {"0", "1"}:
                return {"ok": False, "error": {"code": "invalid_status", "message": "status 只能是 0/1"}}
            where.append("m.status = %(st)s")
            params["st"] = int(status.strip())
        clause = " AND ".join(where)
        caliber = f"m.is_deleted = 0 且关联任务满足 {store.FORMAL_TASK_CALIBER}（R-17）"
        # 单任务问「里程碑安排」要按任务内的编排顺序（sort_order）读，那是这份
        # 清单的业务次序；全局浏览才按年度倒序。次序不同会被判成另一个集合。
        order = "m.year, m.sort_order, m.id" if scoped else "m.year DESC, m.id"
        if scoped:
            caliber += "；按 sort_order 给出该任务内的编排顺序；已按 total_count 给全，勿另行截断"
        total = store.scalar(
            f"SELECT COUNT(*) FROM task_milestone m JOIN task t ON t.id = m.task_id WHERE {clause}",
            params,
        )
        rows = store.fetch(
            "SELECT m.id, m.task_id, t.task_name, m.year, m.category, m.group_name, "
            "m.content, m.status, m.sort_order "
            "FROM task_milestone m JOIN task t ON t.id = m.task_id "
            f"WHERE {clause} ORDER BY {order}",
            params,
            caliber=caliber,
            limit=limit,
        )
        rows["total_count"] = total["value"]
        if scoped:
            # 与进展历史同一个病根：同名系列是各自独立的任务，各有自己的里程碑。
            # 「数据资源登记体系建设」按裸名解析到任务 3 的 5 条是对的，但只被告知
            # 「这里有 5 条」的调用方无从知道 41/60/79 也各有一份，答「里程碑安排」
            # 就容易把 4 条任务的 15 条铺成一张表。把兄弟任务显式回报。
            siblings = store.name_series(params["tid"])
            if siblings:
                rows["same_name_series"] = siblings
                rows["caliber"] += (
                    f"；本次只含任务 {params['tid']} 一条的里程碑，"
                    f"同系列另有 {len(siblings)} 条独立任务（"
                    + "、".join(f"{s['id']} {s['task_name']}" for s in siblings)
                    + "），各有自己的里程碑，不要合并进本任务的安排；"
                    "要另一条就按 id 或完整名（含「（N期）」）再查一次"
                )
        return rows

    return _guard("weekly_milestone_query", work)


@mcp.tool()
def weekly_workflow_query(
    task: str = "",
    action: str = "",
    board: str = "",
    by_task: bool = False,
    scope: str = "",
    limit: int = 200,
    ctx: Context | None = None,
) -> str:
    """Trace approval submissions and actions. Opinions are permission-gated (R-04/R-14).

    Args:
        task: Task id or name; empty returns the most recent actions overall.
        action: Keep only this action (e.g. ``rejected``). Call with an unknown
            value to see the domain -- an out-of-domain word would otherwise
            filter nothing and the full log would pass as a filtered set.
        board: Board code or name to scope by. "集团看板哪些任务被驳回过" needs it.
        by_task: True aggregates per task into action_count instead of listing rows.
        scope: ``by_node_action`` counts every node_type + action pair in one row
            each; ``actions_per_task`` returns the average action count with its
            numerator and denominator.  Either beats counting a truncated listing:
            the log holds 1578 rows and the listing stops at 200.  ``recent``
            orders by the action's own timestamp and carries the task name and
            the submitter -- any "最近谁被驳回了" question needs it, because the
            default listing is ordered by task id and answers no such question.
        limit: Max rows, capped at 200.
    """
    may_read = _caller_may_read_sensitive(ctx)

    def work() -> dict[str, Any]:
        bounded_flow = max(1, min(store.MAX_ROWS, int(limit)))
        scope_key = (scope or "").strip().lower()
        if scope_key not in _WORKFLOW_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(s or '(空)' for s in _WORKFLOW_SCOPES)}",
                },
            }
        if scope_key in ("by_node", "by_operator", "log_span", "opinion_count"):
            # 这四档是「审批表本身有多大」，故不加任务闸门。动作挂在 task_id 外键上，
            # 但问「审批一共走过哪些环节、各几次」问的是日志规模，跟任务还在不在办
            # 没关系——与里程碑 deleted 那一档同一个道理（问的是表本身）。
            # 三档口径实测差得不少，所以每次都把三个总数一起回出，让口径可核对：
            # 裸表 1613 / 加软删 1578 / 加双闸门 1519（提交单是 470 / 462 / 438）。
            # 问「某个任务的审批走到哪了」才要闸门档，见 by_node_action 与 recent。
            gated = store.scalar(
                "SELECT COUNT(*) FROM task_workflow_action a JOIN task t ON t.id = a.task_id "
                f"WHERE {store.formal_task_clause()}"
            )
            soft = store.scalar(
                "SELECT COUNT(*) FROM task_workflow_action a JOIN task t ON t.id = a.task_id WHERE t.is_deleted = 0"
            )
            raw_total = store.scalar("SELECT COUNT(*) FROM task_workflow_action")
            tiers = {
                "raw_table": raw_total["value"],
                "soft_deleted_gate": soft["value"],
                "formal_task_gate": gated["value"],
            }
            tier_note = (
                f"；本档为裸表口径（不加任务闸门），本档的答案就是 {tiers['raw_table']}——"
                "回答时以它为主结论，不要改用下面两个对照数；"
                f"另两档仅供口径对照：加软删闸门 {tiers['soft_deleted_gate']}、"
                f"加正式任务双闸门 {tiers['formal_task_gate']}（都在 caliber_tiers 里）；"
                "问「审批表有多少条 / 走过哪些环节」用本档，"
                "问「某个任务或某看板的审批」请改用 scope=by_node_action 或 recent（那两档带闸门）"
            )
            if scope_key == "by_node":
                out = store.fetch(
                    "SELECT a.node_type, COUNT(*) AS cnt FROM task_workflow_action a "
                    "GROUP BY a.node_type ORDER BY cnt DESC, a.node_type",
                    caliber=(
                        "按审批节点 node_type 分档，一节点一行；各档相加等于动作总数"
                        + tier_note
                        + "；与 by_node_action 不是一档：那一档按 node_type + action 两维分，"
                        "同一个 approved 在 audit / leader / sign 各计一次，答「哪个节点驳回得多」"
                    ),
                    limit=bounded_flow,
                )
            elif scope_key == "by_operator":
                # 「审批环节都是谁在操作、各几次」此前没有出口：by_node_action 只按节点分，
                # 模型只能答「无法给出每人操作次数」。经办人计数必须服务端聚合——
                # 57 人而清单封顶 200 行，翻明细数人次必然错。
                out = store.fetch(
                    "SELECT a.operator_name, COUNT(*) AS cnt FROM task_workflow_action a "
                    "WHERE a.operator_name IS NOT NULL AND a.operator_name <> '' "
                    "GROUP BY a.operator_name ORDER BY cnt DESC, a.operator_name",
                    caliber=(
                        "按经办人 operator_name 分组计动作次数，一人一行，行数即经办人数；"
                        "问「谁经办得最多」取首行（榜首 孙立群 244 次，与第二名 93 差距很大，"
                        "无并列），问「各操作了几次」按 total_count 逐条列全" + tier_note
                    ),
                    limit=bounded_flow,
                )
                out["total_count"] = store.scalar(
                    "SELECT COUNT(DISTINCT a.operator_name) FROM task_workflow_action a "
                    "WHERE a.operator_name IS NOT NULL AND a.operator_name <> ''"
                )["value"]
            elif scope_key == "log_span":
                out = store.fetch(
                    "SELECT MIN(a.created_at) AS first_at, MAX(a.created_at) AS last_at, "
                    "COUNT(*) AS actions FROM task_workflow_action a",
                    caliber="审批动作日志的时间跨度与总条数，一次给全" + tier_note,
                    limit=1,
                )
            else:
                # R-04/R-14：意见正文按权限遮蔽，但「留了几条意见」只是存在性计数，
                # 不外泄任何正文，所以这一档不受权限影响。
                out = store.fetch(
                    "SELECT COUNT(*) AS cnt FROM task_workflow_action a "
                    "WHERE a.opinion IS NOT NULL AND a.opinion <> ''",
                    caliber=(
                        "统计 opinion 非空的动作条数（只数存在性，不返回意见正文，"
                        "故不受 R-04/R-14 遮蔽影响）；意见非空 1455 条少于动作总数，"
                        "差额是没写意见的那些动作，不是漏数" + tier_note
                    ),
                    limit=1,
                )
            out["caliber_tiers"] = tiers
            return out
        if scope_key == "by_node_action":
            # node_type 与 action 是两列，必须两维一起分档：同一个 approved
            # 在 audit / leader / sign 三个节点各有一份，只按 action 分会把
            # 955 条 approved 揉成一档，答不了「哪个节点驳回得多」。
            return store.fetch(
                "SELECT a.node_type, a.action, COUNT(*) AS action_count "
                "FROM task_workflow_action a JOIN task t ON t.id = a.task_id "
                "WHERE t.is_deleted = 0 GROUP BY a.node_type, a.action "
                "ORDER BY action_count DESC, a.node_type, a.action",
                caliber=(
                    "仅 t.is_deleted = 0（动作日志跨发布状态，不加发布闸门）；"
                    "按 node_type + action 两维分档，同一个 action 在不同节点分别计数，"
                    "各档相加等于动作总数 1578；条数由服务端聚合，"
                    "不要翻明细自己数——日志 1578 条而清单封顶 200 行"
                ),
                limit=bounded_flow,
            )
        if scope_key == "actions_per_task":
            return store.fetch(
                "SELECT ROUND(COUNT(*) / COUNT(DISTINCT a.task_id), 2) AS avg_actions, "
                "COUNT(*) AS total_actions, COUNT(DISTINCT a.task_id) AS tasks "
                "FROM task_workflow_action a JOIN task t ON t.id = a.task_id "
                "WHERE t.is_deleted = 0",
                caliber=(
                    "仅 t.is_deleted = 0；分母是有动作记录的任务数（COUNT DISTINCT task_id = 150），"
                    "不是已发布任务数 128；1578 / 150 = 10.52，分子分母一并给出以便核对"
                ),
                limit=1,
            )
        params: dict[str, Any] = {}
        where = ["1 = 1"]
        caliber_extra: list[str] = []
        board_join = ""
        if action.strip():
            domain = {
                str(row["action"]).strip()
                for row in store.fetch(
                    "SELECT DISTINCT action FROM task_workflow_action ORDER BY action",
                    limit=50,
                )["rows"]
                if row["action"] is not None
            }
            token = action.strip()
            if token not in domain:
                return {
                    "ok": False,
                    "error": {
                        "code": "unsupported_action",
                        "message": f"action 不在值域内：{token}；支持 {', '.join(sorted(domain))}",
                    },
                }
            where.append("a.action = %(act)s")
            params["act"] = token
            caliber_extra.append(f"仅 action = {token}")
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {"ok": False, "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"}}
            board_join = "JOIN task t ON t.id = a.task_id"
            where.append("t.is_deleted = 0 AND t.board_id = %(bid)s")
            params["bid"] = board_id
            caliber_extra.append("按看板过滤时任务侧只加 is_deleted = 0（动作日志本就跨发布状态）")
        if scope_key == "recent":
            # 「最近有哪些提交单被驳回了」问的是时间序。默认流水按 task_id, round_no
            # 排，最近发生的那条埋在中间，模型只能把 13 条全铺开当答案，答不出「最近」。
            # 顺带把任务名与填报人一并 JOIN 回来：只给 task_id 与操作人，
            # 「谁的单子被谁驳回」还得再查两次。
            if not board.strip():
                where.append("t.is_deleted = 0")
            if task.strip():
                task_id = store.resolve_task_id(task)
                if task_id is None:
                    return _task_miss(task)
                where.append("a.task_id = %(tid)s")
                params["tid"] = task_id
            return store.fetch(
                "SELECT a.id, a.task_id, t.task_name, s.round_no, s.reporter_name, s.status, "
                "a.node_type, a.action, a.operator_name, a.opinion, a.created_at AS acted_at "
                "FROM task_workflow_action a JOIN task t ON t.id = a.task_id "
                "JOIN task_workflow_submission s ON s.id = a.submission_id "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY a.created_at DESC, t.id",
                params,
                caliber="；".join(
                    [
                        *caliber_extra,
                        "按动作发生时间 acted_at 倒序（并列按任务 id 升序），"
                        "第一行即最近一次；「最近」看的是动作时间，不是任务 id 也不是轮次号",
                        "只含挂在提交单上的动作（INNER JOIN 提交单），"
                        "带任务名与该单填报人，无须再查任务表；"
                        "status 是提交单当前状态，与本行 action 未必同一时刻",
                        "opinion 属敏感字段，按权限展示（R-04/R-14）"
                        + (
                            "；本次凭证有敏感字段权限，opinion 原文返回"
                            if may_read
                            else "；本次凭证无敏感字段权限，opinion 已遮蔽"
                        ),
                    ]
                ),
                can_read_sensitive=may_read,
                limit=bounded_flow,
            )
        if by_task:
            # 「哪些任务被驳回过」问的是任务集合与次数，不是动作流水。逐条明细
            # 里同一任务会出现多次，模型按行数报会把次数当成任务数。
            joins = board_join or "JOIN task t ON t.id = a.task_id"
            if not board.strip():
                where.append("t.is_deleted = 0")
            return store.fetch(
                "SELECT a.task_id, t.task_name, COUNT(*) AS action_count "
                f"FROM task_workflow_action a {joins} "
                f"WHERE {' AND '.join(where)} "
                "GROUP BY a.task_id, t.task_name ORDER BY action_count DESC, a.task_id",
                params,
                caliber="；".join([*caliber_extra, "按任务聚合，action_count 是次数不是任务数；并列按 task id 升序"]),
                limit=limit,
            )
        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            where.append("a.task_id = %(tid)s")
            params["tid"] = task_id
        return store.fetch(
            "SELECT a.id, a.submission_id, a.task_id, s.round_no, a.node_type, a.action, "
            "a.operator_name, a.opinion, a.created_at "
            f"FROM task_workflow_action a {board_join} "
            "LEFT JOIN task_workflow_submission s ON s.id = a.submission_id "
            f"WHERE {' AND '.join(where)} "
            # 审批轨迹按 created_at 排，不按 round_no。轮次号不等于时间序:任务 3
            # 的第 3 轮(2025-09-19)实际早于第 2 轮(2025-10-13),按 round_no 排会
            # 把两轮对调,读出来的轨迹是错的。
            "ORDER BY a.task_id, a.created_at, a.id",
            params,
            caliber="；".join(
                [
                    *caliber_extra,
                    "轨迹按 created_at 时间升序，不按 round_no——轮次号与实际时间不一致，"
                    "存在第 3 轮早于第 2 轮的任务，按轮次读会把顺序读反",
                    "opinion 属敏感字段，按权限展示（R-04/R-14）"
                    + (
                        "；本次凭证有敏感字段权限，opinion 原文返回"
                        if may_read
                        else "；本次凭证无敏感字段权限，opinion 已遮蔽"
                    ),
                    "payload 草稿快照不返回",
                ]
            ),
            can_read_sensitive=may_read,
            limit=limit,
        )

    return _guard("weekly_workflow_query", work)


@mcp.tool()
def weekly_attachment_query(task: str = "", board: str = "", limit: int = 200) -> str:
    """List attachments without ever returning storage_path (chapter 7.2).

    Args:
        task: Task id or name; empty lists across all formal tasks.
        board: Board code or name (e.g. group / tech) to keep only that board's
            attachments. Without it, asking "what files does the group board hold"
            forces a task-by-task loop over 46 tasks.
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        params: dict[str, Any] = {}
        where = ["att.is_deleted = 0"]
        joins = ""
        caliber = [
            "is_deleted = 0；storage_path 禁止外泄，不在返回字段内",
            "file_size 单位是字节，原样报出，不要换算成 KB/MB 也不要写「约」",
        ]
        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            where.append("att.task_id = %(tid)s")
            params["tid"] = task_id
        if board.strip():
            # 看板在 task 上，附件表里没有 board_id，所以按看板筛必须 JOIN 回
            # task 并顺带带上任务闸门与任务名——没有任务名的清单答不了「哪些任务」。
            board_id = store.resolve_board(board)
            if board_id is None:
                return {
                    "ok": False,
                    "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"},
                }
            joins = " JOIN task t ON t.id = att.task_id"
            where.append(store.formal_task_clause())
            where.append("t.board_id = %(bid)s")
            params["bid"] = board_id
            caliber.append(f"仅看板 {board.strip()}（看板在 task 上，已 JOIN 回任务并附加正式任务闸门）")
        columns = (
            "att.id, att.task_id, att.progress_id, att.workflow_submission_id, "
            "att.file_name, att.file_size, att.uploader_id, att.upload_time"
        )
        if board.strip():
            columns += ", t.task_name"
        return store.fetch(
            f"SELECT {columns} FROM task_attachment att{joins} "
            f"WHERE {' AND '.join(where)} ORDER BY att.task_id, att.id",
            params,
            caliber="；".join(caliber),
            limit=limit,
        )

    return _guard("weekly_attachment_query", work)


# 附件的聚合口径。deleted / orphan 两档故意不加任务闸门：问的是表本身。
_ATTACHMENT_STATS_SCOPES = (
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
# 这几档的问题对象不是「某个任务」：zero_attachment 问的是跨任务的存在性（分母是
# 全部正式任务），deleted / deleted_by_link / orphan 是对整张附件表的审计。传了
# task 就报错而不是悄悄忽略——静默忽略会让人以为拿到的是单任务数，那比报错更糟。
_ATTACHMENT_STATS_WHOLE_TABLE = frozenset({"zero_attachment", "deleted", "deleted_by_link", "orphan"})


@mcp.tool()
def weekly_attachment_stats(
    scope: str = "summary",
    date_from: str = "",
    task: str = "",
    top: int = 200,
    include_informal: bool = False,
) -> str:
    """Aggregate attachments: size totals, file types, uploaders, soft-delete audit.

    weekly_attachment_query lists rows and caps at 200, so counting or summing by
    reading rows back understates every total -- there are 454 live attachments on
    formal tasks.  Sizes are returned in bytes AND in MB: the byte figure is the
    authoritative one.

    Args:
        scope: summary (count, total bytes/MB, average) / by_ext (per file
            extension) / largest (biggest files first) / by_uploader (per uploader,
            with size) / uploader_count (distinct uploaders) / by_link (attached to
            progress vs submission vs the task itself) / by_progress (per published
            progress round, attachment-heavy first) / on_open_submission (count on
            submissions that are not yet published) / by_month (uploads per month) /
            deleted (soft-delete audit, whole table) / deleted_by_link (deleted rows
            by attach point) / orphan (rows whose task_id has no task).
        date_from: For by_month, inclusive lower bound YYYY-MM-DD.
        task: Task id or name to scope the aggregate to one task. "How big are
            task 2's attachments" otherwise has no aggregate at all -- the only
            route is listing rows and summing them by hand, which is both wrong
            past 200 rows and (on O3-03) burned the whole round budget. Scoping
            by task drops the formal-task gate on purpose: attachments hang off
            task_id as a plain foreign key, so a task outside the formal set
            still has attachments, and gating here would silently answer 0.
        top: Row cap for the listing scopes.
        include_informal: True drops the formal-task gate and counts the whole
            attachment table (510 live rows) instead of only attachments hanging
            off formal tasks (454). Both readings are legitimate -- "how many
            attachments are there in total" means the table, "how many do the
            formal tasks have" means the gated set -- so the default stays gated
            and this opens the other door explicitly. Ignored when task is set,
            which is already ungated on purpose.
    """

    def work() -> dict[str, Any]:
        key = (scope or "summary").strip().lower()
        if key not in _ATTACHMENT_STATS_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(_ATTACHMENT_STATS_SCOPES)}",
                },
            }
        bounded = max(1, min(store.MAX_ROWS, int(top)))
        scoped_task: int | None = None
        if task.strip():
            if key in _ATTACHMENT_STATS_WHOLE_TABLE:
                return {
                    "ok": False,
                    "error": {
                        "code": "task_not_applicable",
                        "message": (
                            f"口径 {key} 是跨任务/全表口径，传 task 无意义，不做静默忽略："
                            "zero_attachment 问的是「哪些任务一个附件都没有」（分母是全部正式任务），"
                            "deleted / deleted_by_link / orphan 是对整张附件表的软删与孤儿审计。"
                            "要单任务的附件总量请用 scope=summary 加 task"
                        ),
                    },
                }
            scoped_task = store.resolve_task_id(task)
            # resolve_task_id 对纯数字直接放行（外键取数本该如此），所以「库里没这个
            # id」要单独判一次：不判就会回一行 count = 0，和「这任务确实没附件」
            # 长得一样，正是 O3-03 那类问题的来源。
            if scoped_task is None or store.task_miss_reason(str(scoped_task)).get("kind") == "absent":
                return _task_miss(task)
        # 活跃附件的口径：任务侧 R-01 + 附件行自身的软删标记，两道都要。
        # 单任务档例外：附件按 task_id 外键挂，任务不在正式集里也照样有附件，
        # 此处加闸门只会把 2 条静默答成 0（任务 2 正是 workflow_status='rejected'）。
        if scoped_task is not None:
            gate = f"a.task_id = {int(scoped_task)}"
            live_caliber = (
                f"仅任务 {scoped_task}，按 task_id 外键取数；a.is_deleted = 0（附件行软删）；"
                "本档不加正式任务闸门——附件挂在外键上，任务未过 R-01 时它的附件依然存在，"
                "加闸门会把真实条数静默答成 0"
            )
        elif include_informal:
            # 「附件表里一共多少个」问的是整张表，不是正式任务的那一部分。两个数
            # 都是合法读法，差额是任务闸门吃掉的 56 行（510 全表 / 454 正式），
            # 所以不翻默认值，只在明确要全表时开这个口，并把两个数一起给出。
            gate = "1 = 1"
            live_caliber = (
                "全表口径：只按 a.is_deleted = 0（附件行软删），不加正式任务闸门；"
                "本档 510 个活跃附件，加闸门（任务未删除且 workflow_status = 'published'）是 454 个，"
                "差额 56 个挂在非正式任务上；"
                "问「附件表里一共」用本档，问「正式任务有多少附件」用 include_informal = False"
            )
        else:
            gate = store.formal_task_clause()
            live_caliber = (
                f"{store.FORMAL_TASK_CALIBER} 且 a.is_deleted = 0（任务闸门 + 附件行软删两道）；"
                "本档 454 个；整张附件表的活跃行是 510 个，要那个数请传 include_informal = True"
            )
        # 全表口径必须连 JOIN 一起放开：光把闸门改成 1 = 1 还差 3 行，因为
        # INNER JOIN 自己就会丢掉孤儿附件（task_id 匹配不到任何任务）。
        # 507 + 3 孤儿 = 510，字节 2163753344 + 9162752 = 2172916096。
        # 改 LEFT JOIN 而不是去掉 JOIN，是为了让别的分档继续能用 t 别名。
        join_kind = "LEFT JOIN" if (include_informal and scoped_task is None) else "JOIN"
        live = f"FROM task_attachment a {join_kind} task t ON t.id = a.task_id WHERE {gate} AND a.is_deleted = 0"
        size_note = "file_size 单位是字节，bytes 列为权威值，MB 列由服务端换算仅供参考"
        # 关联去向的分档表达式，三处口径必须一致，抽出来共用。
        link_case = (
            "CASE WHEN a.progress_id IS NOT NULL THEN '挂在进展' "
            "WHEN a.workflow_submission_id IS NOT NULL THEN '挂在提交单' "
            "ELSE '挂在任务本体' END"
        )

        if key == "summary":
            return store.fetch(
                "SELECT COUNT(*) AS attachment_count, SUM(a.file_size) AS total_bytes, "
                "ROUND(SUM(a.file_size) / 1024 / 1024, 1) AS total_mb, "
                "ROUND(AVG(a.file_size) / 1024, 1) AS avg_kb, "
                f"COUNT(DISTINCT a.task_id) AS tasks_with_attachment {live}",
                caliber=f"{live_caliber}；{size_note}",
                limit=1,
            )

        if key == "by_ext":
            return store.fetch(
                "SELECT LOWER(SUBSTRING_INDEX(a.file_name, '.', -1)) AS ext, COUNT(*) AS n, "
                "SUM(a.file_size) AS total_bytes, "
                f"ROUND(SUM(a.file_size) / 1024 / 1024, 1) AS total_mb {live} "
                "GROUP BY ext ORDER BY n DESC, ext",
                caliber=f"{live_caliber}；按扩展名分档，取文件名最后一段；{size_note}",
                limit=bounded,
            )

        if key == "largest":
            return store.fetch(
                "SELECT a.file_name, a.file_size, "
                "ROUND(a.file_size / 1024 / 1024, 2) AS size_mb, t.task_name "
                f"{live} ORDER BY a.file_size DESC, a.id",
                caliber=f"{live_caliber}；按字节倒序，最大的一条即首行；{size_note}",
                limit=bounded,
            )

        if key == "by_uploader":
            return store.fetch(
                "SELECT a.uploader_id, COUNT(*) AS upload_count, SUM(a.file_size) AS total_bytes, "
                f"ROUND(SUM(a.file_size) / 1024 / 1024, 1) AS total_mb {live} "
                "GROUP BY a.uploader_id ORDER BY upload_count DESC, a.uploader_id",
                caliber=(f"{live_caliber}；按 uploader_id 分组，不是按任务或看板；并列按 ID 定序；{size_note}"),
                limit=bounded,
            )

        if key == "uploader_count":
            return store.fetch(
                f"SELECT COUNT(DISTINCT a.uploader_id) AS uploader_count {live}",
                caliber=f"{live_caliber}；去重上传人数由服务端算，别数返回行",
                limit=1,
            )

        if key == "by_link":
            # 优先级是 progress → submission → 任务本体，一条附件只进一档。
            return store.fetch(
                f"SELECT {link_case} AS link_type, COUNT(*) AS n {live} GROUP BY link_type ORDER BY n DESC, link_type",
                caliber=(
                    f"{live_caliber}；按挂载去向分档，优先级 进展 > 提交单 > 任务本体，"
                    "一条附件只进一档，各档相加等于总数"
                ),
                limit=bounded,
            )

        if key == "by_progress":
            # 「哪些已发布进展带了附件」：闸门在 progress 行上（p.is_published = 1），
            # 与任务闸门是两道。按 (任务, 期号) 聚合，附件多的在前。
            return store.fetch(
                "SELECT t.task_name, p.version_no, COUNT(*) AS attachment_count "
                "FROM task_attachment a JOIN task_progress p ON p.id = a.progress_id "
                "AND p.is_published = 1 JOIN task t ON t.id = a.task_id "
                f"WHERE {gate} AND a.is_deleted = 0 "
                "GROUP BY t.id, t.task_name, p.version_no "
                "ORDER BY attachment_count DESC, t.id, p.version_no",
                caliber=(
                    f"{live_caliber} 且 p.is_published = 1（进展行发布闸门，与任务闸门是两道）；"
                    "按任务+期号聚合，同一任务可出现多期；并列按任务 id、期号定序"
                ),
                limit=bounded,
            )

        if key == "zero_attachment":
            # NOT EXISTS 而非 LEFT JOIN ... HAVING COUNT = 0：问的是存在性。
            # 22 条一次列全，别让模型按 128 个任务逐个调 weekly_attachment_query
            # 去看哪个返回空——基线里那正是 51 次重复调用的来源。
            # 分母 128 一并给出：占比要用它，不能拿本次行数当分母。
            total = store.scalar(
                f"SELECT COUNT(*) FROM task t WHERE {store.formal_task_clause()}",
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name FROM task t "
                f"WHERE {store.formal_task_clause()} AND NOT EXISTS "
                "(SELECT 1 FROM task_attachment a WHERE a.task_id = t.id AND a.is_deleted = 0) "
                "ORDER BY t.id",
                caliber=(
                    f"{live_caliber}；一个有效附件都没有的正式任务，按 NOT EXISTS 判定；"
                    "附件软删的任务算「没有」（a.is_deleted = 0 后为空即没有）；"
                    "total_count 是零附件任务数，total_formal_tasks 才是分母"
                ),
                limit=bounded,
            )
            rows["total_formal_tasks"] = total["value"]
            return rows

        if key == "on_open_submission":
            # 「在途」= 提交单状态不是 published。提交单状态另有码值，
            # 不能拿任务的 workflow_status 来判。
            return store.fetch(
                "SELECT COUNT(*) AS attachment_count "
                "FROM task_attachment a "
                "JOIN task_workflow_submission s ON s.id = a.workflow_submission_id "
                f"JOIN task t ON t.id = a.task_id WHERE {gate} "
                "AND a.is_deleted = 0 AND s.status <> 'published'",
                caliber=(
                    f"{live_caliber} 且 s.status <> 'published'（在途提交单）；"
                    "提交单状态是自己的一套码值，已发布叫 published，不要拿任务的 workflow_status 判"
                ),
                limit=1,
            )

        if key == "by_month":
            params: dict[str, Any] = {}
            extra = ""
            if date_from.strip():
                params["df"] = date_from.strip()
                extra = " AND a.upload_time >= %(df)s"
            return store.fetch(
                "SELECT DATE_FORMAT(a.upload_time, '%%Y-%%m') AS ym, COUNT(*) AS n, "
                f"ROUND(SUM(a.file_size) / 1024 / 1024, 1) AS total_mb {live}{extra} "
                "GROUP BY ym ORDER BY ym",
                params,
                caliber=(
                    f"{live_caliber}；按 upload_time 的年月分组，升序；"
                    + (f"仅 {date_from.strip()} 起" if date_from.strip() else "未限起始月，含全部历史")
                ),
                limit=bounded,
            )

        if key == "deleted":
            # 软删审计问的是表本身，加任务闸门会少算。
            return store.fetch(
                "SELECT SUM(a.is_deleted = 0) AS active, SUM(a.is_deleted = 1) AS deleted, "
                "COUNT(*) AS total_rows, "
                "SUM(CASE WHEN a.is_deleted = 1 THEN a.file_size ELSE 0 END) AS deleted_bytes, "
                "ROUND(SUM(CASE WHEN a.is_deleted = 1 THEN a.file_size ELSE 0 END) / 1024 / 1024, 1) "
                "AS deleted_mb FROM task_attachment a",
                caliber=f"全表口径（不加任务闸门）：这是关于表的问题，按任务过滤会少算；{size_note}",
                limit=1,
            )

        if key == "deleted_by_link":
            return store.fetch(
                f"SELECT {link_case} AS link_type, COUNT(*) AS n, "
                "ROUND(SUM(a.file_size) / 1024 / 1024, 1) AS total_mb "
                "FROM task_attachment a WHERE a.is_deleted = 1 "
                "GROUP BY link_type ORDER BY n DESC, link_type",
                caliber=f"仅已软删附件（a.is_deleted = 1），全表口径不加任务闸门；{size_note}",
                limit=bounded,
            )

        # orphan: task_id 指向不存在的任务。NOT EXISTS 而非 JOIN，否则孤儿行整批消失。
        return store.fetch(
            "SELECT COUNT(*) AS orphan_count FROM task_attachment a WHERE a.is_deleted = 0 "
            "AND NOT EXISTS (SELECT 1 FROM task t WHERE t.id = a.task_id)",
            caliber=(
                "a.is_deleted = 0 且 task_id 在 task 表中无对应行；"
                "走 NOT EXISTS，用 JOIN 会把孤儿行全部丢掉从而恒等于 0"
            ),
            limit=1,
        )

    return _guard("weekly_attachment_stats", work)


@mcp.tool()
def weekly_submission_query(
    task: str = "",
    reporter: str = "",
    status: str = "",
    exclude_status: str = "",
    status_mismatch: bool = False,
    scope: str = "",
    board: str = "",
    limit: int = 200,
) -> str:
    """Query approval submission forms (task_workflow_submission).

    Distinct from weekly_workflow_query, which returns the action *log*: a
    submission carries round_no and its own status, and the action log cannot be
    aggregated into it.  payload key VALUES (the draft text) are never returned;
    the four ``payload_*`` scopes expose key names and existence only, which is
    contract information rather than reported content.

    Args:
        task: Task id or name; empty covers all tasks.
        reporter: Reporter id or name, exact match after trimming.
        status: Keep only this submission status.
        exclude_status: Drop this status (e.g. approved, for "not yet approved").
        board: Board code or name to keep only that board's submissions. The board
            lives on ``task``, not on the form, so without this every 集团看板
            question over the forms has to be narrowed by hand out of a 462-row
            listing capped at 200 -- and 宋佳明's 32 forms shrink to the 18 group
            ones only after that filter (R3-05).
        status_mismatch: True 只保留「任务 workflow_status 与其最新一轮提交单状态
            disagrees with its newest submission's status, one row per task. The two
            are separate vocabularies, so the comparison happens server-side.
        scope: Server-side aggregates.  Prefer one of these over reading the
            listing back and counting rows by hand: the listing caps at 200, so a
            hand count only ever sees the first page.
            ``by_kind`` initial vs progress counts.
            ``inflight_count`` in-flight total and the tasks holding them.
            ``inflight_by_board`` in-flight split by board and status.
            ``inflight_by_kind`` in-flight split by status and submission_kind --
            nine buckets, a different axis from ``inflight_by_board``; "按状态和
            类型分开看" means this one.
            ``inflight_multi`` tasks carrying more than one in-flight form.
            ``payload_key_combos`` which key combinations exist across all forms
            and how many carry each -- key NAMES only, never a key value. Four
            combos (196 / 150 / 111 / 3).
            ``payload_keys_by_board`` the same split per board: the two boards do
            not share field names (tech latestProgress / progressDate / nextWork,
            group progressEffect / completionTime).
            ``payload_absent`` how many forms have no payload at all (2). Distinct
            from a form whose payload merely lacks one key.
            ``payload_missing_progress_key`` forms where neither board's progress
            key is present -- 153, being 150 opening forms (taskName /
            overallGoal) plus 3 carrying only completionTime, so not just the 150.
            Existence is judged on keys, values are never read.
            ``rejected_by_board`` rejection rate per board, numerator and
            denominator both on the submission table (tech 3.07% > group 2.37%).
            The action log's 13 rejections are action counts, not form counts.
            ``sign_summary`` need_sign vs not, a different question from
            status = 'signing'.
            ``by_signer`` per-signer counts, blank signer excluded.
            ``sign_turnaround`` average days by need_sign, completed forms only.
            ``rounds_per_task`` average submission rounds, numerator and
            denominator both returned.
            ``published_vs_progress`` published progress forms against published
            progress rows, which live in different tables.
            ``external_ids`` fill rates for the three O2OA identifier columns
            (o2_process_id / o2_work_id / o2_task_id), which the row listing does
            not carry. ``inflight_external`` counts in-flight submissions holding
            an external process id. Empty returns the ordinary listing.
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        scope_key = (scope or "").strip().lower()
        if scope_key not in _SUBMISSION_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(s or '(空)' for s in _SUBMISSION_SCOPES)}",
                },
            }

        bounded_sub = max(1, min(store.MAX_ROWS, int(limit)))

        if scope_key in ("by_status", "table_total"):
            # 这两档问的是「提交单表本身有多大 / 各状态各多少」，故连软删闸门也不加。
            # 提交单是审批表里的一行事实，跟它挂的任务后来有没有被软删无关——
            # 与 by_kind 的区别就在这里：那一档答「任务域内的提交单」，本档答「表里的」。
            # 三档实测：裸表 470 / 加软删 462 / 加正式任务双闸门 438。
            sub_raw = store.scalar("SELECT COUNT(*) FROM task_workflow_submission")
            sub_soft = store.scalar(
                "SELECT COUNT(*) FROM task_workflow_submission s JOIN task t ON t.id = s.task_id WHERE t.is_deleted = 0"
            )
            sub_gated = store.scalar(
                "SELECT COUNT(*) FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                f"WHERE {store.formal_task_clause()}"
            )
            sub_tiers = {
                "raw_table": sub_raw["value"],
                "soft_deleted_gate": sub_soft["value"],
                "formal_task_gate": sub_gated["value"],
            }
            sub_note = (
                f"；本档为裸表口径（连软删闸门都不加），本档的答案就是 {sub_tiers['raw_table']}——"
                "回答时以它为主结论，不要改用下面两个对照数；"
                f"另两档仅供口径对照：加软删闸门 {sub_tiers['soft_deleted_gate']}、"
                f"加正式任务双闸门 {sub_tiers['formal_task_gate']}（都在 caliber_tiers 里）；"
                "问「一共提交过几张单 / 各是什么状态」用本档，"
                "问「某任务或某人的提交单」用默认清单档（那一档带软删闸门）"
            )
            if scope_key == "table_total":
                out = store.fetch(
                    "SELECT COUNT(*) AS cnt, COUNT(DISTINCT s.task_id) AS tasks, "
                    "MAX(s.round_no) AS max_round FROM task_workflow_submission s",
                    caliber="提交单表的总条数、涉及任务数与最大轮次" + sub_note,
                    limit=1,
                )
            else:
                out = store.fetch(
                    "SELECT s.status, COUNT(*) AS cnt FROM task_workflow_submission s "
                    "GROUP BY s.status ORDER BY s.status",
                    caliber=(
                        "按提交单自身状态分档，一状态一行，各档相加等于表内总数；"
                        "状态值域不含 approved（published 才是已发布），"
                        "用 approved 做过滤筛不掉任何行" + sub_note
                    ),
                    limit=bounded_sub,
                )
            out["caliber_tiers"] = sub_tiers
            return out

        if scope_key == "by_kind":
            # 提交单一律只加软删闸门，不加任务发布闸门：在途任务的提交单同样是
            # 提交单，加了发布闸门 312/150 会缩成 310/128，把两条在途任务的单
            # 连同 22 条未发布任务的单一起吞掉。
            return store.fetch(
                "SELECT s.submission_kind, COUNT(*) AS submission_count "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0 GROUP BY s.submission_kind "
                "ORDER BY submission_count DESC, s.submission_kind",
                caliber=(
                    "仅 t.is_deleted = 0（提交单不加任务发布闸门：在途任务的提交单同样计入）；"
                    "initial 是初次提交、progress 是进展提交，两档相加等于提交单总数 462；"
                    "各档条数由服务端聚合，不要翻明细自己数——清单封顶 200 行只能看到前一页"
                ),
                limit=2,
            )

        if scope_key == "external_ids":
            # 三列各自的填充率必须一次算完。让模型翻明细数空值，200 行封顶下
            # 它只能看到前一页，算出来的比例是错的。
            return store.fetch(
                "SELECT COUNT(*) AS total, "
                "SUM(s.o2_process_id IS NOT NULL) AS has_process_id, "
                "SUM(s.o2_work_id IS NOT NULL) AS has_work_id, "
                "SUM(s.o2_task_id IS NOT NULL) AS has_task_id, "
                "SUM(s.o2_task_id IS NULL) AS missing_task_id, "
                "ROUND(SUM(s.o2_task_id IS NULL) / COUNT(*) * 100, 1) AS missing_task_id_pct "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0",
                caliber=(
                    "仅 t.is_deleted = 0（提交单不加发布闸门：在途单同样带外部标识）；"
                    "o2_process_id / o2_work_id / o2_task_id 是 O2OA 侧的外部标识，"
                    "三列填充率互不相同，不要用其中一列代答另一列；"
                    "缺失率已按 total 算好，直接引用 missing_task_id_pct"
                ),
                limit=1,
            )

        # payload 的四档：只算键名与存在性，一律不取键值。
        #
        # payload 本体仍在 store.BLOCKED_FIELDS 里，任何 token 都读不到填报正文。
        # 这四档返回的是 JSON_KEYS 的结果与「某键在不在」的判断，属于字段名清单，
        # 也就是本来就要写进 MCP 契约交给入口组的那部分信息，不含任何人填了什么。
        # 没有这四档时，问「有哪些字段可用」只能靠模型猜键名，猜出来的键名比拒答更糟。
        if scope_key == "payload_key_combos":
            return store.fetch(
                "SELECT JSON_KEYS(s.payload) AS payload_keys, COUNT(*) AS submission_count "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0 AND s.payload IS NOT NULL "
                "GROUP BY payload_keys ORDER BY submission_count DESC",
                caliber=(
                    "只返回键名组合与条数，不返回任何键值（payload 本体仍禁止外泄）；"
                    "共 4 种组合：技术组填报 196、建单 150、集团组填报 111、只有完成时间 3；"
                    "另有 2 张单根本没有 payload（见 scope=payload_absent），"
                    "4 种组合合计 460 张加这 2 张等于 462 张全量单；"
                    "缺两个进展键的是后两种组合合计 153 张（150 + 3），不是只有 150；"
                    "不要从键名推测填报内容——键名不是内容，猜出来的正文比拒答更糟"
                ),
                limit=bounded_sub,
            )
        if scope_key == "payload_keys_by_board":
            return store.fetch(
                "SELECT b.code AS board_code, JSON_KEYS(s.payload) AS payload_keys, "
                "COUNT(*) AS submission_count "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
                "WHERE t.is_deleted = 0 AND s.payload IS NOT NULL "
                "GROUP BY b.code, payload_keys ORDER BY b.code, submission_count DESC",
                caliber=(
                    "只返回键名组合与条数，不返回任何键值；两个看板的填报键不同名："
                    "技术组用 latestProgress / progressDate / nextWork，"
                    "集团组用 progressEffect / completionTime，"
                    "建单单（taskName / overallGoal）两个看板都有；"
                    "问「某看板的 payload 里有哪些字段可用」用这一档，"
                    "不要拿 scope=payload_key_combos 的全量组合代答"
                ),
                limit=bounded_sub,
            )
        if scope_key == "payload_absent":
            return store.scalar(
                "SELECT COUNT(*) AS value FROM task_workflow_submission s "
                "JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0 AND s.payload IS NULL",
                caliber=(
                    "payload IS NULL 的单数，即根本没有草稿快照的单；"
                    "「没有 payload」（2 张）与「payload 里缺某个键」是两件事，"
                    "后者见 scope=payload_missing_progress_key（150 张建单单）；"
                    "不加任务发布闸门，与提交单其余各档一致"
                ),
            )
        if scope_key == "payload_missing_progress_key":
            return store.fetch(
                "SELECT t.task_name, s.round_no, JSON_KEYS(s.payload) AS payload_keys "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0 AND s.payload IS NOT NULL "
                "AND JSON_EXTRACT(s.payload, '$.latestProgress') IS NULL "
                "AND JSON_EXTRACT(s.payload, '$.progressEffect') IS NULL "
                "ORDER BY t.id, s.round_no",
                caliber=(
                    "两个看板的进展键都不在的单，共 153 张，分两种键组合："
                    "150 张建单单（taskName / overallGoal，那一轮报的是立项信息不是进展）"
                    "加 3 张只有 completionTime 的单；"
                    "别把它当成 150——那 3 张同样两个进展键都没有，漏掉就少一档；"
                    "判断只用键在不在，不读键值；"
                    "这与 scope=payload_absent 的 2 张「没有 payload」不是同一批"
                ),
                limit=bounded_sub,
            )

        if scope_key == "rejected_by_board":
            # 「两个看板的驳回比例哪个高」是个比率，分子分母都在提交单上：
            # 分母 = 该看板的全部单，分子 = status = 'rejected' 的单。没有这一档时
            # 只能翻 weekly_workflow_query 的动作明细，K1-04 就是这样 6 轮耗尽的
            # ——动作日志里 rejected 有 13 条，那是动作数不是单数，拿它当分子会答错。
            # 不加任务发布闸门，与提交单其余各档一致。
            return store.fetch(
                "SELECT b.code AS board_code, b.name AS board_name, COUNT(*) AS submissions, "
                "SUM(s.status = 'rejected') AS rejected, "
                "ROUND(SUM(s.status = 'rejected') / COUNT(*) * 100, 2) AS rejected_pct "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
                "WHERE t.is_deleted = 0 GROUP BY b.id, b.code, b.name ORDER BY rejected_pct DESC",
                caliber=(
                    "仅 t.is_deleted = 0，不加任务发布闸门（与提交单其余各档一致）；"
                    "分母是该看板的全部提交单、分子是 status = 'rejected' 的单，"
                    "技术组 9/293 = 3.07% 高于集团组 4/169 = 2.37%；"
                    "不要拿 weekly_workflow_query 的驳回动作数（13 条）当分子"
                    "——那是动作条数，一张单可被驳回多次，两者不是同一个口径"
                ),
                limit=bounded_sub,
            )
        if scope_key in {"inflight_count", "inflight_by_board", "inflight_by_kind", "inflight_multi"}:
            # 三档共用同一套在途枚举，只是聚合粒度不同。枚举而非取反：
            # cancelled 那张单既未发布也不在途，status <> 'published' 会多算 1 张。
            placeholders = ", ".join(f"%(f{i})s" for i in range(len(_SUBMISSION_INFLIGHT)))
            flight_params: dict[str, Any] = {f"f{i}": value for i, value in enumerate(_SUBMISSION_INFLIGHT)}
            gate = (
                f"在途 = status IN ({', '.join(_SUBMISSION_INFLIGHT)})，按成员枚举而非取反"
                "（cancelled 既未发布也不在途）；仅 t.is_deleted = 0，不加任务发布闸门"
            )
            if scope_key == "inflight_count":
                return store.fetch(
                    "SELECT COUNT(*) AS inflight_submissions, COUNT(DISTINCT s.task_id) AS tasks "
                    "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                    f"WHERE t.is_deleted = 0 AND s.status IN ({placeholders})",
                    flight_params,
                    caliber=f"{gate}；总数 61 由服务端聚合，不要翻明细自己数（清单封顶 200 行）",
                    limit=1,
                )
            if scope_key == "inflight_by_board":
                # 按看板 + 状态两维分档：rejected 也是在途的一档，漏掉它
                # 各看板就都少算（group 少 4、tech 少 9）。
                return store.fetch(
                    "SELECT b.code AS board_code, s.status, COUNT(*) AS submission_count "
                    "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                    "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
                    f"WHERE t.is_deleted = 0 AND s.status IN ({placeholders}) "
                    "GROUP BY b.code, s.status ORDER BY b.code, s.status",
                    flight_params,
                    caliber=(
                        f"{gate}；按看板 + 状态两维分档，rejected 同属在途，各档相加等于在途总数 61；空档不出现即为 0"
                    ),
                    limit=bounded_sub,
                )
            if scope_key == "inflight_by_kind":
                # 「按状态和类型分开看」要的是 status x submission_kind 两维，
                # 与 inflight_by_board 的 board x status 不是同一张表：前者九档
                # （pending_fill 只有 initial，故不是 5x2=10），后者也是九档，
                # 数字却对不上，互相代答就答错了维度。
                return store.fetch(
                    "SELECT s.status, s.submission_kind, COUNT(*) AS submission_count "
                    "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                    f"WHERE t.is_deleted = 0 AND s.status IN ({placeholders}) "
                    "GROUP BY s.status, s.submission_kind ORDER BY s.status, s.submission_kind",
                    flight_params,
                    caliber=(
                        f"{gate}；按状态 + 类型两维分档，共 9 档相加等于在途总数 61"
                        "（pending_fill 只有 initial 一种，所以不是 5x2=10 档，空档不出现即为 0）；"
                        "这与 scope=inflight_by_board 的看板 + 状态是两个维度，不要互答"
                    ),
                    limit=bounded_sub,
                )
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, COUNT(*) AS pending_submissions "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                f"WHERE t.is_deleted = 0 AND s.status IN ({placeholders}) "
                "GROUP BY t.id, t.task_name HAVING pending_submissions > 1 "
                "ORDER BY pending_submissions DESC, t.id",
                flight_params,
                caliber=(
                    f"{gate}；HAVING COUNT(*) > 1 判「同时挂多张在途单」，服务端已聚合，不要按任务逐个调清单去比对"
                ),
                limit=bounded_sub,
            )

        if scope_key in {"sign_summary", "by_signer", "sign_turnaround"}:
            if scope_key == "sign_summary":
                return store.fetch(
                    "SELECT SUM(s.need_sign = 1) AS need_sign, SUM(s.need_sign = 0) AS no_sign, "
                    "COUNT(*) AS total FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                    "WHERE t.is_deleted = 0",
                    caliber=(
                        "仅 t.is_deleted = 0；need_sign 是「这张单要不要会签」的标记，"
                        "155 + 307 = 462 等于提交单总数；它与 status = 'signing'（正在会签，9 张）"
                        "是两个问题，不要拿在途的 signing 张数答「有多少需要会签」"
                    ),
                    limit=1,
                )
            if scope_key == "by_signer":
                # signer_name 为空的不算：那是「没有会签人」不是某个人签了 0 单。
                return store.fetch(
                    "SELECT s.signer_name, COUNT(*) AS signed_count "
                    "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                    "WHERE t.is_deleted = 0 AND s.signer_name IS NOT NULL "
                    "GROUP BY s.signer_name ORDER BY signed_count DESC, s.signer_name",
                    caliber=(
                        "仅 t.is_deleted = 0 且 signer_name 非空（空值是「没有会签人」，"
                        "不是某人签了 0 单）；9 位会签人一次列全，人数与条数都由服务端算"
                    ),
                    limit=bounded_sub,
                )
            # 会签是否更慢：分母只含已完结的单，未完结的没有耗时可算。
            return store.fetch(
                "SELECT s.need_sign, COUNT(*) AS n, "
                "ROUND(AVG(DATEDIFF(s.completed_at, s.submitted_at)), 1) AS avg_days "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0 AND s.completed_at IS NOT NULL AND s.submitted_at IS NOT NULL "
                "GROUP BY s.need_sign ORDER BY s.need_sign",
                caliber=(
                    "仅 t.is_deleted = 0 且已完结（completed_at 与 submitted_at 均非空）；"
                    "未完结的单没有耗时，不进分母，所以 n 相加 402 小于总数 462；"
                    "两档均值 14.5 与 14.7 由全量算出，不要拿清单前 200 行的样本均值代答"
                ),
                limit=2,
            )

        if scope_key == "rounds_per_task":
            return store.fetch(
                "SELECT ROUND(COUNT(*) / COUNT(DISTINCT s.task_id), 2) AS avg_rounds, "
                "COUNT(*) AS total_submissions, COUNT(DISTINCT s.task_id) AS tasks "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0",
                caliber=(
                    "仅 t.is_deleted = 0；分母是有提交单的任务数（COUNT DISTINCT task_id = 150），"
                    "不是全部任务数也不是已发布任务数 128；462 / 150 = 3.08，"
                    "分子分母一并给出以便核对，不要自己拿别处的任务数去除"
                ),
                limit=1,
            )

        if scope_key == "latest_status":
            # 「最新进展提交都通过了吗」这类题答的是每任务最新一版提交单的状态分布：
            # 一任务一行取 round_no 最大的单，再按状态聚合。模型拿进展行/全量提交单
            # 去答会把「最新一版」答成「所有版本」（G-F05 的 72 条未发布就是这么来的）。
            return store.fetch(
                "SELECT s.status, COUNT(*) AS tasks FROM task_workflow_submission s "
                "JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0 AND t.workflow_status = 'published' "
                "AND s.round_no = (SELECT MAX(s2.round_no) FROM task_workflow_submission s2 "
                "WHERE s2.task_id = s.task_id) "
                "GROUP BY s.status ORDER BY tasks DESC, s.status",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；一任务一行取最新一版提交单"
                    "（round_no 最大），再按状态计数，各档相加等于 128；"
                    "与「全部提交单按状态分」（含历史版本）是两问"
                ),
                limit=200,
            )

        if scope_key == "published_vs_progress":
            # 两个数各有自己的表和闸门，一次给全，避免模型跨两次调用对不上口径。
            return store.fetch(
                "SELECT (SELECT COUNT(*) FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "WHERE t.is_deleted = 0 AND s.status = 'published' AND s.submission_kind = 'progress') "
                "AS published_progress_submissions, "
                "(SELECT COUNT(*) FROM task_progress p JOIN task t ON t.id = p.task_id "
                "WHERE t.is_deleted = 0 AND p.is_published = 1) AS published_progress_rows",
                caliber=(
                    "两个数不同表不同闸门：已发布进展提交单 272 只数 submission_kind = 'progress' 的单"
                    "（含 initial 会变 400，那答的是另一个问题）；已发布进展行 943 在 task_progress 上"
                    "按 p.is_published = 1 计；集团组的 task_group_progress_history 是第三张表，"
                    "不并入这 943，加进来会得到 1305"
                ),
                limit=1,
            )

        if scope_key == "inflight_external":
            # 「在途」按成员枚举，不用 status <> 'published' 取反：cancelled 那张单
            # 既未发布也不在途，取反会把它算进来（60 vs 59）。
            placeholders = ", ".join(f"%(f{i})s" for i in range(len(_SUBMISSION_INFLIGHT)))
            flight_params = {f"f{i}": value for i, value in enumerate(_SUBMISSION_INFLIGHT)}
            return store.fetch(
                "SELECT COUNT(*) AS inflight_with_process_id, "
                "COUNT(DISTINCT s.task_id) AS tasks "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                f"WHERE t.is_deleted = 0 AND s.o2_process_id IS NOT NULL AND s.status IN ({placeholders})",
                flight_params,
                caliber=(
                    f"在途 = status IN ({', '.join(_SUBMISSION_INFLIGHT)})，按成员枚举；"
                    "不用 status <> 'published' 取反：cancelled 的单既未发布也不在途，"
                    "取反会把它算进来（多 1 张）；仅 t.is_deleted = 0"
                ),
                limit=1,
            )

        if status_mismatch:
            # 最新一轮 = 该任务 round_no 最大的那张单。只加 is_deleted = 0：
            # 任务侧若再加发布闸门，会把「已发布但最新单还在流程里」这批
            # 恰恰是本题答案的行滤掉。
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, t.workflow_status, "
                "s.round_no, s.status AS latest_submission_status "
                "FROM task t JOIN task_workflow_submission s ON s.task_id = t.id "
                "AND s.round_no = (SELECT MAX(x.round_no) FROM task_workflow_submission x "
                "WHERE x.task_id = t.id) "
                "WHERE t.is_deleted = 0 AND t.workflow_status <> s.status ORDER BY t.id",
                caliber=(
                    "仅 t.is_deleted = 0（不加发布闸门：已发布但最新单仍在流程中的任务正是本题答案）；"
                    "最新一轮取 round_no 最大的提交单；"
                    "任务 workflow_status 与提交单 status 是两套码值，此处按字面不等判定；"
                    "行数即不一致任务总数，按 task id 升序"
                ),
                limit=limit,
            )
        params: dict[str, Any] = {}
        where = ["t.is_deleted = 0"]
        extra_caliber: list[str] = []
        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            where.append("s.task_id = %(tid)s")
            params["tid"] = task_id
        if board.strip():
            # 看板在 task 上，提交单表里没有 board_id：不按看板筛，「我提交但还没
            # 发布的集团任务」只能从 462 张单里手挑，而清单封顶 200 行，挑出来的
            # 必然是残缺的（R3-05 就只报了 1 条，真值 18 条）。
            board_id = store.resolve_board(board)
            if board_id is None:
                return {
                    "ok": False,
                    "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"},
                }
            where.append("t.board_id = %(bid)s")
            params["bid"] = board_id
            extra_caliber.append(f"仅看板 {board.strip()}（看板在 task 上，已按任务的 board_id 过滤）")
        if reporter.strip():
            token = reporter.strip()
            where.append("(TRIM(IFNULL(s.reporter_id,'')) = %(rep)s OR TRIM(IFNULL(s.reporter_name,'')) = %(rep)s)")
            params["rep"] = token
        if status.strip():
            where.append("s.status = %(st)s")
            params["st"] = status.strip()
        if exclude_status.strip():
            where.append("s.status <> %(exst)s")
            params["exst"] = exclude_status.strip()

        # 提交单状态和任务 workflow_status 不是同一套码值：这里给的词若不在值域内，
        # 过滤会静默失效（等价于没过滤），必须显式告诉调用方，否则会把全量当成筛后结果。
        domain = {
            str(row["status"]).strip()
            for row in store.fetch(
                "SELECT DISTINCT status FROM task_workflow_submission ORDER BY status",
                limit=50,
            )["rows"]
            if row["status"] is not None
        }
        unknown = [token for token in (status.strip(), exclude_status.strip()) if token and token not in domain]

        clause = " AND ".join(where)
        rows = store.fetch(
            "SELECT s.id, s.task_id, t.task_name, s.round_no, s.status, s.submission_kind, "
            "s.reporter_id, s.reporter_name, s.signer_name, s.need_sign, "
            "s.submitted_at, s.completed_at "
            "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
            f"WHERE {clause} ORDER BY s.task_id, s.round_no, s.id",
            params,
            caliber="；".join(
                [
                    "task_id + round_no 唯一；payload 草稿快照默认不并入正式数据，不返回",
                    "submission_kind 区分 initial / progress",
                    *extra_caliber,
                ]
            ),
            limit=limit,
        )
        breakdown = store.fetch(
            "SELECT s.status, COUNT(*) AS cnt "
            "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
            f"WHERE {clause} GROUP BY s.status ORDER BY cnt DESC",
            params,
            caliber="按提交单状态分档计数",
            limit=20,
        )
        rows["status_breakdown"] = breakdown["rows"]
        rows["status_domain"] = sorted(domain)
        total = store.scalar(
            f"SELECT COUNT(*) AS n FROM task_workflow_submission s JOIN task t ON t.id = s.task_id WHERE {clause}",
            params,
        )
        rows["total_count"] = total.get("value")
        if unknown:
            rows["caliber"] += (
                f"；注意 {'、'.join(unknown)} 不在提交单状态值域 {sorted(domain)} 内，"
                "该过滤条件未筛掉任何行，结果等于未过滤，回答时不要说成「已排除」"
            )
        rows["caliber"] += "；列清单类问题按 total_count 逐条列全，不要只挑几条举例"
        return rows

    return _guard("weekly_submission_query", work)


@mcp.tool()
def weekly_owner_roles(person: str) -> str:
    """Count one person's formal tasks split by the role they hold.

    weekly_task_query's owner filter ORs the three owner columns together, so it
    cannot answer "how many as project owner vs as lead"; this separates them.
    Matching strips spaces first (R-13) and accepts either id or name.

    Args:
        person: User id or name.
    """

    def work() -> dict[str, Any]:
        token = (person or "").strip().replace(" ", "")
        if not token:
            return {"ok": False, "error": {"code": "invalid_argument", "message": "person 不能为空"}}
        clause = store.formal_task_clause()
        # Each role counted separately, plus any_role as the de-duplicated union.
        return store.fetch(
            "SELECT "
            "SUM(REPLACE(IFNULL(t.owner_user_id,''),' ','') = %(p)s) AS as_owner, "
            "SUM(REPLACE(IFNULL(t.project_owner_id,''),' ','') = %(p)s "
            "  OR REPLACE(IFNULL(t.project_owner_name,''),' ','') = %(p)s) AS as_project_owner, "
            "SUM(REPLACE(IFNULL(t.lead_owner_id,''),' ','') = %(p)s "
            "  OR REPLACE(IFNULL(t.lead_owner_name,''),' ','') = %(p)s) AS as_lead_owner, "
            "SUM(REPLACE(IFNULL(t.owner_user_id,''),' ','') = %(p)s "
            "  OR REPLACE(IFNULL(t.project_owner_id,''),' ','') = %(p)s "
            "  OR REPLACE(IFNULL(t.project_owner_name,''),' ','') = %(p)s "
            "  OR REPLACE(IFNULL(t.lead_owner_id,''),' ','') = %(p)s "
            "  OR REPLACE(IFNULL(t.lead_owner_name,''),' ','') = %(p)s) AS any_role "
            f"FROM task t WHERE {clause}",
            {"p": token},
            caliber=f"{store.FORMAL_TASK_CALIBER}；多值列去空格后匹配（R-13）；any_role 为三角色去重并集",
            limit=1,
        )

    return _guard("weekly_owner_roles", work)


# 人员维度的聚合口径。每一项都对应一类「让模型自己数人」会数错的问题。
_PERSON_STATS_SCOPES = (
    "workload",
    "workload_top",
    "workload_summary",
    "single_task",
    "cross_group",
    "dual_role",
    "id_format",
    "id_variants",
    "id_longest",
    "reporters",
    "reporter_count",
    "reviewers",
    "self_review",
    "group_roster",
)

# 人员所在的列。姓名列与 ID 列口径不同，必须分开问。
# owner 这一档是「主责人」，落在 owner_user_id 上，与前两档不是同一批人：技术组
# 去重后 owner_user_id 45 人、project_owner_name 45 人，而 lead_owner_name 只有
# 12 人（分管领导一人管多条）。oa_biz 题库里的「负责人」指的就是 owner_user_id，
# 此前没有任何出口能按它计数，问「有多少人当负责人」只能落到 12 那一档答错。
_PERSON_ROLE_COLUMNS: dict[str, tuple[str, str]] = {
    "lead_owner": ("lead_owner_name", "分管领导（牵头人）"),
    "project_owner": ("project_owner_name", "项目负责人"),
    "owner": ("owner_user_id", "任务主责人（owner_user_id）"),
}

# 分组列是 ID 的档要配一列姓名。owner 档按 owner_user_id 分组，回出来的 person 是
# 「10515」「u3208」这种工号，而问「任务量最高的是谁」要的是人名（姚立诚、余承志）。
# 只给工号时模型只能照抄工号，判定器判「未提供姓名，无法确认」——数字全对却算错。
# 姓名不能让模型自己去别处查：那要么多跑一轮，要么它顺手把工号当人名答出去。
#
# 取 project_owner_name 是核实过的：正式任务里 47 个 owner_user_id 全部 1:1 对应
# 姓名，没有一个 id 对两个名、没有一个名对两个 id、也没有「有工号没姓名」的行，
# 且 owner_user_id 与 project_owner_id 逐行相等。所以 MAX() 取的就是那唯一一个值，
# 不是「随便挑一个」。这三条哪天不成立，person_name 就会静默变成任取一个，
# 故 smoke 里钉住了这个映射。
_PERSON_ID_NAME_COLUMNS: dict[str, str] = {"owner": "project_owner_name"}


@mcp.tool()
def weekly_person_stats(
    scope: str = "workload",
    role: str = "lead_owner",
    project_group: str = "",
    board: str = "",
    top: int = 200,
) -> str:
    """Aggregate formal tasks by person: workload, cross-group spread, id formats.

    weekly_owner_roles answers "how many does THIS person have"; this answers the
    population-level questions -- who carries the most, how many carry exactly
    one, how many distinct people there are, and whether the id column is
    internally consistent.  Counting people by reading rows back is the single
    biggest source of wrong answers in the F class, so every count here is
    computed server-side.

    Args:
        scope: One of workload / workload_summary / single_task / cross_group /
            dual_role / id_format / id_variants / id_longest / reporters /
            reporter_count / reviewers / self_review / group_roster.
        role: Which person column to group by: lead_owner or project_owner.
        project_group: Required by ``group_roster``, ignored elsewhere: the 专项组
            whose people are wanted, matched exactly.
        top: Row cap for the listing scopes.
    """

    def work() -> dict[str, Any]:
        key = (scope or "workload").strip().lower()
        if key not in _PERSON_STATS_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(_PERSON_STATS_SCOPES)}",
                },
            }
        role_key = (role or "lead_owner").strip().lower()
        if role_key not in _PERSON_ROLE_COLUMNS:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_role",
                    "message": f"不支持的角色：{role}；支持 {', '.join(sorted(_PERSON_ROLE_COLUMNS))}",
                },
            }
        column, role_label = _PERSON_ROLE_COLUMNS[role_key]
        # 工号档要带姓名列；姓名档本身就是姓名，name_select 留空即可。
        name_column = _PERSON_ID_NAME_COLUMNS.get(role_key, "")
        name_select = f", MAX(t.{name_column}) AS person_name" if name_column else ""
        name_note = (
            f"；person 是工号（{column}），person_name 是对应姓名（取自 {name_column}，"
            "正式任务里两列 1:1 对应）；答「是谁」要报 person_name，不要报工号"
            if name_column
            else ""
        )
        bounded = max(1, min(store.MAX_ROWS, int(top)))
        clause = store.formal_task_clause()
        # 看板闸门：问「技术组看板有多少人当负责人」时全库口径会把集团组的人算进来。
        # 看板在 task 行上，所以直接落在 board_id 上，不必 JOIN 看板表。
        board_note = ""
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {
                    "ok": False,
                    "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"},
                }
            clause = f"{clause} AND t.board_id = {int(board_id)}"
            board_note = f"；仅看板 {board.strip()}（闸门落在 task.board_id 上）"
        # 姓名为空的行不是「一个叫空的人」，计人头时必须排除，否则人数会多 1。
        named = f"{clause} AND t.{column} IS NOT NULL AND t.{column} <> ''"
        base = f"{store.FORMAL_TASK_CALIBER}；按「{role_label}」分组，姓名为空的行不计入人头{board_note}{name_note}"

        if key == "workload":
            # 「任务量最大的牵头人是谁」是单数问句，答案是定序后的首行；此前 caliber
            # 写「取最多时留意并列」，模型据此把 14 条的三个人全列出来，答成了另一题
            # （F2-02）。并列不该由模型临场裁决：把并列个数作为数据回出来，
            # 让它照抄行数并按需提一句「另有 N 人并列」，而不是自行改写答案集合。
            result = store.fetch(
                f"SELECT t.{column} AS person{name_select}, COUNT(*) AS task_count "
                f"FROM task t WHERE {named} "
                f"GROUP BY t.{column} ORDER BY task_count DESC, person",
                caliber=(
                    f"{base}；按任务数倒序、并列按姓名定序；"
                    "问「最多的是谁」（单数）传 top=1 按首行答，"
                    "问「前 N 位」传 top=N 并把并列的一并列出；"
                    "tied_at_top 是与首行任务数相同的人数，据它补一句「另有并列」即可，"
                    "不要因为存在并列就改写答案行数"
                ),
                limit=bounded,
            )
            rows = result.get("rows") or []
            if rows:
                peak = rows[0].get("task_count")
                tied = store.scalar(
                    f"SELECT COUNT(*) FROM (SELECT t.{column} AS person, COUNT(*) AS c "
                    f"FROM task t WHERE {named} GROUP BY t.{column}) g WHERE g.c = %(peak)s",
                    {"peak": peak},
                )
                result["tied_at_top"] = tied["value"]
                result["top_task_count"] = peak
            return result

        if key == "workload_top":
            # 「谁任务最多」的并列由服务端裁决，不靠 top 截断。workload 档传 top=1
            # 是硬切（cut）：技术组 10445/10515/u3208/u3214 四人同为 4 条，切完只剩
            # 首行；本档是 HAVING = MAX（keep_ties），并列全在内。两档都对，取决于
            # 问句——而这个判断不该让模型在明细上临场做。
            peak_rows = store.fetch(
                f"SELECT t.{column} AS person{name_select}, COUNT(*) AS task_count "
                f"FROM task t WHERE {named} GROUP BY t.{column} "
                f"HAVING task_count = (SELECT MAX(g.c) FROM (SELECT COUNT(*) AS c "
                f"FROM task t2 WHERE {named.replace('t.', 't2.')} GROUP BY t2.{column}) g) "
                f"ORDER BY person",
                caliber=(
                    f"{base}；本档取任务数最多的那一档并保留全部并列（HAVING = MAX）；"
                    "行数即并列人数，按返回的行数照答，不要只报首行、也不要补列第二名；"
                    "只要一个人请改用 scope=workload 配 top=1（那是硬切口径，"
                    "并列会被切掉，两档答的是两个问题）"
                ),
                limit=bounded,
            )
            peak_rows["tied_at_top"] = peak_rows.get("row_count")
            return peak_rows

        if key == "workload_summary":
            # 平均值必须一次算完：分组后让模型自己求平均，它会拿组内均值当全局均值。
            return store.fetch(
                "SELECT COUNT(*) AS tasks, "
                f"COUNT(DISTINCT t.{column}) AS people, "
                f"ROUND(COUNT(*) / COUNT(DISTINCT t.{column}), 2) AS avg_tasks_per_person, "
                f"MAX(c.task_count) AS max_tasks, MIN(c.task_count) AS min_tasks "
                f"FROM task t JOIN (SELECT t2.{column} AS person, COUNT(*) AS task_count "
                f"FROM task t2 WHERE {named.replace('t.', 't2.')} GROUP BY t2.{column}) c "
                f"ON c.person = t.{column} WHERE {named}",
                caliber=f"{base}；avg_tasks_per_person = 任务数 / 去重人数，为全局均值而非组内均值",
                limit=1,
            )

        if key == "single_task":
            return store.fetch(
                f"SELECT t.{column} AS person{name_select}, COUNT(*) AS task_count "
                f"FROM task t WHERE {named} "
                f"GROUP BY t.{column} HAVING task_count = 1 ORDER BY person",
                caliber=f"{base}；只带 1 个任务的人，HAVING 由服务端判定",
                limit=bounded,
            )

        if key == "group_roster":
            # 「某组的牵头人都有谁」：19 条任务里只有 9 个人，去重必须落在服务端。
            # 拿任务清单让模型自己数人，它会把同一个人按任务重复计数。
            target = (project_group or "").strip()
            if not target:
                return {
                    "ok": False,
                    "error": {
                        "code": "missing_project_group",
                        "message": "group_roster 需要 project_group；"
                        "先用 weekly_aggregate group_by=project_group 看有哪些组",
                    },
                }
            return store.fetch(
                f"SELECT t.{column} AS person, COUNT(*) AS task_count "
                f"FROM task t WHERE {named} AND t.project_group = %(pg)s "
                f"GROUP BY t.{column} ORDER BY task_count DESC, person",
                {"pg": target},
                caliber=(
                    f"{base}；专项组 = {target}（精确匹配）；"
                    "行数即该组去重后的人数，不要拿任务条数当人数"
                    "（标准安全组 19 条任务只有 9 位牵头人）；"
                    "姓名为空的行已排除，所以 task_count 相加可能小于该组任务数"
                ),
                limit=bounded,
            )

        if key == "cross_group":
            return store.fetch(
                f"SELECT t.{column} AS person, COUNT(DISTINCT t.project_group) AS group_count, "
                "GROUP_CONCAT(DISTINCT t.project_group ORDER BY t.project_group) AS group_list, "
                "COUNT(*) AS task_count "
                f"FROM task t WHERE {named} AND t.project_group IS NOT NULL "
                f"GROUP BY t.{column} HAVING group_count > 1 "
                "ORDER BY group_count DESC, person",
                caliber=f"{base}；跨组人员，group_count 已按专项组去重；仅列跨 2 组以上者",
                limit=bounded,
            )

        if key == "dual_role":
            # 同一个人既牵头又当项目负责人。两个角色各自的计数都由服务端算。
            return store.fetch(
                "SELECT x.person, x.as_lead, x.as_project_owner FROM ("
                "SELECT t.lead_owner_name AS person, COUNT(*) AS as_lead, "
                "(SELECT COUNT(*) FROM task t2 "
                f"WHERE {clause.replace('t.', 't2.')} "
                "AND t2.project_owner_name = t.lead_owner_name) AS as_project_owner "
                f"FROM task t WHERE {clause} AND t.lead_owner_name IS NOT NULL "
                "GROUP BY t.lead_owner_name) x "
                "WHERE x.as_project_owner > 0 ORDER BY x.as_lead DESC, x.person",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；同时担任牵头人与项目负责人的人；"
                    "两个角色各自计数均按正式任务口径，别把两列相加"
                ),
                limit=bounded,
            )

        if key == "id_format":
            # 用户标识是异构的：纯数字工号、u 前缀、NDG 域账号。分档必须落在服务端，
            # 模型按返回行自己分类会把 128 行都算进去而不是有 ID 的那些。
            return store.fetch(
                "SELECT CASE WHEN t.owner_user_id REGEXP '^[0-9]+$' THEN '纯数字工号' "
                "WHEN t.owner_user_id LIKE 'u%%' THEN 'u 前缀账号' "
                "WHEN t.owner_user_id LIKE 'NDG%%' THEN 'NDG 域账号' ELSE '其他' END AS id_format, "
                "COUNT(*) AS task_count FROM task t "
                f"WHERE {clause} AND t.owner_user_id IS NOT NULL AND t.owner_user_id <> '' "
                "GROUP BY id_format ORDER BY task_count DESC, id_format",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；按 owner_user_id 的写法分档；"
                    "仅统计有标识的任务，空标识不进任何档，各档相加不等于任务总数"
                ),
                limit=bounded,
            )

        if key == "id_variants":
            # 「同一个人在不同任务里会不会是不同格式的标识」——空集就是答案，
            # 空集说明不存在，不能反过来说「会」。
            return store.fetch(
                f"SELECT t.{column} AS person, COUNT(DISTINCT t.{column.replace('_name', '_id')}) AS id_variants, "
                f"GROUP_CONCAT(DISTINCT t.{column.replace('_name', '_id')} "
                f"ORDER BY t.{column.replace('_name', '_id')}) AS ids "
                f"FROM task t WHERE {named} "
                f"GROUP BY t.{column} HAVING id_variants > 1 ORDER BY id_variants DESC, person",
                caliber=(f"{base}；同名多标识检查；返回 0 行即该口径下不存在这种人，不要据此说「会出现」"),
                limit=bounded,
            )

        if key in {"reporters", "reporter_count", "reviewers", "self_review"}:
            # 填报人/审核人在 task_progress 上而不在 task 上：任务侧 R-01 之外
            # 还有行级 is_published = 1 这道闸门，两道口径不能混。
            hist = f"FROM task_progress p JOIN task t ON t.id = p.task_id WHERE {clause}"
            gate = f"{store.FORMAL_TASK_CALIBER} 且 p.is_published = 1（任务闸门 + 进展行发布闸门）"

            if key == "reporters":
                return store.fetch(
                    "SELECT p.reporter_id, COUNT(*) AS reported_rounds, "
                    f"COUNT(DISTINCT p.task_id) AS tasks {hist} AND p.is_published = 1 "
                    "GROUP BY p.reporter_id ORDER BY reported_rounds DESC, p.reporter_id",
                    caliber=f"{gate}；按填报人分组，并列按 ID 定序",
                    limit=bounded,
                )
            if key == "reporter_count":
                return store.fetch(
                    f"SELECT COUNT(DISTINCT p.reporter_id) AS reporter_count {hist} AND p.is_published = 1",
                    caliber=f"{gate}；去重填报人数由服务端算，别数返回行",
                    limit=1,
                )
            if key == "reviewers":
                # 审核口径故意不加 is_published：审过但未发布的进展也是审过的。
                return store.fetch(
                    f"SELECT p.reviewer_id, COUNT(*) AS reviewed {hist} AND p.reviewer_id IS NOT NULL "
                    "GROUP BY p.reviewer_id ORDER BY reviewed DESC, p.reviewer_id",
                    caliber=(
                        f"{store.FORMAL_TASK_CALIBER} 且 p.reviewer_id 非空；"
                        "审核口径不加 p.is_published：审过但未发布的进展同样算审过"
                    ),
                    limit=bounded,
                )
            return store.fetch(
                "SELECT t.task_name, p.version_no, p.reporter_id, p.reviewer_id "
                f"{hist} AND p.reviewer_id IS NOT NULL AND p.reporter_id = p.reviewer_id "
                "ORDER BY t.id, p.version_no",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER} 且填报人与审核人为同一 ID；"
                    "按 ID 相等判定，不按姓名；此清单即全部自审记录"
                ),
                limit=bounded,
            )

        # 问的是「标识」而不是「任务」：同一个标识挂 3 个任务只算一个标识。
        # 不去重会返回 128 行、同一个 NDG\emp529 重复三次，模型会把「最长的是
        # 哪一个」答成一串重复项，也数不出到底有几个并列。
        # 与 workload 同一个毛病：caliber 让「按并列陈述」，模型就把 4 个等长标识
        # 全列出来，而问句「最长的是哪一个」是单数（F4-04）。并列个数照样作为数据回。
        longest = store.fetch(
            "SELECT t.owner_user_id, CHAR_LENGTH(t.owner_user_id) AS id_length, "
            "COUNT(*) AS task_count "
            f"FROM task t WHERE {clause} AND t.owner_user_id IS NOT NULL AND t.owner_user_id <> '' "
            "GROUP BY t.owner_user_id ORDER BY id_length DESC, t.owner_user_id",
            caliber=(
                f"{store.FORMAL_TASK_CALIBER}；一行一个去重后的标识，task_count 是该标识挂了几个任务；"
                "按标识字符长度倒序、等长按标识定序；"
                "问「最长的是哪一个」（单数）传 top=1 按首行答；"
                "tied_at_top 是与首行等长的标识个数，据它补一句「另有并列」即可，"
                "不要因为存在并列就改写答案行数"
            ),
            limit=bounded,
        )
        id_rows = longest.get("rows") or []
        if id_rows:
            peak_len = id_rows[0].get("id_length")
            tied_ids = store.scalar(
                "SELECT COUNT(*) FROM (SELECT t.owner_user_id FROM task t "
                f"WHERE {clause} AND t.owner_user_id IS NOT NULL AND t.owner_user_id <> '' "
                "AND CHAR_LENGTH(t.owner_user_id) = %(len)s "
                "GROUP BY t.owner_user_id) g",
                {"len": peak_len},
            )
            longest["tied_at_top"] = tied_ids["value"]
            longest["max_id_length"] = peak_len
        return longest

    return _guard("weekly_person_stats", work)


# Fields whose fill-in rate can be asked about (R-07 / R-19). Whitelisted rather
# than interpolated from the argument: the column name reaches SQL as an
# identifier, which no placeholder can bind.
_COMPLETENESS_FIELDS: dict[str, tuple[str, str]] = {
    "overall_goal": ("task", "总体目标"),
    "annual_goals": ("task", "年度目标"),
    "project_owner_name": ("task", "项目负责人"),
    "lead_owner_name": ("task", "分管领导"),
    "project_group": ("task", "项目组"),
    # 姓名列和 ID 列的完整度不是一回事：project_owner_name 128 条全满，
    # project_owner_id 只有 119 条，缺的那 9 条只能从 ID 列看出来。
    "owner_user_id": ("task", "责任人 ID"),
    "project_owner_id": ("task", "项目负责人 ID"),
    "lead_owner_id": ("task", "分管领导 ID"),
    "target_result": ("task_group_detail", "目标成果"),
    "implementation_measure": ("task_group_detail", "实施举措"),
    "progress_effect": ("task_group_detail", "进度成效"),
    "completion_time": ("task_group_detail", "完成时间（文本）"),
}


# Which date column a time window applies to. Whitelisted for the same reason as
# _COMPLETENESS_FIELDS: it reaches SQL as an identifier, not a bound value.
# The two are not interchangeable -- a progress row filed late has a report_time
# months after the progress_date it covers, and E2-04 turns exactly on that gap.
_PROGRESS_DATE_FIELDS: dict[str, str] = {
    "progress_date": "进展周期",
    "report_time": "上报时间",
}

# 空窗提示：进展行按月报，短窗口在本表上恒为 0 行，得换问 task 上的
# latest_progress_time。不写出来，0 行就会被当成「本周没人报」或退化成
# 「最后一批 17 条」——O7-03 的 6 轮正是耗在这个岔口上。
_SHORT_WINDOW_HINT = (
    "；本次窗口内 0 行不是「没人报进展」：进展行按月上报（progress_date 最新一批是 2026-07-31，"
    f"距快照日 {store.AS_OF} 已有半月上下），任何短于半月的窗口在 task_progress 上必然为空。"
    "问「最近一周哪些任务更新了进展」问的是任务上的 latest_progress_time（逐条更新），"
    "请改用 weekly_freshness_distribution recent_days=7（得 23 条）；"
    "也不要退而报「最新一批进展」的 17 条，那是 2026-07-31 那一期的期数，答的是另一个问题"
)

# Bucket expression and ORDER BY for grouped counts. `bucket` is the SELECT alias.
_PROGRESS_GROUPINGS: dict[str, tuple[str, str]] = {
    "month": ("DATE_FORMAT(p.progress_date, '%%Y-%%m')", "bucket"),
    "quarter": ("CONCAT(YEAR(p.progress_date), 'Q', QUARTER(p.progress_date))", "bucket"),
    "task": ("t.task_name", "progress_count DESC, bucket"),
}

# Buckets over task.created_at -- the setup clock, not the reporting clock.
_CREATED_GROUPINGS: dict[str, str] = {
    "month": "DATE_FORMAT(t.created_at, '%%Y-%%m')",
    "year": "YEAR(t.created_at)",
}

_TURNAROUND_SCOPES = ("summary", "board", "slowest", "pending")

# 进展覆盖面的四个口径。unpublished / version_gaps 都是「让模型自己数会数错」
# 的那类：一个要辨两套状态码值，一个要拿最大期号减实际期数。
# unpublished_by_task 再往下一层，落到「哪些任务挂着未发布进展」的逐任务清单。
_COVERAGE_SCOPES = (
    "summary",
    "formal_coverage",
    "publish_split",
    "import_split",
    "unpublished",
    "unpublished_by_task",
    "pending_review",
    "latest_unpublished",
    "same_text",
    "never_reported",
    "version_gaps",
    "latest_round",
    "missing_next",
    "backfill",
    "text_check",
    "orphan_records",
)

# 「最新一期」的定序键：version_no 大者为新，同期再按 id 兜底，两级都要。
# 只按 progress_date 或只按 id 取最新都会取错行（latest_by_wrong_key）：
# 期号与日期并非单调同向，补报的老期号可能有更晚的 progress_date。
# 滞后/活跃分组的两根轴。值是 (分组表达式, 额外 JOIN)。看板要 JOIN 回 task_board
# 才有名字，专项组就在 task 上。两轴都按同一套「滞后 = 从未上报 或 早于窗口」判。
_STALE_AXES: dict[str, tuple[str, str]] = {
    "board": ("b.name", "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "),
    "project_group": ("IFNULL(NULLIF(TRIM(t.project_group),''),'(未填)')", ""),
}

_LATEST_PROGRESS_CTE = (
    "(SELECT p.*, ROW_NUMBER() OVER (PARTITION BY p.task_id "
    "ORDER BY p.version_no DESC, p.id DESC) AS rn "
    "FROM task_progress p WHERE p.is_published = 1)"
)

# text_check 的三类文本规则。文案是合成数据里固定的报数句式（「完成标准草案6项，
# 其中50项已报批，16项进入征求意见阶段」），数字在「项」前后都可能出现。
_DRAFT_RE = re.compile(r"草案(\d+)\s*项")
_REPORT_RE = re.compile(r"(\d+)\s*项已?报批|已?报批(\d+)\s*项")
_CONSULT_RE = re.compile(r"(\d+)\s*项进入征求意见|征求意见(\d+)\s*项")
_AVAIL_RE = re.compile(r"可用性(\d+(?:\.\d+)?)%")
_COORD_RE = re.compile(r"协调|协同|联动|牵头组织")

_TEXT_RULES = ("number_conflict", "availability", "keyword")


def _text_check(
    rule: str,
    keyword: str,
    clause: str,
    task: str = "",
    all_versions: bool = False,
) -> dict[str, Any]:
    """Run one text rule over published progress rounds.

    Default scans each task's LATEST published round (the "当前进展" semantics).
    ``all_versions=True`` scans every published round -- for "历史哪一版出现过
    冲突" questions (e.g. 任务 103 的 V8).  ``task`` narrows to one task by id or
    exact name.
    """
    rule = (rule or "").strip().lower()
    if rule not in _TEXT_RULES:
        return {
            "ok": False,
            "error": {
                "code": "unsupported_rule",
                "message": f"不支持的规则：{rule or '(空)'}；支持 {', '.join(_TEXT_RULES)}",
            },
        }
    latest_cte = (
        _LATEST_PROGRESS_CTE if not all_versions else "(SELECT p.* FROM task_progress p WHERE p.is_published = 1)"
    )
    rn_join = " AND p.rn = 1" if not all_versions else ""
    params: dict[str, Any] = {}
    task_clause = ""
    task_key = task.strip()
    if task_key:
        resolved = store.resolve_task_id(task_key)
        if resolved is None:
            return {
                "ok": False,
                "error": {"code": "task_not_found", "message": f"任务不存在：{task_key}"},
            }
        params["tid"] = resolved
        task_clause = " AND t.id = %(tid)s"
    # 进展正文在两处：技术看板在 task_progress，集团看板在 task_group_progress_history。
    # 只扫 task_progress 会漏掉集团任务的历史版本（任务 103 的 V8 冲突就在集团历史表里）。
    # 两张表各自取（latest 模式各取最新一期；all_versions 模式取全部已发布行），
    # 再合并成同一行集供规则扫描。
    hist_latest = ""
    if not all_versions:
        hist_latest = (
            " AND h.version_no = (SELECT MAX(h2.version_no) FROM task_group_progress_history h2 "
            "WHERE h2.task_id = h.task_id AND h2.is_published = 1)"
        )
    rows = store.all_rows(
        "SELECT t.id AS task_id, t.task_name, p.version_no, p.latest_progress AS progress_text, "
        f"p.next_work FROM task t JOIN {latest_cte} p ON p.task_id = t.id{rn_join} "
        f"WHERE {clause}{task_clause}",
        params,
    ) + store.all_rows(
        "SELECT t.id AS task_id, t.task_name, h.version_no, h.progress_effect AS progress_text, "
        f"'' AS next_work FROM task_group_progress_history h JOIN task t ON t.id = h.task_id "
        f"WHERE {clause} AND h.is_published = 1{hist_latest}{task_clause}",
        params,
    )
    rows.sort(key=lambda r: (r["task_id"], r["version_no"]))
    if rule == "availability":
        hits: list[dict[str, Any]] = []
        for r in rows:
            m = _AVAIL_RE.search(r.get("progress_text") or "")
            if m and float(m.group(1)) < 90:
                hits.append(
                    {
                        "task_id": r["task_id"],
                        "task_name": r["task_name"],
                        "version_no": r["version_no"],
                        "availability_pct": m.group(1),
                    }
                )
        return {
            "ok": True,
            "columns": ["task_id", "task_name", "version_no", "availability_pct"],
            "rows": hits,
            "row_count": len(hits),
            "has_more": False,
            "caliber": (
                f"{store.FORMAL_TASK_CALIBER}；每任务最新一期已发布进展（version_no 倒序）；"
                "从正文正则抽取「可用性 NN%」，列出低于 90% 的；文本抽取≠结构化指标，"
                "只能作待核实清单"
            ),
            "snapshot_note": store.SNAPSHOT_NOTE,
            "snapshot_date": store.AS_OF,
        }
    if rule == "keyword":
        pattern = _COORD_RE
        if keyword.strip():
            pattern = re.compile(re.escape(keyword.strip()))
        hits = [
            {
                "task_id": r["task_id"],
                "task_name": r["task_name"],
                "version_no": r["version_no"],
                "next_work": r.get("next_work") or "",
            }
            for r in rows
            if pattern.search(r.get("next_work") or "")
        ]
        return {
            "ok": True,
            "columns": ["task_id", "task_name", "version_no", "next_work"],
            "rows": hits,
            "row_count": len(hits),
            "has_more": False,
            "caliber": (
                f"{store.FORMAL_TASK_CALIBER}；每任务最新一期已发布进展；"
                f"按 next_work 文本检索 {keyword.strip() or '协调/协同/联动/牵头组织'}；"
                "无结构化协同方/责任边界/承诺日期，只能作待核实线索"
            ),
            "snapshot_note": store.SNAPSHOT_NOTE,
            "snapshot_date": store.AS_OF,
        }
    # number_conflict: 硬冲突 = 报批/征求数大于草案数；阶段和异常 = 草案+报批+征求 > 100。
    hits: list[dict[str, Any]] = []
    for r in rows:
        text = f"{r.get('progress_text') or ''} {r.get('next_work') or ''}"
        d = _DRAFT_RE.findall(text)
        rp = _REPORT_RE.findall(text)
        cs = _CONSULT_RE.findall(text)
        if not d:
            continue
        draft = int(d[0])
        report = int(rp[0][0] or rp[0][1]) if rp else None
        consult = int(cs[0][0] or cs[0][1]) if cs else None
        conflict_type: list[str] = []
        if report is not None and report > draft:
            conflict_type.append("hard_report_gt_draft")
        if consult is not None and consult > draft:
            conflict_type.append("hard_consult_gt_draft")
        total = draft + (report or 0) + (consult or 0)
        if report is not None and consult is not None and total > 100:
            conflict_type.append("sum_anomaly")
        if not conflict_type:
            continue
        hits.append(
            {
                "task_id": r["task_id"],
                "task_name": r["task_name"],
                "version_no": r["version_no"],
                "draft_cnt": draft,
                "report_cnt": report if report is not None else "",
                "consult_cnt": consult if consult is not None else "",
                "conflict_type": ",".join(conflict_type),
                "text_snippet": (r.get("progress_text") or "")[:60],
            }
        )
    hard = sum(1 for h in hits if h["conflict_type"].startswith("hard"))
    sum_anom = sum(1 for h in hits if "sum_anomaly" in h["conflict_type"])
    return {
        "ok": True,
        "columns": [
            "task_id",
            "task_name",
            "version_no",
            "draft_cnt",
            "report_cnt",
            "consult_cnt",
            "conflict_type",
            "text_snippet",
        ],
        "rows": hits,
        "row_count": len(hits),
        "has_more": False,
        "hard_conflict_count": hard,
        "sum_anomaly_count": sum_anom,
        "caliber": (
            f"{store.FORMAL_TASK_CALIBER}；每任务最新一期已发布进展；"
            f"硬冲突 {hard} 项（报批或征求意见数大于草案总数），阶段数量之和超 100 的 {sum_anom} 项"
        ),
        "snapshot_note": store.SNAPSHOT_NOTE,
        "snapshot_date": store.AS_OF,
    }


# 动作日志的聚合口径。日志 1578 条而清单封顶 200 行，所以「各节点各动作多少条」
# 和「人均多少次动作」都必须服务端算完再回，模型翻明细自己数必然只数到第一页。
_WORKFLOW_SCOPES = (
    "",
    "by_node_action",
    "actions_per_task",
    "recent",
    "by_node",
    "by_operator",
    "log_span",
    "opinion_count",
)

# 提交单的附加口径。空串走通用清单；两个 external 口径专门回答 O2OA 外部标识
# （o2_process_id / o2_work_id / o2_task_id）填得全不全，这三列此前没有任何
# 工具能取到，模型只能答「取不到」。
_SUBMISSION_SCOPES = (
    "",
    "by_kind",
    "by_status",
    "table_total",
    "inflight_count",
    "inflight_by_board",
    "inflight_by_kind",
    "inflight_multi",
    "latest_status",
    "payload_key_combos",
    "payload_keys_by_board",
    "payload_absent",
    "payload_missing_progress_key",
    "rejected_by_board",
    "sign_summary",
    "by_signer",
    "sign_turnaround",
    "rounds_per_task",
    "published_vs_progress",
    "external_ids",
    "inflight_external",
)

# 「在途」的成员必须列举，不能写成 status <> 'published'：cancelled 那 1 张单
# 既不是已发布也不在途，用取反会把它算进来（60 vs 59，negation_includes_cancelled）。
_SUBMISSION_INFLIGHT = ("pending_fill", "signing", "pending_audit", "pending_leader", "rejected")

# 规模/完整度/进展强度三种横截面。共用一套分组轴，区别只在度量：
#   totals       —— 任务数与各子表条数（全部 COUNT(DISTINCT)，见下）
#   completeness —— 有目标/有里程碑/有进展的任务数（SUM(EXISTS ...)）
#   intensity    —— 进展行数与人均期数（分母是任务数，不是行数）
_SCALE_MODES = ("totals", "completeness", "intensity")

# 规模横截面的分组轴。值是 (分组表达式, 分组定序键, 额外 JOIN)。
_SCALE_AXES: dict[str, tuple[str, str, str]] = {
    # 排序列写成 MIN(b.sort_order)：这里按 b.name 分组，裸 b.sort_order 既不在
    # GROUP BY 里也不是聚合，only_full_group_by 会直接报 1055。
    "board": ("b.name", "MIN(b.sort_order)", "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0"),
    "project_group": (
        "IFNULL(NULLIF(TRIM(t.project_group),''),'(未填)')",
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

# 排名口径的三种语义。哪一种都不能由模型按返回行自己拿主意：
#   cut       ——「前 N 条」，并列也要按定序硬切到 N 条；
#   keep_ties ——「并列的都要列出来」，第 N 名有几个并列就返回几行；
#   per_group ——「每组各自的第一名」，一组一行，组内并列按定序取第一。
# 同一份数据在三种语义下行数不同（进展期数前 3 名：cut=3、keep_ties=12），
# 让模型自己在 200 行明细上判断，多返回和少返回都会被判集合不一致。
# 另有两档不是名次而是分布，问「中位数」「分成四档」时用它们：
#   distribution ——五数概括（Q1/中位数/Q3/极值/均值），一行；
#   quartiles    ——NTILE(4) 等量四档，每档任务数与期数区间。
_RANK_MODES = ("cut", "keep_ties", "per_group", "distribution", "quartiles")

# 可排名的度量。每项给出：子表、子表自身闸门、计数表达式、中文标签。
# 全部走 LEFT JOIN，零值行才不会被 INNER JOIN 静默丢掉（inner_join_drops_zero）。
_RANK_METRICS: dict[str, tuple[str, str, str, str]] = {
    "progress_rounds": ("task_progress", "x.is_published = 1", "COUNT(x.id)", "已发布进展期数"),
    "milestones": ("task_milestone", "x.is_deleted = 0", "COUNT(x.id)", "里程碑数"),
    "milestones_done": ("task_milestone", "x.is_deleted = 0", "SUM(x.status = 1)", "已完成里程碑数"),
    "attachments": ("task_attachment", "x.is_deleted = 0", "COUNT(x.id)", "附件数"),
    "submissions": ("task_workflow_submission", "1 = 1", "COUNT(x.id)", "审批提交单数"),
    "group_rounds": ("task_group_progress_history", "x.is_published = 1", "COUNT(x.id)", "集团看板成效期数"),
    # 唯一不 JOIN 子表的度量：值就在 task 行上，故 table 留空、gate 恒真。
    "project_team_size": ("", "", "_TEAM_SIZE", "项目团队人数"),
}

# 项目团队人数：数 task 行上 project_owner_name 的分隔符个数 + 1，不 JOIN 子表。
# 三种分隔符都要数（、／,／；），只数顿号会把另两种写法算成 1 人。
# 与集团明细的多值列 project_owner_names 是两个列：那一列在 task_group_detail 上、
# 只覆盖集团看板 46 条，本列在 task 行上、覆盖两个看板 128 条，两个「人数」答案
# 必须能分开——问「哪个任务的项目团队人数最多」问的是本列。
_TEAM_SIZE_EXPR = (
    "CHAR_LENGTH(t.project_owner_name) - CHAR_LENGTH("
    "REPLACE(REPLACE(REPLACE(t.project_owner_name, '、', ''), ',', ''), '；', '')) + 1"
)

# per_group 的分组轴。键是对外口径名，值是 (分组表达式, 分组定序键)。
# 定序键进 ROW_NUMBER 的 ORDER BY 尾部，保证组内并列的裁决可复现。
_RANK_GROUPINGS: dict[str, tuple[str, str]] = {
    "project_group": ("t.project_group", "t.project_group"),
    "board": ("b.name", "b.sort_order"),
    "primary_category": ("pc.name", "pc.id"),
    "status": ("t.status", "t.status"),
}

# Columns of task_group_detail a caller may select or filter on. Whitelisted for
# the same reason as _COMPLETENESS_FIELDS: these reach SQL as identifiers.
_GROUP_DETAIL_FIELDS: dict[str, str] = {
    "target_result": "目标成果",
    "implementation_measure": "实施举措",
    "completion_time": "完成时间（展示文本，不可做日期运算）",
    "progress_effect": "进度成效当前正文",
    "lead_owner_names": "牵头人姓名（多值，逗号分隔）",
    "lead_owner_ids": "牵头人 ID（多值，逗号分隔）",
    "project_owner_names": "项目负责人姓名（多值，逗号分隔）",
    "project_owner_ids": "项目负责人 ID（多值，逗号分隔）",
    "project_group": "项目组",
}

# Which multi-value owner column a person lookup searches. The two are distinct
# roles: 唐立本 leads 5 group tasks but is project owner on 3, and collapsing
# them answers a different question (the single_vs_multi_owner_column trap).
_GROUP_OWNER_ROLES: dict[str, tuple[str, str]] = {
    "lead": ("lead_owner_ids", "lead_owner_names"),
    "project": ("project_owner_ids", "project_owner_names"),
}

_GROUP_STATS_SCOPES = (
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

# completion_time 是展示文本（R-12），能算日期的只有两种写法：标准日期原样用，
# 季度取该季末日。其余 34 条是「2026年底前」「持续推进」这类自由文本，没有
# 可比的日子，归一化后为 NULL——不能猜成 12-31，那是替业务下判断。
_COMPLETION_DEADLINE = (
    "CASE "
    "WHEN d.completion_time REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN d.completion_time "
    "WHEN d.completion_time REGEXP '^[0-9]{4}Q[1-4]$' THEN CONCAT(LEFT(d.completion_time, 4), '-', "
    "ELT(CAST(SUBSTRING(d.completion_time, 6, 1) AS UNSIGNED), '03-31', '06-30', '09-30', '12-31')) "
    "END"
)

# 完成时间的「写法」分档。判别顺序即优先级，不能重排：'2026年6月底' 同时命中
# 「含底」与「中文年月」，先判到哪档就算哪档，调换后两档会把 11 拆成 6+5。
# 标准日期与季度用 REGEXP 锚定全串，避免 '2026Q3前' 之类被算成规范写法。
_COMPLETION_TIME_FORMAT_CASE = (
    "CASE "
    "WHEN d.completion_time REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN '标准日期 YYYY-MM-DD' "
    "WHEN d.completion_time REGEXP '^[0-9]{4}Q[1-4]$' THEN '季度 YYYYQn' "
    "WHEN d.completion_time LIKE '%%底%%' THEN '模糊表述（含“底”）' "
    "WHEN d.completion_time LIKE '%%年%%月%%日%%' THEN '中文年月日' "
    "WHEN d.completion_time LIKE '%%年%%月%%' THEN '中文年月' "
    "ELSE '其他' END"
)

# Buckets over task_group_progress_history.report_time -- the group board keeps
# its progress in its own table, so the _PROGRESS_GROUPINGS over task_progress
# cannot see any of it (R7-02: group_task_progress = 0, group_history = 362).
_GROUP_HISTORY_GROUPINGS: dict[str, tuple[str, str]] = {
    "year": ("YEAR(h.report_time)", "bucket"),
    "month": ("DATE_FORMAT(h.report_time, '%%Y-%%m')", "bucket"),
    "quarter": ("CONCAT(YEAR(h.report_time), 'Q', QUARTER(h.report_time))", "bucket"),
    # 并列按 task id 升序，不按任务名：11 期的有 8 条并列，问「前 5 条」时按名排
    # 与按 id 排是两个不同集合（按名排把 133 号顶进前 5、把 115 号挤出去）。
    # 名次题的定序键要与其他榜单一致，一律 task id。
    "task": ("t.task_name", "progress_count DESC, t.id"),
    "reporter": ("h.reporter_id", "progress_count DESC, bucket"),
    # 滞报榜按任务给最后一次上报距快照日的天数。放进分组白名单而不是单独开
    # 工具：问的仍是这张历史表的分面，只是聚合出的是 lag 而非条数。
    "lag": ("t.id", "lag_days DESC, bucket"),
    # 与提交单的挂接率。同样是这张表的分面，聚合出的是填充数而非条数。
    "linkage": ("t.id", "bucket"),
}

_YEAR_GOAL_SCOPES = ("by_year", "coverage", "missing", "missing_by_group", "span", "multi_year")

# Milestone breakdown dimensions. Whitelisted because they reach SQL as
# identifiers; the labels double as the caliber text.
_MILESTONE_DIMENSIONS: dict[str, str] = {
    "year": "年度",
    "category": "类别",
    "group_name": "承担组",
    "status": "完成状态",
    "task_status": "任务状态",
    # 任务分类树的一级。与 category（里程碑自己的类别文本）是两个维度，别混。
    "primary_category": "任务一级分类",
    # 任务的项目组。与 group_name（里程碑行自己的承担组短名）是两个轴，名字像但取值
    # 集合都不一样：group_name 是 区域组/安全组/技术组… 六个短名，project_group 是
    # 关键技术攻关组/算力网络组/国家工程办… 十一个项目组。问「哪些项目组的里程碑
    # 完成比例高/低」问的是后者，此前没有这一维，只能拿 group_name 顶上去答错。
    "project_group": "项目组",
    # 填报人。此前没有这一维，问「里程碑都是谁报的、各几条」只能答不可答。
    "reporter_id": "填报人",
    "owner_id": "责任人",
    # 看板。里程碑行上没有 board_id，看板在任务上，要 JOIN 回 task_board。
    # 问「技术组和集团组的里程碑分别怎样」此前没有出口，只能答不可答。
    "board": "看板",
}

_MILESTONE_STATS_SCOPES = ("summary", "by_dimension", "deleted", "fully_deleted", "per_task", "mismatch")

_MILESTONE_MISMATCH_KINDS = ("task_done_milestones_open", "milestones_done_task_open")


@mcp.tool()
def weekly_field_completeness(field: str = "", list_missing: bool = False, limit: int = 200) -> str:
    """Count how many formal tasks have a given field filled in (R-07 / R-19).

    Answers "how many tasks have an overall goal / a named project owner" with one
    call. Without this the only route is fetching every task and counting by hand,
    which burns the tool-call budget and tends to run out mid-answer.

    Args:
        field: Column to measure; empty lists the supported columns.
        list_missing: Return the rows that are missing the field, not just counts.
        limit: Row cap for ``list_missing``.
    """

    def work() -> dict[str, Any]:
        token = (field or "").strip()
        if not token:
            return {
                "ok": True,
                "supported_fields": {
                    name: {"table": table, "label": label}
                    for name, (table, label) in sorted(_COMPLETENESS_FIELDS.items())
                },
                "caliber": "传入 field 以统计该字段的填报完整度",
                "snapshot_note": store.SNAPSHOT_NOTE,
            }
        if token not in _COMPLETENESS_FIELDS:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_field",
                    "message": f"不支持的字段：{token}；支持 {', '.join(sorted(_COMPLETENESS_FIELDS))}",
                },
            }
        table, label = _COMPLETENESS_FIELDS[token]
        clause = store.formal_task_clause()

        if list_missing:
            # 「哪些任务没填」需要的是清单，不是占比。计数问不出是哪 9 条。
            # LEFT JOIN 保留无明细行的任务，它们也算缺项（R-08）。
            join = "" if table == "task" else f"LEFT JOIN {table} d ON d.task_id = t.id "
            col = f"t.{token}" if table == "task" else f"d.{token}"
            gap = f"({col} IS NULL OR {col} = '')"
            missing_rows = store.fetch(
                "SELECT t.id, t.task_name, t.owner_user_id, t.project_owner_id, "
                "t.project_owner_name, t.lead_owner_name "
                f"FROM task t {join}WHERE {clause} AND {gap} ORDER BY t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；列出「{label}」为空的正式任务（R-07/R-19）；"
                    "空字符串按未填计入；此清单即全部缺项，按 total_count 逐条列全"
                ),
                limit=limit,
            )
            total = store.scalar(f"SELECT COUNT(*) AS n FROM task t {join}WHERE {clause} AND {gap}")
            missing_rows["total_count"] = total.get("value")
            missing_rows["field"] = token
            missing_rows["field_label"] = label
            return missing_rows

        # 完整率必须由服务端算：问「完整率是多少」时模型手算 filled / total 会
        # 连百分号带小数位一起自己拿主意，答出 100% 这种与 119/128 相矛盾的数。
        if table == "task":
            expr = f"t.{token} IS NOT NULL AND t.{token} <> ''"
            sql = (
                f"SELECT COUNT(*) AS total, SUM({expr}) AS filled, "
                f"SUM(t.{token} IS NULL OR t.{token} = '') AS missing, "
                f"ROUND(SUM({expr}) / COUNT(*) * 100, 1) AS filled_pct "
                f"FROM task t WHERE {clause}"
            )
        else:
            # LEFT JOIN so tasks with no detail row count as missing, not vanish (R-08).
            expr = f"d.{token} IS NOT NULL AND d.{token} <> ''"
            sql = (
                f"SELECT COUNT(*) AS total, SUM({expr}) AS filled, "
                f"SUM(d.{token} IS NULL OR d.{token} = '') AS missing, "
                f"ROUND(SUM({expr}) / COUNT(*) * 100, 1) AS filled_pct "
                f"FROM task t LEFT JOIN {table} d ON d.task_id = t.id WHERE {clause}"
            )
        # 「这个字段可信吗」问的不是填报率。集团组实施举措 55 行全部非空、填写率
        # 100%，但 55 行是同一句话复制的——只报填写率会推出「字段没问题」，与真相
        # 相反。区分度必须一起给：distinct_values 是非空值里的不同值个数，
        # top_value_rows 是最高频那个值占了多少行。两者落在服务端，模型没法自己
        # 从占比里反推出来。
        if table == "task":
            scope_from = "FROM task t"
            scope_where = f"WHERE {clause} AND t.{token} IS NOT NULL AND t.{token} <> ''"
            scope_col = f"t.{token}"
        else:
            scope_from = f"FROM task t JOIN {table} d ON d.task_id = t.id"
            scope_where = f"WHERE {clause} AND d.{token} IS NOT NULL AND d.{token} <> ''"
            scope_col = f"d.{token}"
        distinct_gated = store.scalar(f"SELECT COUNT(DISTINCT {scope_col}) AS n {scope_from} {scope_where}")
        top_gated = store.scalar(
            f"SELECT COUNT(*) AS n {scope_from} {scope_where} GROUP BY {scope_col} ORDER BY n DESC LIMIT 1"
        )

        quality: list[str] = []
        # 明细表字段还要给裸表口径：E04 的金标依据是「55 条集团组当前明细」，那是
        # task_group_detail 裸行数；filled_pct 的分母是 128 条正式任务（R-08 的
        # LEFT JOIN 把无明细行的任务算成缺项）。两个分母都对，各答各的问题，
        # 差在中间那 9 行挂在已软删或未发布的任务上。不写明分母，问字段质量的
        # 会拿 128 当分母，问业务结论的会拿 55 当分母，两边都答偏。
        raw_distinct = distinct_gated
        if table != "task":
            raw_rows = store.scalar(f"SELECT COUNT(*) AS n FROM {table} d")
            raw_filled = store.scalar(
                f"SELECT COUNT(*) AS n FROM {table} d WHERE d.{token} IS NOT NULL AND d.{token} <> ''"
            )
            raw_distinct = store.scalar(
                f"SELECT COUNT(DISTINCT d.{token}) AS n FROM {table} d WHERE d.{token} IS NOT NULL AND d.{token} <> ''"
            )
            quality.append(
                f"另给 {table} 裸表口径（不加任务闸门）：raw_row_count 共 "
                f"{raw_rows['value']} 行、其中非空 {raw_filled['value']} 行、"
                f"不同值 {raw_distinct['value']} 个；"
                "问「这个字段本身可信吗／填得怎么样」看裸表这一档（明细表有多少行就是多少行），"
                "问「有多少任务填了」看上面过闸的 filled / total（分母是正式任务，"
                "R-08 保留无明细行的任务）；两档不要混着引用"
            )

        # 信号按裸表那一档判：明细表字段的区分度是表本身的属性，过闸只是少看了
        # 9 行，不该让「同一句话复制 55 遍」因为闸门把行数减到 46 就不再报警。
        signal_distinct = int(raw_distinct["value"] or 0)
        if signal_distinct <= 1:
            quality.append(
                f"字段质量信号：非空行里只有 {signal_distinct} 个不同的值，"
                "即所有行填的是同一份内容——填写率再高也不具备区分度，"
                "不能拿它做差异化归纳（比较各组各任务的举措有何不同），"
                "应回查生成逻辑或源数据；这是规则校验信号，不构成对项目或人员的绩效判断，"
                "需业务责任人核实后才能进正式结论"
            )
        elif signal_distinct and top_gated["value"]:
            quality.append(
                f"字段质量信号：非空行里有 {signal_distinct} 个不同的值，"
                f"最高频的那个值占 {top_gated['value']} 行；不同值远少于行数时"
                "说明内容高度重复，做差异化归纳前先核实源数据"
            )

        result = store.fetch(
            sql,
            caliber="；".join(
                [
                    f"{store.FORMAL_TASK_CALIBER}；统计「{label}」非空占比（R-07/R-19）；"
                    "空字符串按未填计入 missing；"
                    "filled_pct 已按 total 算好（保留一位小数），直接引用，不要自己拿 filled / total 重算"
                    + ("；LEFT JOIN 保留无明细行的任务（R-08）" if table != "task" else ""),
                    "distinct_values 是非空值里的不同值个数，top_value_rows 是最高频值占的行数，"
                    "两者已算好，问「字段是否可信／有没有区分度」看它们，不要只看 filled_pct",
                    *quality,
                ]
            ),
            limit=1,
        )
        result["field"] = token
        result["field_label"] = label
        result["distinct_values"] = distinct_gated["value"]
        result["top_value_rows"] = top_gated["value"]
        if table != "task":
            result["raw_row_count"] = raw_rows["value"]
            result["raw_filled"] = raw_filled["value"]
            result["raw_distinct_values"] = raw_distinct["value"]
        return result

    return _guard("weekly_field_completeness", work)


@mcp.tool()
def weekly_progress_range(
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    by: str = "",
    date_field: str = "progress_date",
    peak: bool = False,
    limit: int = 200,
    ctx: Context | None = None,
) -> str:
    """Query or count published progress across a time window, over ALL tasks.

    This is the time-axis entry point. Without it the only way to answer "how
    many progress reports in the last 30 days" is to walk every task's history
    one call at a time, which exhausts the tool budget before an answer exists.

    Relative windows are measured from the data snapshot date, not from the
    current clock -- see ``_store.AS_OF``.

    Args:
        date_from: Inclusive start, YYYY-MM-DD. Empty means unbounded.
        date_to: Inclusive end, YYYY-MM-DD. Empty means unbounded.
        last_days: Window of N days ending at the snapshot date. Overrides date_from.
        by: Empty lists rows; ``month`` / ``quarter`` / ``task`` returns counts per group.
        date_field: ``progress_date`` (the period reported on) or ``report_time``
            (when it was submitted). These differ for late filings.
        peak: True returns only the highest-count bucket. The ordering and the
            tie-break run server-side, so the single row is the answer.
        limit: Max rows, capped at 200.
    """
    may_read = _caller_may_read_sensitive(ctx)

    def work() -> dict[str, Any]:
        if date_field not in _PROGRESS_DATE_FIELDS:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_field",
                    "message": f"不支持的 date_field：{date_field}；支持 {', '.join(sorted(_PROGRESS_DATE_FIELDS))}",
                },
            }
        grouping = (by or "").strip().lower()
        if grouping and grouping not in _PROGRESS_GROUPINGS:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_group_by",
                    "message": f"不支持的 by：{by}；支持 {', '.join(sorted(_PROGRESS_GROUPINGS))}",
                },
            }

        column = f"p.{date_field}"
        lo, hi = store.date_window(date_from, date_to, last_days or None)
        params: dict[str, Any] = {}
        where = f"{store.formal_task_clause()} AND p.is_published = 1"
        window = store.window_clause(column, lo, hi, params)
        if window:
            where += f" AND {window}"

        field_label = _PROGRESS_DATE_FIELDS[date_field]
        caliber = (
            f"{store.FORMAL_TASK_CALIBER}；仅正式发布进展（is_published = 1）；"
            f"{store.window_caliber(lo, hi, label=field_label)}；{store.as_of_caliber()}"
        )
        # 进展行是按月上报的（progress_date 最新一批是 2026-07-31，距快照日 15 天），
        # 所以任何短于半月的窗口在本表上必然是 0 行。这个 0 是真的，但它答不了
        # 「最近一周哪些任务更新了进展」——那问的是任务上的 latest_progress_time
        # （近 7 天 23 条）。不点明这层，看到 0 行只会退成「最后一批」17 条，
        # O7-03 那 6 轮就是这么耗掉的。
        short_window = bool(lo and hi and (_days_between(lo, hi) < 15))

        if not grouping:
            # Counts must survive truncation: "今年以来报了多少期" is 366 rows, well
            # past MAX_ROWS, and a caller seeing 200 + has_more cannot recover the
            # real total. Both totals are reported because E1 asks for each
            # separately (rows = 期数, tasks = 更新过进展的任务数).
            totals = store.fetch(
                "SELECT COUNT(*) AS total_rows, COUNT(DISTINCT p.task_id) AS total_tasks "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {where}",
                params,
                caliber=caliber,
                limit=1,
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, p.version_no, p.progress_date, "
                "p.report_time, DATEDIFF(p.report_time, p.progress_date) AS lag_days "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {where} ORDER BY p.{date_field} DESC, t.id",
                params,
                caliber=caliber + "；lag_days>0 表示补报更早周期",
                can_read_sensitive=may_read,
                limit=limit,
            )
            first = totals["rows"][0] if totals["rows"] else {}
            rows["total_count"] = first.get("total_rows")
            rows["total_tasks"] = first.get("total_tasks")
            if not rows["total_count"] and short_window:
                rows["caliber"] += _SHORT_WINDOW_HINT
            return rows

        expression, order = _PROGRESS_GROUPINGS[grouping]
        select = f"{expression} AS bucket, COUNT(*) AS progress_count"
        if grouping != "task":
            select += ", COUNT(DISTINCT p.task_id) AS task_count"
        group_order = order
        note = f"；按 {grouping} 分组计数"
        tail = ""
        if grouping in {"month", "quarter"} and not peak:
            # 环比要的是相邻两档并排。只给各月计数,模型自己错位相减会把上一档
            # 记错(K3-03 曾把 2026-06 的 48 说成 61)。prev_count / change 由服务端
            # 用 LAG 按时序算好,首档 change 为 NULL 是对的:没有上一档可比。
            return _progress_mom(store, select, where, params, caliber + note, grouping, limit)
        if peak:
            # 「哪个月最高」的裁决落在服务端。按 bucket 时序返回 19 行让模型自己
            # 挑最大值，它会把 2026-02(61) 和名次靠后的月份看成并列。
            group_order = "progress_count DESC, bucket"
            # LIMIT 落在 SQL 里而不是靠 limit 参数截断：靠截断会带出
            # has_more = True，看着像「还有行没给」，与「首行即答案」相冲。
            tail = " LIMIT 1"
            note += "；已按计数降序、并列取 bucket 升序，首行即峰值，勿另行比较"
        grouped = store.fetch(
            f"SELECT {select} FROM task_progress p JOIN task t ON t.id = p.task_id "
            f"WHERE {where} GROUP BY bucket ORDER BY {group_order}{tail}",
            params,
            caliber=caliber + note,
            limit=limit,
        )
        if not grouped.get("row_count") and short_window:
            grouped["caliber"] += _SHORT_WINDOW_HINT
        return grouped

    return _guard("weekly_progress_range", work)


@mcp.tool()
def weekly_progress_coverage(
    scope: str = "summary",
    project_group: str = "",
    limit: int = 200,
    rule: str = "",
    keyword: str = "",
    task: str = "",
    all_versions: bool = False,
) -> str:
    """Summarise how far back published progress goes and how much it covers.

    Args:
        scope: ``summary`` row count, tasks covered, date span, max version_no.
            ``publish_split`` gives published / unpublished / total in one row
            (943 / 123 / 1066) -- summary carries only the published side and
            ``unpublished`` only the other, and adding them up by hand tends to
            drop the task gate, which reads 945 / 1068 instead.
            ``import_split`` splits published progress into imported vs
            hand-entered by ``import_id IS NULL`` (943 / 943 / 0). Nothing else
            exposes that column, so "how many periods were typed in by hand"
            otherwise reads as unanswerable when the answer is a plain 0.
            ``unpublished`` splits unpublished progress by its OWN approval code
            (0 草稿 / 1 待审核 / 2 驳回 / 3 通过) -- a different vocabulary from the
            task's workflow_status -- and gives each bucket's row count plus its
            distinct task count, since "多少条进展、涉及多少任务" wants both (驳回
            is 39 rows over 33 tasks). ``unpublished_by_task`` lists the tasks that
            hold unpublished periods while their submission is already published,
            most periods first. ``pending_review`` lists the 待审核 periods newest
            first with the task's publicly visible version alongside, for "存在
            待审核进展但对外还是上一期". ``never_reported`` lists the 55 formal tasks
            with no published row in task_progress at all -- judged by NOT EXISTS,
            not by ``latest_progress_time IS NULL``, which only finds 9 of them.
            ``version_gaps`` tasks missing a period.
            ``latest_round`` gives each task's NEWEST published period with its
            ``next_work`` -- use it for "下一步打算做什么", never the full history
            (a task with 19 periods would otherwise contribute 19 rows and its
            oldest plan reads as current). ``missing_next`` counts tasks whose
            newest period left 下一步 blank.
            ``text_check`` runs a text rule over each task's latest published
            round -- ``rule=number_conflict`` finds 报批/征求意见数大于草案数
            (plus 阶段数量之和超 100), ``rule=availability`` lists 可用性低于
            90% 的, ``rule=keyword`` searches next_work for 协调/协同/联动/
            牵头组织 (or a custom ``keyword``).  These are the 规则信号题的
            服务端出口: 文本数字冲突 / 可用性抽取 / 跨部门协调线索.
        project_group: Narrow to one 项目组, for "算力网络组各任务下一步做什么".
        limit: Max rows for the listing scopes, capped at 200.
        rule: For scope=text_check: number_conflict / availability / keyword.
        keyword: For scope=text_check with rule=keyword: custom search term.
    """

    def work() -> dict[str, Any]:
        key = (scope or "summary").strip().lower()
        if key not in _COVERAGE_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(_COVERAGE_SCOPES)}",
                },
            }
        bounded = max(1, min(store.MAX_ROWS, int(limit)))
        clause = store.formal_task_clause()

        if key in ("latest_round", "missing_next"):
            params: dict[str, Any] = {}
            where = [clause]
            caliber = [
                store.FORMAL_TASK_CALIBER,
                "每任务只取最新一期已发布进展（version_no 倒序、同期按 id 兜底）；"
                "不是全部历史，也不是按 progress_date 取最新（补报的老期号可能日期更晚）",
            ]
            if project_group.strip():
                params["pg"] = project_group.strip()
                where.append("TRIM(t.project_group) = %(pg)s")
                caliber.append(f"仅项目组 = {project_group.strip()}")
            latest_where = " AND ".join(where)

            if key == "missing_next":
                return store.fetch(
                    "SELECT COUNT(*) AS tasks_missing_next "
                    f"FROM task t JOIN {_LATEST_PROGRESS_CTE} p ON p.task_id = t.id AND p.rn = 1 "
                    f"WHERE {latest_where} AND (p.next_work IS NULL OR p.next_work = '')",
                    params,
                    caliber="；".join([*caliber, "只看最新一期是否留空，中间某期空着不算"]),
                    limit=1,
                )

            total = store.scalar(
                f"SELECT COUNT(*) FROM task t JOIN {_LATEST_PROGRESS_CTE} p ON p.task_id = t.id AND p.rn = 1 "
                f"WHERE {latest_where} AND p.next_work IS NOT NULL AND p.next_work <> ''",
                params,
            )
            # latest_progress 与 next_work 同排返回：问「这一期各任务报了什么」要的是
            # 正文，此前本档只给 next_work（下一步），正文得回 weekly_progress_history
            # 逐任务查 73 次。注意本档的 next_work 非空闸门对正文题是错闸门——
            # 今天 73/73 都非空所以无害，问正文时要留意这一点。
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, t.project_group, p.version_no, "
                "p.latest_progress, p.next_work, p.progress_date "
                f"FROM task t JOIN {_LATEST_PROGRESS_CTE} p ON p.task_id = t.id AND p.rn = 1 "
                f"WHERE {latest_where} AND p.next_work IS NOT NULL AND p.next_work <> '' "
                "ORDER BY t.id",
                params,
                caliber="；".join(
                    [
                        *caliber,
                        "仅列出最新一期写了下一步的任务；一任务一行，total_count 即任务数",
                        "下一步为空的任务数另用 scope=missing_next",
                    ]
                ),
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            return rows

        if key == "formal_coverage":
            # 「正式周报覆盖率」：两张正式进展表各算各的（技术组 task_progress、
            # 集团组 task_group_progress_history），并集 = 有正式进展的任务数。
            # 单独看任何一张表都会漏掉另一看板（summary 的 tasks_covered 73 只含
            # 技术组；集团组 46 条成效写在历史表）；128 - 9 = 119 的两张表都没有
            # 的口径已在 never_reported 里，这里直接给并集与覆盖率。
            covered = store.scalar(
                f"SELECT COUNT(*) FROM task t WHERE {clause} AND ("
                "EXISTS (SELECT 1 FROM task_progress p WHERE p.task_id = t.id AND p.is_published = 1) "
                "OR EXISTS (SELECT 1 FROM task_group_progress_history h "
                "WHERE h.task_id = t.id AND h.is_published = 1))",
            )
            total = store.scalar(f"SELECT COUNT(*) FROM task t WHERE {clause}")
            return {
                "ok": True,
                "columns": ["formal_task_count", "tasks_with_progress", "coverage_pct"],
                "rows": [
                    {
                        "formal_task_count": total["value"],
                        "tasks_with_progress": covered["value"],
                        "coverage_pct": round(covered["value"] / total["value"] * 100, 1),
                    }
                ],
                "row_count": 1,
                "has_more": False,
                "caliber": (
                    f"{store.FORMAL_TASK_CALIBER}；覆盖率 = 两张正式进展表（技术组 "
                    "task_progress / 集团组 task_group_progress_history）并集的任务数 "
                    "除以正式任务总数；任何一张表的单独口径都会漏掉另一看板，"
                    "不要用 scope=summary 的 tasks_covered 代答"
                ),
                "snapshot_note": store.SNAPSHOT_NOTE,
            }

        if key == "text_check":
            # 规则信号题的服务端出口：数字冲突/可用性/关键词三类文本规则检查。
            # 此前这些题让模型自己从正文里数——要么 max_rounds，要么方向不符；
            # 正则与判定固化在服务端，模型只需调一次并照抄结果。
            # 默认扫描每任务最新一期已发布进展；all_versions=True 扫全部版本
            # （问「历史哪一版出过冲突」时用），task= 可限定单任务。
            return _text_check(rule, keyword, clause, task, all_versions)

        if key == "orphan_records":
            # 「数据有没有孤儿记录」：进展行挂不到任务、审批动作挂不到提交单。
            # 两张表各自 NOT EXISTS 判，一次给全（各 2 条）；模型翻附件表/导入批次
            # 去答会答成另一类孤儿（G-E03 曾指到附件孤儿上）。
            return store.fetch(
                "SELECT "
                "(SELECT COUNT(*) FROM task_progress p LEFT JOIN task t ON t.id = p.task_id "
                "WHERE t.id IS NULL) AS orphan_progress_rows, "
                "(SELECT COUNT(*) FROM task_workflow_action a "
                "LEFT JOIN task_workflow_submission s ON s.id = a.submission_id "
                "WHERE s.id IS NULL) AS orphan_actions ",
                caliber=(
                    "孤儿 = 外键指向查不到的记录（NOT EXISTS 语义）：进展行无对应任务、"
                    "审批动作无对应提交单；外键为空是「没走那条路」不算孤儿。"
                    "当前 2 条孤儿进展行 + 2 条孤儿审批动作"
                ),
                limit=1,
            )

        if key == "backfill":
            # 「补报」= 期次小的那一期反而提交得更晚，判据是相邻两期的 report_time
            # 逆序（p.version_no + 1 = n.version_no 且 p.report_time > n.report_time）。
            # 此前没有任何出口能取到它：progress_range 的 lag_days 是「上报日减周期日」
            # ——同一行内的滞后，跟相邻两期谁先谁后是两件事，用它答会答成另一题。
            # 两期都要过发布闸门，否则草稿期会混进来充当「更晚的那一期」。
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, p.version_no AS late_filed_version, "
                "p.report_time, n.version_no AS next_version, n.report_time AS next_report_time, "
                "DATEDIFF(p.report_time, n.report_time) AS filed_later_by_days "
                "FROM task_progress p "
                "JOIN task_progress n ON n.task_id = p.task_id "
                "AND n.version_no = p.version_no + 1 AND n.is_published = 1 "
                "JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 1 AND p.report_time > n.report_time "
                "ORDER BY t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅已发布进展（两期都要 is_published = 1）；"
                    "判据是相邻两期上报时间逆序：期号 late_filed_version 的上报时间"
                    "晚于它的下一期 next_version，即这一期是事后补报的；"
                    "filed_later_by_days 是晚了多少天，由服务端算好；"
                    "一对相邻期一行，行数即补报次数；"
                    "这与 weekly_progress_range 的 lag_days（同一行内「上报日 - 周期日」）"
                    "不是一个口径，那个答不了「哪期反而报得更晚」"
                ),
                limit=bounded,
            )

        if key == "publish_split":
            # 「多少已发布、多少还没发布」要的是一行三个数。summary 只给已发布那
            # 一侧（943），unpublished 只给未发布那一侧（123），两处分别取再自己
            # 相加，模型多半会漏掉任务闸门：不加 t.workflow_status = 'published'
            # 就是 945/1068，正是基线答错的那组数。三个数一次给全，闸门写进口径。
            return store.fetch(
                "SELECT SUM(p.is_published = 1) AS published, SUM(p.is_published = 0) AS unpublished, "
                "COUNT(*) AS total, COUNT(DISTINCT p.task_id) AS tasks "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause}",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；进展行按 is_published 一分为二，"
                    "published 943 + unpublished 123 = total 1066，三个数同一次查询同一套闸门；"
                    "闸门是任务侧的（任务未删除且 workflow_status = 'published'），"
                    "去掉它得 945/123/1068——多出的 2 行挂在非正式任务上，"
                    "答「进展记录里」仍要按正式任务口径；"
                    "published 943 与 scope=summary 的 progress_rows 同源，"
                    "unpublished 123 与 scope=unpublished 的 total_count 同源"
                ),
                limit=1,
            )

        if key == "import_split":
            # 「多少条是手工填的、不是导入的」——判据是 task_progress.import_id
            # 是否为空，此前没有任何工具暴露这一列的分布，模型只能答「无法精确
            # 统计」（Q5-04 就是这么失分的）。两套闸门的答案差得很远，必须同时
            # 给全：正式发布口径 943 全部来自导入、手工 0；把 is_published = 1
            # 去掉则是 1066 行里 948 导入、118 手工——那 118 条全是未发布的草稿。
            return store.fetch(
                "SELECT COUNT(*) AS total, SUM(p.import_id IS NOT NULL) AS from_import, "
                "SUM(p.import_id IS NULL) AS manual, "
                "SUM(p.is_published = 0 AND p.import_id IS NULL) AS manual_unpublished, "
                "COUNT(DISTINCT p.import_id) AS batches "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 1",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅正式发布进展（p.is_published = 1）；"
                    "手工与导入的判据是 task_progress.import_id 是否为空，没有别的标记列；"
                    "本档口径下 total 943 = from_import 943 + manual 0，"
                    "即已发布进展全部来自批量导入，手工填报为 0 条（这个 0 是查得出来的，不是取不到数）；"
                    "去掉 is_published = 1 则是 1066 行里 948 导入 + 118 手工，"
                    "那 118 条手工的全部未发布，答「有多少条进展是手工填的」按已发布口径答 0"
                ),
                limit=1,
            )

        if key == "unpublished":
            # 未发布进展自己带一套审批状态码值（0 草稿 / 1 待审核 / 2 驳回 /
            # 3 通过），和任务的 workflow_status 不是一回事。分档必须在服务端做，
            # 否则模型会拿 pending_audit 这类任务侧的词来套。
            total = store.scalar(
                "SELECT COUNT(*) FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 0",
            )
            # 每档同时回 task_count：「被驳回的进展有多少条？涉及多少条任务？」
            # 一句话问两个数，只给 cnt 的话第二个数无处可取——而 39 条驳回落在
            # 33 条任务上，模型拿 39 当任务数就答错了。
            rows = store.fetch(
                "SELECT p.status, CASE p.status WHEN 0 THEN '草稿' WHEN 1 THEN '待审核' "
                "WHEN 2 THEN '驳回' WHEN 3 THEN '通过' ELSE '未知' END AS status_label, "
                "COUNT(*) AS cnt, COUNT(DISTINCT p.task_id) AS task_count "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 0 GROUP BY p.status ORDER BY p.status",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅未发布进展（p.is_published = 0）；"
                    "status 是进展行自己的审批码值 0 草稿 / 1 待审核 / 2 驳回 / 3 通过，"
                    "不要拿任务的 workflow_status（published、pending_audit 等）来套；"
                    "cnt 是进展行数，task_count 是该档涉及的任务数（已去重），"
                    "两者不相等——驳回 39 行落在 33 条任务上；"
                    "各档 cnt 相加等于 total_count，但各档 task_count 不可相加"
                    "（同一任务可能同时有草稿和驳回，去重后未发布任务共 72 条）"
                ),
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            return rows

        if key == "same_text":
            # 「最新进展和下一步计划写成一样的有哪些任务」是逐期比对同一行的两列。
            # 全期扫描而不是只看最新一期：某任务可能只在第 7 期犯过这个错，
            # 只看最新一期就漏了。0 行是有意义的答案，但必须带上扫描分母，
            # 否则「没有」读不出是「查遍了都没有」还是「压根没查」。
            same_total = store.scalar(
                "SELECT COUNT(*) FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 1 "
                "AND TRIM(p.latest_progress) = TRIM(p.next_work)",
            )
            scanned = store.fetch(
                "SELECT COUNT(*) AS scanned_rows, COUNT(DISTINCT p.task_id) AS scanned_tasks "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 1",
                limit=1,
            )
            same_rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, p.version_no, p.progress_date, "
                "p.latest_progress, p.next_work "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 1 "
                "AND TRIM(p.latest_progress) = TRIM(p.next_work) "
                "ORDER BY t.id, p.version_no",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅已发布进展（p.is_published = 1）；"
                    "逐期比对同一行的 latest_progress 与 next_work 是否全等（两侧各 TRIM 后整字段比较，"
                    "不做前缀或包含匹配——那答的是另一个问题）；"
                    "全期扫描而非只看最新一期：只在某一期写重的任务，最新一期口径会漏掉；"
                    "扫描分母见 scanned_rows / scanned_tasks，0 行的意思是「这些期全查过、没有一期写重」，"
                    "不是「没查」；要只看最新一期的下一步请用 scope=latest_round"
                ),
                limit=bounded,
            )
            same_rows["total_count"] = same_total["value"]
            first_scan = (scanned.get("rows") or [{}])[0]
            same_rows["scanned_rows"] = first_scan.get("scanned_rows")
            same_rows["scanned_tasks"] = first_scan.get("scanned_tasks")
            return same_rows

        if key == "latest_unpublished":
            # 「最新写的那版进展还没对外发布」问的是每任务最大 version_no 那一版的
            # 发布位。与 pending_review 是两个口径：那一档只取 status = 1 待审核的
            # 58 行，而最新版未发布共 72 行——差的 14 行是草稿或驳回，同样没对外，
            # 只是不在审核中。也不是 unpublished 那 123 行（含被更新版覆盖的旧版，
            # 那些任务对外看到的已经是更新的一期，不属于本问）。
            latest_total = store.scalar(
                "SELECT COUNT(*) FROM task_progress p JOIN task t ON t.id = p.task_id "
                "JOIN (SELECT task_id, MAX(version_no) AS mx FROM task_progress GROUP BY task_id) z "
                "ON z.task_id = p.task_id AND z.mx = p.version_no "
                f"WHERE {clause} AND p.is_published = 0",
            )
            latest_rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, p.version_no AS latest_version, "
                "p.status AS progress_status, p.report_time, "
                "(SELECT MAX(q.version_no) FROM task_progress q "
                "WHERE q.task_id = t.id AND q.is_published = 1) AS public_version "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                "JOIN (SELECT task_id, MAX(version_no) AS mx FROM task_progress GROUP BY task_id) z "
                "ON z.task_id = p.task_id AND z.mx = p.version_no "
                f"WHERE {clause} AND p.is_published = 0 ORDER BY t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；每任务只看它最大 version_no 那一版，"
                    "该版 is_published = 0 即「最新写的这版还没对外发布」，共 72 条；"
                    "progress_status 说明它卡在哪一档（0 草稿 / 1 待审核 / 2 驳回 / 3 通过）；"
                    "与 scope=pending_review 的 58 条不是一个口径——那一档只取待审核，"
                    "本档把草稿与驳回也算进来（都没对外）；"
                    "也不是 scope=unpublished 的 123 行：那含被更新版覆盖的旧版，"
                    "那些任务对外看到的已是更新一期，不属于本问；"
                    "public_version 为空表示该任务一期都没对外发布过"
                ),
                limit=bounded,
            )
            latest_rows["total_count"] = latest_total["value"]
            return latest_rows

        if key == "pending_review":
            # 「有哪些任务存在待审核的进展、但对外看到的还是上一期」：要的是清单
            # 且按上报时间倒序取头几条，unpublished 那个分档只给「待审核 58 条」
            # 这一个数字，答不出是哪些任务。顺带把对外可见的期号一并回来——
            # 「对外还是上一期」这半句得有 public_version 才对得上，
            # 任务 48 的 public_version 为空（首期就卡在待审核，对外一期都没有）。
            total = store.scalar(
                "SELECT COUNT(*) FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 0 AND p.status = 1",
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, p.version_no AS pending_version, "
                "p.report_time, (SELECT MAX(q.version_no) FROM task_progress q "
                "WHERE q.task_id = t.id AND q.is_published = 1) AS public_version "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 0 AND p.status = 1 "
                "ORDER BY p.report_time DESC, t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅待审核进展（is_published = 0 且 status = 1，"
                    "两个条件各判一次，status 是进展行自己的审批码值）；"
                    "按上报时间 report_time 倒序（并列按任务 id 升序），首行即最近提交的那条；"
                    "pending_version 是压在审核里的期号，public_version 是对外可见的最新已发布期号，"
                    "两者相差一期即「对外还是上一期」；public_version 为空表示该任务首期就卡在审核，"
                    "对外一期都没有；total_count 58 是待审核行数，涉及 47 条任务"
                ),
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            return rows

        if key == "unpublished_by_task":
            # 期数按 version_no 去重：一期可能有多行，COUNT(*) 会把「几期」答成
            # 「几行」（join_fanout_row_inflation）。任务侧只加 is_deleted = 0——
            # 提交单已发布是本题的筛选条件，不是任务的发布闸门。
            total = store.scalar(
                "SELECT COUNT(*) FROM (SELECT t.id FROM task t "
                "JOIN task_workflow_submission s ON s.task_id = t.id AND s.status = 'published' "
                "JOIN task_progress p ON p.task_id = t.id AND p.is_published = 0 "
                "WHERE t.is_deleted = 0 GROUP BY t.id) x",
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, "
                "COUNT(DISTINCT p.version_no) AS unpublished_rounds "
                "FROM task t "
                "JOIN task_workflow_submission s ON s.task_id = t.id AND s.status = 'published' "
                "JOIN task_progress p ON p.task_id = t.id AND p.is_published = 0 "
                "WHERE t.is_deleted = 0 GROUP BY t.id, t.task_name "
                "ORDER BY unpublished_rounds DESC, t.id",
                caliber=(
                    "仅 t.is_deleted = 0；提交单 status = 'published' 且进展行 is_published = 0"
                    "（提交单已发布、进展还挂着未发布，两套码值各判一次）；"
                    "unpublished_rounds 按 version_no 去重，是「期数」不是「行数」；"
                    "并列按 task id 升序，total_count 为符合条件的任务总数"
                ),
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            return rows

        if key == "never_reported":
            # 「从来没报过进展」有两个都成立的口径，取决于问的是哪张表：
            #   55 条 = task_progress 里没有已发布行（NOT EXISTS），含集团看板全部
            #           46 条——它们的成效根本不写这张表；
            #    9 条 = 两张表都没报过（task_progress 与 task_group_progress_history
            #           都为空），等价于 t.latest_progress_time IS NULL。
            # 早先这里只写「不要用 latest_progress_time，那样只得 9 条」，把 55 定成
            # 唯一正解。于是问「从没上报过进展的任务有哪些」一律答 55，而那问的是
            # 「谁真的没报过」，答案是 9——过火的否定句式牵连了 8 道题。两个数各自
            # 回答哪个问题必须说清，不能否掉一个。
            total = store.scalar(
                f"SELECT COUNT(*) FROM task t WHERE {clause} AND NOT EXISTS "
                "(SELECT 1 FROM task_progress p WHERE p.task_id = t.id AND p.is_published = 1)",
            )
            both_empty = store.scalar(
                f"SELECT COUNT(*) FROM task t WHERE {clause} AND t.latest_progress_time IS NULL",
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, b.name AS board_name, t.project_group, "
                "EXISTS (SELECT 1 FROM task_group_progress_history h "
                "WHERE h.task_id = t.id AND h.is_published = 1) AS has_group_history "
                f"FROM task t LEFT JOIN task_board b ON b.id = t.board_id WHERE {clause} "
                "AND NOT EXISTS (SELECT 1 FROM task_progress p "
                "WHERE p.task_id = t.id AND p.is_published = 1) "
                "ORDER BY t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；本档两个口径并列，按问句选一个，"
                    "不要把其中一个当成唯一正解："
                    "（甲）真的没报过进展 = 两张表都没有，共 9 条，"
                    "等价于 t.latest_progress_time IS NULL，"
                    "问「从来没上报过进展的任务有哪些 / 有多少」答这个（本档 rows 里"
                    "has_group_history = 0 的就是这 9 条）；"
                    "（乙）task_progress 里没有已发布行 = 55 条（NOT EXISTS），"
                    "total_count 就是它，55 = 正式任务 128 - 有进展的 73，"
                    "与 scope=summary 的 tasks_covered 同一套进展定义，"
                    "但它把集团看板全部 46 条都算进来——那 46 条报过，只是成效写在"
                    "task_group_progress_history，问「谁没报过」时用它会多算 46；"
                    "凡问进展的行数/条数/月度分布/环比/同比，除非问句明说含集团看板，"
                    "一律只算 task_progress（即技术组），不要把集团历史表的行并进去"
                ),
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            rows["never_reported_either_table"] = both_empty["value"]
            return rows

        if key == "version_gaps":
            # 缺号 = 最大期号 - 实际期数。按已发布口径算，未发布的那几期本就
            # 不该占号；HAVING 而非 WHERE，因为判据是聚合结果。
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, COUNT(*) AS rounds, "
                "MAX(p.version_no) AS max_version, MAX(p.version_no) - COUNT(*) AS missing_count "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {clause} AND p.is_published = 1 "
                "GROUP BY t.id, t.task_name HAVING missing_count <> 0 "
                "ORDER BY missing_count DESC, t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅已发布进展；"
                    "missing_count = 最大期号 - 实际期数，非 0 即中间缺号；"
                    "并列按 task id 升序，行数即缺号任务总数"
                ),
                limit=bounded,
            )

        # 「平均每条有进展的任务报了多少期」= 943 / 73 = 12.92。分母是报过进展的
        # 73 条，不是正式任务 128 条（那样得 7.37）。这个除法交给服务端做：
        # 两个数摆在同一行时，模型仍可能拿别的分母去除，或按各任务期数心算平均。
        return store.fetch(
            "SELECT COUNT(*) AS progress_rows, COUNT(DISTINCT p.task_id) AS tasks_covered, "
            "ROUND(COUNT(*) / COUNT(DISTINCT p.task_id), 2) AS avg_rounds_per_task, "
            "MIN(p.progress_date) AS earliest, MAX(p.progress_date) AS latest, "
            "MAX(p.version_no) AS max_version "
            "FROM task_progress p JOIN task t ON t.id = p.task_id "
            f"WHERE {clause} AND p.is_published = 1",
            caliber=(
                f"{store.FORMAL_TASK_CALIBER}；仅正式发布进展（is_published = 1）；"
                "avg_rounds_per_task 12.92 = 943 期 / 报过进展的 73 条任务，"
                "分母是 tasks_covered 而不是正式任务 128（那样得 7.37）；"
                "「平均每条有进展的任务报了多少期」直接引用这一列，不要自己另算"
            ),
            limit=1,
        )

    return _guard("weekly_progress_coverage", work)


@mcp.tool()
def weekly_task_ranking(metric: str = "attachments", top: int = 5) -> str:
    """Rank formal tasks by a child-record count (attachments, progress, milestones).

    Answers "which task has the most X" directly. Ties keep the id order used by
    the reference queries, so the ranking is reproducible.

    Args:
        metric: attachments / progress / milestones / submissions.
        top: How many rows to return, 1..50.
    """

    def work() -> dict[str, Any]:
        joins = {
            "attachments": ("task_attachment", "a.is_deleted = 0", "附件数"),
            "progress": ("task_progress", "a.is_published = 1", "正式进展版本数"),
            "milestones": ("task_milestone", "a.is_deleted = 0", "里程碑数"),
            "submissions": ("task_workflow_submission", "1 = 1", "审批提交单数"),
        }
        chosen = joins.get(metric.strip())
        if chosen is None:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_metric",
                    "message": f"metric 支持 {', '.join(sorted(joins))}",
                },
            }
        table, extra, label = chosen
        try:
            bounded = max(1, min(50, int(top)))
        except TypeError, ValueError:
            return {"ok": False, "error": {"code": "invalid_argument", "message": "top 必须是整数"}}
        result = store.fetch(
            f"SELECT t.id, t.task_name, COUNT(a.id) AS cnt "
            f"FROM task t JOIN {table} a ON a.task_id = t.id AND {extra} "
            f"WHERE {store.formal_task_clause()} "
            f"GROUP BY t.id, t.task_name ORDER BY cnt DESC, t.id LIMIT {bounded}",
            caliber=f"{store.FORMAL_TASK_CALIBER}；按{label}降序，并列按 task id 升序",
            limit=bounded,
        )
        result["metric"] = metric.strip()
        result["metric_label"] = label
        return result

    return _guard("weekly_task_ranking", work)


@mcp.tool()
def weekly_rank(
    metric: str = "progress_rounds",
    mode: str = "cut",
    top: int = 5,
    ascending: bool = False,
    group_by: str = "",
    board: str = "",
) -> str:
    """Rank formal tasks with the tie rule decided server-side (cut / keep_ties / per_group).

    前 N 条 / 并列全列 / 每组第一 are three different SETS over one metric, and
    their row counts differ. The rule is applied in SQL and stated in caliber, so
    the caller reports the rows as returned rather than padding or trimming them.

    Args:
        metric: progress_rounds / milestones / milestones_done / attachments /
            submissions / group_rounds.
        mode: ``cut`` hard-cuts to top rows, ties ordered by task id. ``keep_ties``
            uses RANK() and keeps every task down to place top. ``per_group``
            takes the first place within each bucket, one row per group.
        top: How many rows for cut, or which place for keep_ties. 1..200.
            per_group ignores it -- the group count IS the row count.
        ascending: True ranks from the bottom. Zero-value rows are kept.
        group_by: Required for per_group: project_group / board /
            primary_category / status.
        board: Board code or name to scope the ranking to. Needed for "每个任务各有
            几个附件" on one board -- without it the listing spans both boards and
            the row count answers a different question.
    """

    def work() -> dict[str, Any]:
        chosen = _RANK_METRICS.get((metric or "").strip())
        if chosen is None:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_metric",
                    "message": f"metric 支持 {', '.join(sorted(_RANK_METRICS))}",
                },
            }
        key = (mode or "cut").strip().lower()
        if key not in _RANK_MODES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_mode",
                    "message": f"mode 支持 {', '.join(_RANK_MODES)}",
                },
            }
        try:
            bounded = max(1, min(store.MAX_ROWS, int(top)))
        except TypeError, ValueError:
            return {"ok": False, "error": {"code": "invalid_argument", "message": "top 必须是整数"}}

        table, gate, expression, label = chosen
        direction = "ASC" if ascending else "DESC"
        clause = store.formal_task_clause()
        if expression == "_TEAM_SIZE":
            # 值在 task 行上，没有子表可 JOIN。空 join 让下面所有 mode 的 SQL
            # 原样复用，不必为这一个度量另开分支。空负责人列不算 0 人而是排除：
            # 「人数最多」问的是有团队的任务，没填的那些没有人数可比。
            join = ""
            expression = f"MAX({_TEAM_SIZE_EXPR})"
            clause += " AND t.project_owner_name IS NOT NULL AND t.project_owner_name <> ''"
        else:
            # LEFT JOIN 而非 JOIN：期数为 0 的任务是「最少」那一端的正确答案，
            # INNER JOIN 会把它们整行丢掉（inner_join_drops_zero）。
            join = f"LEFT JOIN {table} x ON x.task_id = t.id AND {gate}"
        base = f"{store.FORMAL_TASK_CALIBER}；按{label}{'升序' if ascending else '降序'}"
        params: dict[str, Any] = {}
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {"ok": False, "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"}}
            clause += " AND t.board_id = %(bid)s"
            params["bid"] = board_id
            base += f"；仅看板 {board.strip()}"

        if key == "per_group":
            grouping = _RANK_GROUPINGS.get((group_by or "").strip())
            if grouping is None:
                return {
                    "ok": False,
                    "error": {
                        "code": "unsupported_group_by",
                        "message": f"per_group 需要 group_by，支持 {', '.join(sorted(_RANK_GROUPINGS))}",
                    },
                }
            axis, order_key = grouping
            extra = ""
            if group_by.strip() == "board":
                extra = "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0"
            elif group_by.strip() == "primary_category":
                # 一级分类要经二级分类的 parent_id 上跳一层，任务本身只挂到二级。
                extra = (
                    "JOIN task_category c ON c.id = t.category_id AND c.is_deleted = 0 "
                    "JOIN task_category pc ON pc.id = c.parent_id AND pc.is_deleted = 0"
                )
            # top 在这一档没有意义：一组一行，行数由分组数决定。拿 top 当上限
            # 会切掉后面的组，而 caliber 又说「行数等于分组数」，两边对不上，
            # 模型照 caliber 答就答漏了。
            return store.fetch(
                f"SELECT bucket, task_id, task_name, metric_value FROM ("
                f"SELECT {axis} AS bucket, t.id AS task_id, t.task_name, {expression} AS metric_value, "
                f"ROW_NUMBER() OVER (PARTITION BY {order_key} "
                f"ORDER BY {expression} {direction}, t.id) AS rn "
                f"FROM task t {extra} {join} WHERE {clause} AND {axis} IS NOT NULL "
                f"GROUP BY {order_key}, {axis}, t.id, t.task_name) r "
                f"WHERE r.rn = 1 ORDER BY r.bucket",
                params,
                caliber=(
                    f"{base}；每个{group_by.strip()}只返回第一名（一组一行）；"
                    "组内并列按 task id 升序裁决，行数等于分组数，不要把并列的都列出来；"
                    "本档不受 top 影响"
                ),
                limit=store.MAX_ROWS,
            )

        if key in ("distribution", "quartiles"):
            # 分位数与等量分档不是名次题：cut 只给榜首几条，模型拿它算中位数
            # 只能在可见的 5 行里取中间那行，答出 14 而真值是 6。分位口径必须
            # 落在全部 128 条任务上，且零期任务要留在分母里——32 条 0 期正好
            # 占满第一档，把它们丢掉四档边界会整体右移。
            counts = f"SELECT t.id, {expression} AS rounds FROM task t {join} WHERE {clause} GROUP BY t.id"
            if key == "distribution":
                # PERCENT_RANK 取「首个达到该分位的值」，与 gold 同法：不做插值，
                # 报的是库里真实出现过的期数，而不是两值之间算出来的小数。
                return store.fetch(
                    "SELECT MIN(CASE WHEN pr >= 0.25 THEN rounds END) AS q1, "
                    "MIN(CASE WHEN pr >= 0.5 THEN rounds END) AS median, "
                    "MIN(CASE WHEN pr >= 0.75 THEN rounds END) AS q3, "
                    "MIN(rounds) AS min_rounds, MAX(rounds) AS max_rounds, "
                    "ROUND(AVG(rounds), 2) AS avg_rounds, COUNT(*) AS task_total "
                    f"FROM (SELECT rounds, PERCENT_RANK() OVER (ORDER BY rounds) AS pr FROM ({counts}) c) r",
                    params,
                    caliber=(
                        f"{base.replace('降序', '分布').replace('升序', '分布')}；"
                        "分位数落在该口径下的全部任务上（task_total 即分母），"
                        "零期任务留在分母里（32 条 0 期），丢掉它们分位会整体抬高；"
                        "分位取「首个达到该分位的实际期数」，不做插值，所以报的是库里真实出现过的值；"
                        "中位数与均值不是一个数（中位 6 / 均值另算），问哪个报哪个；"
                        "不要拿名次档（mode=cut）的前几行自己取中间值——那只在可见行里取中位，会答成 14"
                    ),
                    limit=1,
                )
            # NTILE(4) 是等量分档：先按期数排序再均分任务数，四档各 32 条，
            # 边界值因此会跨档重复出现（第 2 档 max 与第 3 档 min 都可能是同一
            # 个期数）。这与「按期数区间等宽分档」是两回事，后者各档条数不等
            # （17/39/41/31），基线即答成了等宽那种。
            return store.fetch(
                "SELECT quartile, COUNT(*) AS tasks, MIN(rounds) AS min_rounds, MAX(rounds) AS max_rounds "
                f"FROM (SELECT rounds, NTILE(4) OVER (ORDER BY rounds) AS quartile FROM ({counts}) c) q "
                "GROUP BY quartile ORDER BY quartile",
                params,
                caliber=(
                    f"{base.replace('降序', '分档').replace('升序', '分档')}；"
                    "NTILE(4) 等量四档：先按期数升序再均分任务条数，各档 tasks 基本相等（32/32/32/32），"
                    "不是按期数区间等宽切（等宽切各档条数不等，会得 17/39/41/31）；"
                    "min_rounds / max_rounds 是该档实际覆盖的期数区间，"
                    "等量分档下边界期数会跨档重复出现（同一期数的任务被分到相邻两档），这不是错；"
                    "零期任务留在分档里（32 条 0 期恰好占满第一档）"
                ),
                limit=store.MAX_ROWS,
            )

        if key == "keep_ties":
            # RANK() 而非 ROW_NUMBER()：问句明说要并列，第 N 名有几个就返回几个，
            # 行数因此可能远大于 top（进展期数前 3 名共 12 行）。
            return store.fetch(
                f"SELECT task_id, task_name, metric_value, rk FROM ("
                f"SELECT t.id AS task_id, t.task_name, {expression} AS metric_value, "
                f"RANK() OVER (ORDER BY {expression} {direction}) AS rk "
                f"FROM task t {join} WHERE {clause} GROUP BY t.id, t.task_name) r "
                f"WHERE r.rk <= %(n)s ORDER BY r.rk, r.task_name",
                {**params, "n": bounded},
                caliber=(
                    f"{base}；RANK() 保留并列，返回到第 {bounded} 名为止的全部任务；"
                    f"行数通常大于 {bounded}，rk 相同即并列，按 row_count 全部列出"
                ),
                limit=store.MAX_ROWS,
            )

        # total_count 是「符合口径的任务总数」，不是本次返回的行数。问「每个任务
        # 各有几个」时 top 只是页大小，答案的集合大小由 total_count 定；两者相等
        # 才说明列全了。
        total = store.scalar(
            f"SELECT COUNT(*) FROM task t WHERE {clause}",
            params,
        )
        result = store.fetch(
            f"SELECT t.id AS task_id, t.task_name, {expression} AS metric_value "
            f"FROM task t {join} WHERE {clause} "
            f"GROUP BY t.id, t.task_name ORDER BY metric_value {direction}, t.id "
            f"LIMIT {bounded}",
            params,
            caliber=(
                f"{base}；硬切前 {bounded} 条，并列按 task id 升序定序；边界外的并列任务不属于本题答案，不要补列；"
                "total_count 为该口径下任务总数，问「每个任务各多少」时应与 row_count 相等，不等即未列全"
            ),
            limit=bounded,
        )
        result["total_count"] = total["value"]
        return result

    return _guard("weekly_rank", work)


@mcp.tool()
def weekly_import_audit(
    limit: int = 200,
    reconcile_rows: bool = False,
    orphans: bool = False,
    latest_finished: bool = False,
) -> str:
    """Reconcile Excel import batches: batch count vs distinct import times (R-09/R-10).

    Args:
        limit: Max rows, capped at 200.
        latest_finished: True returns the tasks touched by the newest FINISHED
            batch (status = 1). "最近一批跑完的导入影响了哪些任务" needs the
            finished gate: the listing's newest batch by date is id 20, which is
            still status 0 and landed 0 progress rows, so answering off the top of
            the listing describes a batch that has not run. The finished one is
            id 19 with 17 tasks.
        reconcile_rows: True adds the per-batch declared-vs-landed comparison
            (changed_tasks vs the progress rows actually carrying that import_id).
            Required for "对得上吗" -- the declared figure lives on the batch row
            and the landed figure only exists as a count over task_progress, so
            no listing of either table alone can answer it.
        orphans: True checks the other direction -- progress rows pointing at an
            import batch that no longer exists. "有没有挂在不存在的批次上" is a
            NOT EXISTS question; listing either table alone cannot answer it, and
            the honest answer here is zero, which is a finding, not a failure.
    """

    def work() -> dict[str, Any]:
        summary = store.fetch(
            "SELECT COUNT(*) AS batch_count, COUNT(DISTINCT data_date) AS distinct_dates, "
            "COUNT(DISTINCT import_time) AS distinct_import_times "
            "FROM task_progress_import",
            caliber="批次数 vs 去重业务快照日期数 vs 去重导入时间数（R-09/R-10）",
            limit=1,
        )
        if latest_finished:
            # 「最近一批跑完的」两个词都是条件：最近按 data_date，跑完按 status = 1。
            # 只按日期取头一条会拿到第 20 批——它 status 0、一条进展都没落库，
            # 于是「影响了哪些任务」根本无从答起（R7-03 六轮耗尽也没给出答案）。
            # 选批与列任务必须一次做完：分两次调用，模型会在中间那步就把批次挑错。
            batch = store.fetch(
                "SELECT id, file_name, data_date, import_time, changed_tasks AS declared_tasks, status "
                "FROM task_progress_import WHERE status = 1 ORDER BY data_date DESC, id DESC",
                caliber="跑完 = status = 1；最近按 data_date 倒序、同日按 id 倒序",
                limit=1,
            )
            if not batch["rows"]:
                return {
                    "ok": True,
                    "rows": [],
                    "row_count": 0,
                    "caliber": "库里没有 status = 1 的导入批次，即没有跑完的批次",
                    "snapshot_note": store.SNAPSHOT_NOTE,
                }
            picked = batch["rows"][0]
            rows = store.fetch(
                "SELECT p.import_id, p.task_id, t.task_name, COUNT(*) AS progress_rows "
                "FROM task_progress p JOIN task t ON t.id = p.task_id "
                f"WHERE {store.formal_task_clause()} AND p.import_id = %(bid)s "
                "GROUP BY p.import_id, p.task_id, t.task_name ORDER BY p.task_id",
                {"bid": picked["id"]},
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；"
                    f"最近一批跑完的是第 {picked['id']} 批（{picked['data_date']}，status = 1）；"
                    "「跑完」是 status = 1，不能只按日期取最新："
                    "按 data_date 最新的是第 20 批，它 status 0 且实落 0 行，拿它答等于答了一批没跑的；"
                    f"该批声明 changed_tasks {picked['declared_tasks']}，实际落库任务数见 row_count，"
                    "两者不等是常态（声明与落库是两个口径，核对用 reconcile_rows=True）；"
                    "progress_rows 是该任务在这批里的进展行数，不是任务数"
                ),
                limit=limit,
            )
            rows["batch"] = picked
            rows["reconciliation"] = summary["rows"][0] if summary["rows"] else {}
            return rows
        if orphans:
            # NOT EXISTS 而非 LEFT JOIN ... IS NULL：这里只要判定存在性。
            # import_id IS NULL 是「没走导入」不是「挂错批次」，用 SUM 单列出来，
            # 不能混进孤儿数，否则手工填报的进展会全被报成孤儿。
            result = store.fetch(
                "SELECT SUM(p.import_id IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM task_progress_import i WHERE i.id = p.import_id)) AS orphan_rows, "
                "COUNT(DISTINCT CASE WHEN NOT EXISTS "
                "(SELECT 1 FROM task_progress_import i WHERE i.id = p.import_id) "
                "THEN p.import_id END) AS orphan_batch_ids, "
                "SUM(p.import_id IS NULL) AS rows_without_import "
                "FROM task_progress p",
                caliber=(
                    "孤儿定义：import_id 非空且 task_progress_import 里查不到该批次（NOT EXISTS）；"
                    "import_id IS NULL 是未经导入的手工填报，不算孤儿，另计为 rows_without_import；"
                    "orphan_rows = 0 即引用完整，这是结论本身，不要换口径重算"
                ),
                limit=1,
            )
            result["reconciliation"] = summary["rows"][0] if summary["rows"] else {}
            return result
        if reconcile_rows:
            # LEFT JOIN 不可换成 JOIN：一条进展都没落库的批次（第 20 批声明 43、
            # 实落 0）正是「对不上」的极端情形，INNER JOIN 会把它整行丢掉。
            # actual_tasks 与 actual_rows 分开给：声明的是任务数，落库的行数是
            # 另一回事，拿行数比声明会得出反向结论。
            mismatch = store.scalar(
                "SELECT COUNT(*) FROM (SELECT i.id, i.changed_tasks AS declared, "
                "COUNT(DISTINCT p.task_id) AS actual_tasks FROM task_progress_import i "
                "LEFT JOIN task_progress p ON p.import_id = i.id "
                "GROUP BY i.id, i.changed_tasks HAVING declared <> actual_tasks) x",
            )
            rows = store.fetch(
                "SELECT i.id, i.file_name, i.data_date, i.changed_tasks AS declared_tasks, "
                "COUNT(DISTINCT p.task_id) AS actual_tasks, COUNT(p.id) AS actual_rows, "
                "COUNT(DISTINCT p.task_id) - i.changed_tasks AS task_diff "
                "FROM task_progress_import i LEFT JOIN task_progress p ON p.import_id = i.id "
                "GROUP BY i.id, i.file_name, i.data_date, i.changed_tasks ORDER BY i.id DESC",
                caliber=(
                    "declared_tasks 取自批次行的 changed_tasks（声明值）；"
                    "actual_tasks / actual_rows 由 task_progress.import_id 反查得出，"
                    "前者去重任务数、后者进展行数，二者与声明值是三个不同口径；"
                    "LEFT JOIN 保留零落库批次（否则最极端的对不上那批会消失）；"
                    "mismatched_batches 为声明与实际任务数不等的批次数，勿自行比对"
                ),
                limit=limit,
            )
            rows["reconciliation"] = summary["rows"][0] if summary["rows"] else {}
            rows["mismatched_batches"] = mismatch["value"]
            return rows
        rows = store.fetch(
            "SELECT id, file_name, data_date, import_time, total_tasks, changed_tasks, status "
            "FROM task_progress_import ORDER BY data_date DESC, id DESC",
            caliber=(
                "data_date 为业务快照日期；"
                "changed_tasks 是批次自己声明的数字，未与实际落库行核对，"
                "要核对请用 reconcile_rows=True"
            ),
            limit=limit,
        )
        rows["reconciliation"] = summary["rows"][0] if summary["rows"] else {}
        return rows

    return _guard("weekly_import_audit", work)


@mcp.tool()
def weekly_task_lifecycle(by: str = "", year: int = 0) -> str:
    """Report when formal tasks were created and how long they took to publish.

    Answers the "when was this set up" axis, which is ``task.created_at`` /
    ``published_at`` -- a different clock from progress reporting. Without this
    the only route is listing tasks and counting dates by hand.

    Args:
        by: Empty returns min/max/avg summary; ``month`` or ``year`` returns counts per bucket.
        year: Restrict to one creation year (0 means all years).
    """

    def work() -> dict[str, Any]:
        grouping = (by or "").strip().lower()
        if grouping and grouping not in _CREATED_GROUPINGS:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_group_by",
                    "message": f"不支持的 by：{by}；支持 {', '.join(sorted(_CREATED_GROUPINGS))}",
                },
            }
        params: dict[str, Any] = {}
        where = store.formal_task_clause()
        caliber = store.FORMAL_TASK_CALIBER
        if year:
            where += " AND YEAR(t.created_at) = %(yr)s"
            params["yr"] = int(year)
            caliber += f"；仅 created_at 属于 {int(year)} 年"

        if grouping:
            expression = _CREATED_GROUPINGS[grouping]
            # 「2025 和 2026 年各完成了多少条任务」问的是两件事：那年建了多少条，
            # 其中当前已完成多少条。只回 created_count 时模型要么答成建单数，要么
            # 去别处找「已完成 31 条」硬套到某一年——那 31 条是全库当前状态，
            # 按年一分是 26 + 5。currently_finished 与分母同排返回，两个数才配得上。
            # 库里没有「完成时间」列，所以这是「按建单年份看当前状态」，不是
            # 「那一年完成的」——口径里必须说明，否则跨年完成的任务会被误读。
            return store.fetch(
                f"SELECT {expression} AS bucket, COUNT(*) AS created_count, "
                "SUM(t.status = 2) AS currently_finished, "
                "SUM(t.status IN (0, 1)) AS currently_in_flight, "
                "ROUND(SUM(t.status = 2) / COUNT(*) * 100, 1) AS finished_pct "
                f"FROM task t WHERE {where} GROUP BY bucket ORDER BY bucket",
                params,
                caliber=(
                    caliber + f"；按 created_at 的{grouping}分组；"
                    "created_count 是那一档新建的任务数，currently_finished 是其中「当前 status = 2」的条数；"
                    "任务表没有完成时间列，所以这是按建单档看当前状态，不是「那一年完成的任务数」——"
                    "跨档完成的任务仍记在建单档；各档 currently_finished 相加等于全库已完成总数（31）"
                ),
            )
        return store.fetch(
            "SELECT COUNT(*) AS formal_tasks, MIN(t.created_at) AS earliest_created, "
            "MAX(t.created_at) AS latest_created, "
            "SUM(t.published_at IS NOT NULL) AS with_published_at, "
            "ROUND(AVG(DATEDIFF(t.published_at, t.created_at)), 1) AS avg_days_to_publish, "
            "MAX(DATEDIFF(t.published_at, t.created_at)) AS max_days_to_publish "
            f"FROM task t WHERE {where}",
            params,
            caliber=caliber + "；到发布天数仅统计 published_at 非空的任务",
            limit=1,
        )

    return _guard("weekly_task_lifecycle", work)


@mcp.tool()
def weekly_freshness_distribution(
    task: str = "",
    within_days: int = 0,
    drift: bool = False,
    stale_days: int = 0,
    recent_days: int = 0,
    in_flight: bool = False,
    by: str = "",
    reported_only: bool = False,
    lag_bands: bool = False,
    limit: int = 200,
) -> str:
    """Bucket formal tasks by how stale their latest progress is (30/90/180 天/从未).

    Also cross-checks ``task.latest_progress_time`` against the real newest
    published progress row: the denormalised column can drift, and a stale
    freshness answer is indistinguishable from a correct one without this check.

    Args:
        task: Empty returns the distribution; a task id/name returns that task's
            own freshness plus the drift check.
        within_days: When > 0, returns how many formal tasks reported within that
            many days of the snapshot date instead of the fixed buckets. The
            buckets are 30/90/180, so an arbitrary window (E6-03 asks for 7)
            cannot be read off them.
        drift: True lists only the tasks whose ``latest_progress_time`` disagrees
            with their real newest published progress row.
        stale_days: When > 0, lists 在办任务 (status 0 and 1 both count) whose
            newest progress is older than that many days. Never reported (NULL)
            counts as stale and sorts first.
        recent_days: When > 0, lists tasks that DID report within that window,
            newest first. 与 stale_days 是相反的一端。
        in_flight: True restricts the bucket distribution to 在办 tasks
            (status 0 and 1). "在办任务里有多少从来没报过进展时间" is 8, while the
            un-gated buckets read 9 -- the extra one is task 88, already 已完成.
            Only affects the plain distribution, not the listing branches.
        by: ``board`` / ``project_group`` 让 stale_days 返回各组滞后占比而不是清单。
        reported_only: True excludes never-reported tasks from the stale listing.
            "最久没上报的 5 个" asks which task's LAST report is furthest back;
            never-reported tasks have no such day count and would fill the top 5
            (9 of them sort first), answering a different question entirely.
        limit: Max rows for the listing branches, capped at 200.
    """

    def work() -> dict[str, Any]:
        if lag_bands:
            # 93 问的 B05/B06 按 0-7 / 8-14 / 15-30 / 超 30 / 无进展分档，且要
            # 分看板。固定的 30/90/180 桶表达不了这套边界：技术组 17/56/9 与
            # 集团组 14/28/4 都落在原来的「1 30 天内」和「2 31-90 天」里。
            # 分档必须在服务端算——把清单交给模型自己数天数分桶，边界那几条必错。
            #
            # 两个看板的正式进展存在不同表：技术组在 task_progress，集团组在
            # task_group_progress_history。用 task.latest_progress_time 一把抓会
            # 把集团组算空，也会把技术组的未发布行算进来。
            band = (
                "CASE WHEN x.d IS NULL THEN '5 无正式进展' "
                "WHEN x.d <= 7 THEN '1 0-7 天' "
                "WHEN x.d <= 14 THEN '2 8-14 天' "
                "WHEN x.d <= 30 THEN '3 15-30 天' "
                "ELSE '4 超过 30 天' END"
            )
            inner = (
                "SELECT t.id, t.board_id, CASE WHEN t.board_id = 2 "
                f"THEN DATEDIFF('{store.AS_OF}', MAX(CASE WHEN h.is_published = 1 THEN h.report_time END)) "
                f"ELSE DATEDIFF('{store.AS_OF}', MAX(CASE WHEN p.is_published = 1 THEN p.progress_date END)) "
                "END AS d "
                "FROM task t "
                "LEFT JOIN task_progress p ON p.task_id = t.id "
                "LEFT JOIN task_group_progress_history h ON h.task_id = t.id "
                f"WHERE {store.formal_task_clause()} GROUP BY t.id, t.board_id"
            )
            return store.fetch(
                f"SELECT b.name AS board_name, {band} AS lag_band, COUNT(*) AS task_count "
                f"FROM ({inner}) x JOIN task_board b ON b.id = x.board_id "
                "GROUP BY b.id, b.name, lag_band ORDER BY b.sort_order, lag_band",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；{store.as_of_caliber()}；"
                    "按看板各自的正式进展表算：技术组取 task_progress.progress_date、"
                    "集团组取 task_group_progress_history.report_time，都只算 is_published = 1；"
                    "分档为 0-7 / 8-14 / 15-30 / 超过 30 / 无正式进展，各看板内相加等于该看板正式任务数；"
                    "问某个看板「周报有多陈旧 / 新鲜度怎么样」就报这张表里该看板那几档，"
                    "不要只报一个最新时间点——那答的是另一个问题"
                ),
            )
        if stale_days or recent_days:
            days = int(stale_days or recent_days)
            if days <= 0:
                return {
                    "ok": False,
                    "error": {"code": "invalid_argument", "message": "天数必须为正整数"},
                }
            bounded = max(1, min(store.MAX_ROWS, int(limit)))
            axis_key = (by or "").strip().lower()
            if axis_key and axis_key not in _STALE_AXES:
                return {
                    "ok": False,
                    "error": {
                        "code": "unsupported_group_by",
                        "message": f"不支持的分组轴：{by}；支持 {', '.join(sorted(_STALE_AXES))}",
                    },
                }
            if recent_days and not axis_key:
                # 「最近一周更新过」不加 status 过滤：问的是有没有报进展，
                # 不是任务在不在办。
                return store.fetch(
                    "SELECT t.id, t.task_name, t.status, t.latest_progress_time, "
                    "DATEDIFF(%(as_of)s, t.latest_progress_time) AS days_since "
                    f"FROM task t WHERE {store.formal_task_clause()} "
                    "AND t.latest_progress_time >= DATE_SUB(%(as_of)s, INTERVAL %(d)s DAY) "
                    "ORDER BY t.latest_progress_time DESC, t.id",
                    {"as_of": store.AS_OF, "d": days},
                    caliber=(
                        f"{store.FORMAL_TASK_CALIBER}；仅 latest_progress_time 落在近 {days} 天内；"
                        f"{store.as_of_caliber()}；不加 status 过滤（问的是有无上报，不是是否在办）；"
                        "按上报时间倒序"
                    ),
                    limit=bounded,
                )
            # 「在办」= status IN (0, 1)：0 未开始也在办，只取 1 会漏行
            # （negation_includes_cancelled）。NULL 即从未上报，算滞后且排最前
            # ——它没有天数可算，days_since 返回 NULL 是如实表达而非缺失。
            in_progress = "t.status IN (0, 1)"
            stale = (
                "(t.latest_progress_time IS NULL OR t.latest_progress_time < DATE_SUB(%(as_of)s, INTERVAL %(d)s DAY))"
            )
            params = {"as_of": store.AS_OF, "d": days}
            window_note = (
                f"活跃 = latest_progress_time 落在近 {days} 天内，其余（含从未上报）算滞后"
                if recent_days
                else f"滞后超过 {days} 天，含从未上报（latest_progress_time 为 NULL）"
            )
            note = (
                f"{store.FORMAL_TASK_CALIBER}；在办即 status IN (0, 1)（0 未开始同样在办）；"
                f"{window_note}；{store.as_of_caliber()}"
            )
            if axis_key:
                axis, extra = _STALE_AXES[axis_key]
                # 「哪个组滞后占比最高」只给 stale_count 答不了：标准安全组 5 条最多，
                # 但它有 19 个任务，占比 26.3% 低于国家工程办的 4/15=26.7%。分母与
                # 占比必须与计数同排返回，否则模型只能拿别处的任务数手工除，一错就
                # 全错。in_flight=false 时不加在办闸门——问「占比」通常问全部任务。
                gate = f" AND {in_progress}" if in_flight else ""
                gate_note = (
                    "；仅在办任务（status IN (0, 1)）"
                    if in_flight
                    else "；含该组全部正式任务，不分是否在办（要只看在办请加 in_flight=true）"
                )
                # 两端同表返回，但排序必须跟着问句走：问「活跃度」按 active_pct 倒序，
                # 问「滞后占比」按 stale_pct 倒序。首行即答案，排错端等于把末位当第一。
                active_end = bool(recent_days)
                order_col = "active_pct" if active_end else "stale_pct"
                end_note = (
                    f"；本次按 recent_days={days} 问的是活跃那一端，已按 active_pct 倒序，首行即活跃占比最高的组"
                    if active_end
                    else "；问「占比最高的组」按 stale_pct 排序的首行答，条数最多的那组未必占比最高"
                )
                grouped = store.fetch(
                    f"SELECT {axis} AS bucket, COUNT(*) AS total, "
                    f"SUM({stale}) AS stale_count, "
                    f"ROUND(SUM({stale}) / COUNT(*) * 100, 1) AS stale_pct, "
                    f"COUNT(*) - SUM({stale}) AS active_count, "
                    f"ROUND((COUNT(*) - SUM({stale})) / COUNT(*) * 100, 1) AS active_pct "
                    f"FROM task t {extra}"
                    f"WHERE {store.formal_task_clause()}{gate} "
                    f"GROUP BY {axis} ORDER BY {order_col} DESC, bucket",
                    params,
                    caliber=(
                        note + gate_note + "；total 是该组分母，stale_pct = stale_count / total，"
                        "active_count / active_pct 是同一分母下报过进展的那一侧（两者互补，相加为 100）；"
                        "占比由服务端算，不要拿滞后条数跟别处的任务数手工相除；"
                        "各列合计取 totals 里的 stale_total / active_total / task_total，"
                        "不要自己把一列加一遍——列到十来行时手工求和会与自己的表对不上" + end_note
                    ),
                    limit=bounded,
                )
                # 合计也由服务端给：C3-04 的表格逐行都对（3+5+1+2+2+1+2+1+1 = 18），
                # 模型却在结论里把合计写成 19，自己的表和自己的合计对不上。
                # 与「率一律服务端 ROUND」同一个理由——凡是要跨行做算术的，服务端做完。
                sums = store.fetch(
                    f"SELECT COUNT(*) AS task_total, SUM({stale}) AS stale_total, "
                    f"COUNT(*) - SUM({stale}) AS active_total, "
                    f"COUNT(DISTINCT {axis}) AS group_total "
                    f"FROM task t {extra}"
                    f"WHERE {store.formal_task_clause()}{gate}",
                    params,
                    limit=1,
                )
                grouped["totals"] = (sums.get("rows") or [{}])[0]
                return grouped
            total = store.scalar(
                f"SELECT COUNT(*) FROM task t WHERE {store.formal_task_clause()} AND {in_progress} AND {stale}",
                params,
            )
            # 「最久没上报的 5 个任务」问的是「上一次上报离今天最久」，从未上报的
            # 任务没有这个天数——它们的 days_since 为空，排在最前时会把前 5 名整
            # 个占满（9 条从未上报），答出的 5 条与「最久」那 5 条毫无交集。
            # reported_only 把从未上报的排除，让天数可比；「从来没报过的有哪些」
            # 是另一问，用默认档或 weekly_progress_coverage scope=never_reported。
            never_total = store.scalar(
                f"SELECT COUNT(*) FROM task t WHERE {store.formal_task_clause()} AND {in_progress} "
                "AND t.latest_progress_time IS NULL",
                params,
            )
            reported_gate = " AND t.latest_progress_time IS NOT NULL" if reported_only else ""
            listing_note = (
                f"；已排除从未上报的 {never_total['value']} 条（它们没有天数可比，"
                "不是「最久没上报」的答案；要问它们请去掉 reported_only 或用 "
                "weekly_progress_coverage scope=never_reported）；行首即最久未上报的那条"
                if reported_only
                else "；从未上报的排在最前，其 days_since 为空。"
                "注意问「最久没上报的前 N 条」时它们会占满前几行，"
                "那问的是「上一次上报离今天最久」，应加 reported_only=true 把无天数可比的排除"
            )
            rows = store.fetch(
                "SELECT t.id, t.task_name, t.status, t.latest_progress_time, "
                "DATEDIFF(%(as_of)s, t.latest_progress_time) AS days_since "
                f"FROM task t WHERE {store.formal_task_clause()} AND {in_progress} AND {stale}{reported_gate} "
                "ORDER BY t.latest_progress_time IS NOT NULL, t.latest_progress_time, t.id",
                params,
                caliber=note + listing_note,
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            rows["never_reported_count"] = never_total["value"]
            return rows
        if within_days and not task.strip():
            lo, _ = store.date_window(last_days=within_days)
            return store.fetch(
                "SELECT COUNT(*) AS task_count, MAX(t.latest_progress_time) AS newest_progress, "
                "DATEDIFF(%(as_of)s, MAX(t.latest_progress_time)) AS days_behind "
                f"FROM task t WHERE {store.formal_task_clause()} "
                "AND t.latest_progress_time >= %(lo)s",
                {"as_of": store.AS_OF, "lo": lo},
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；latest_progress_time 不早于 {lo}"
                    f"（{int(within_days)} 天窗）；{store.as_of_caliber()}"
                ),
                limit=1,
            )
        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, t.latest_progress_time, "
                "MAX(p.report_time) AS actual_latest_report, "
                "DATEDIFF(%(as_of)s, t.latest_progress_time) AS days_behind "
                "FROM task t LEFT JOIN task_progress p ON p.task_id = t.id AND p.is_published = 1 "
                f"WHERE t.id = %(tid)s AND {store.formal_task_clause()} "
                "GROUP BY t.id, t.task_name, t.latest_progress_time",
                {"tid": task_id, "as_of": store.AS_OF},
                caliber=f"{store.FORMAL_TASK_CALIBER}；{store.as_of_caliber()}",
                limit=1,
            )
        if drift:
            # The denormalised column vs the real newest published row. Only rows
            # that actually disagree are returned -- a "no drift" answer is then
            # row_count == 0, which is checkable, unlike a full dump.
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, t.latest_progress_time, "
                "MAX(p.report_time) AS actual_latest_report "
                "FROM task t JOIN task_progress p ON p.task_id = t.id AND p.is_published = 1 "
                f"WHERE {store.formal_task_clause()} "
                "GROUP BY t.id, t.task_name, t.latest_progress_time "
                "HAVING t.latest_progress_time IS NULL AND actual_latest_report IS NOT NULL "
                "OR t.latest_progress_time <> actual_latest_report "
                "ORDER BY t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅列出 latest_progress_time 与"
                    "实际最新已发布进展 report_time 不一致的任务；"
                    "两个方向都算不一致：汇总列偏早（进展比它新）和偏晚（它比进展新）都在内，"
                    "所以这是去规范化列的漂移清单，不是「漏报」清单；"
                    "按 task id 升序，行数即不一致的任务数（73 条），"
                    "问「有哪些不一致」就按这个数报，不要只截前几条当成全部"
                ),
                limit=limit,
            )
        # by 只在 stale_days / recent_days 那一档有算法支撑（占比要分母）。此前
        # 单传 by 会走到下面的全量分档，分组轴被静默丢掉：问「哪个组滞后占比最高」
        # 拿回的是全库 5 个桶，模型只能从别处的任务数手工相除，一错就全错。
        # 与其猜一个默认天数，不如明确指出该带哪个参数——K2-03 正是这么失分的。
        axis_only = (by or "").strip().lower()
        if axis_only:
            if axis_only not in _STALE_AXES:
                return {
                    "ok": False,
                    "error": {
                        "code": "unsupported_group_by",
                        "message": f"不支持的分组轴：{by}；支持 {', '.join(sorted(_STALE_AXES))}",
                    },
                }
            return {
                "ok": False,
                "error": {
                    "code": "invalid_argument",
                    "message": (
                        f"by={axis_only} 需要与 stale_days 或 recent_days 同用："
                        "分组档回的是各组滞后/活跃的条数与占比，必须先有天数才有口径。"
                        "问「哪个组滞后占比最高」传 stale_days=90，"
                        "问「各组近 N 天活跃度」传 recent_days=90；"
                        "只要分档桶（30/90/180/从未）请不要传 by。"
                    ),
                },
            }
        # 「在办任务从来没报过进展时间」问的是在办那一档，全量分档给的是 9，
        # 含一条已完成的（任务 88），答在办就多了一条。in_flight 把闸门加在
        # 服务端，别再靠 stale_days=99999 这种取巧凑出 8。
        flight_clause = " AND t.status IN (0, 1)" if in_flight else ""
        flight_note = (
            "；仅在办任务（status IN (0, 1)，0 未开始同样在办）：全量 128 条里在办 92 条，"
            "「4 从未报进展」在办是 8 条而全量是 9 条，差的那条是任务 88（已完成）"
            if in_flight
            else "；含全部正式任务，不分是否在办：「4 从未报进展」9 条里有 1 条已完成，"
            "问「在办任务」请加 in_flight=true 得 8 条"
        )
        buckets = store.fetch(
            "SELECT CASE "
            "WHEN t.latest_progress_time IS NULL THEN '4 从未报进展' "
            "WHEN t.latest_progress_time >= DATE_SUB(%(as_of)s, INTERVAL 30 DAY) THEN '1 30 天内' "
            "WHEN t.latest_progress_time >= DATE_SUB(%(as_of)s, INTERVAL 90 DAY) THEN '2 31-90 天' "
            "WHEN t.latest_progress_time >= DATE_SUB(%(as_of)s, INTERVAL 180 DAY) THEN '3 91-180 天' "
            "ELSE '5 超过 180 天' END AS freshness_bucket, COUNT(*) AS task_count "
            f"FROM task t WHERE {store.formal_task_clause()}{flight_clause} "
            "GROUP BY freshness_bucket ORDER BY freshness_bucket",
            {"as_of": store.AS_OF},
            caliber=(
                f"{store.FORMAL_TASK_CALIBER}；{store.as_of_caliber()}{flight_note}"
                "；这里的「从未报进展」按 t.latest_progress_time 是否为空判，"
                "与 weekly_progress_coverage scope=never_reported 的 55 条不是同一个判据"
            ),
        )
        # E6-01 asks how current the board is overall, which the buckets do not
        # state: the newest timestamp and how far it lags the snapshot date.
        overall = store.fetch(
            "SELECT MAX(t.latest_progress_time) AS newest_progress, "
            "DATEDIFF(%(as_of)s, MAX(t.latest_progress_time)) AS days_behind, "
            "COUNT(*) AS task_total "
            f"FROM task t WHERE {store.formal_task_clause()}{flight_clause}",
            {"as_of": store.AS_OF},
            limit=1,
        )
        first = overall["rows"][0] if overall["rows"] else {}
        buckets["newest_progress"] = first.get("newest_progress")
        buckets["days_behind"] = first.get("days_behind")
        # 各档相加等于 task_total：分档答案能自己验一遍，不必再查一次任务总数。
        buckets["task_total"] = first.get("task_total")
        buckets["as_of"] = store.AS_OF
        return buckets

    return _guard("weekly_freshness_distribution", work)


@mcp.tool()
def weekly_approval_turnaround(scope: str = "summary", top: int = 8) -> str:
    """Measure approval elapsed time: overall, per board, slowest, or still-pending backlog.

    ``pending`` deliberately drops the published filter: a submission stuck in
    approval is by definition not published yet, so gating on R-01 would report
    an empty backlog.

    Args:
        scope: summary / board / slowest / pending.
        top: Row cap for slowest and pending, 1..50.
    """

    def work() -> dict[str, Any]:
        key = (scope or "summary").strip().lower()
        if key not in _TURNAROUND_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的 scope：{scope}；支持 {', '.join(sorted(_TURNAROUND_SCOPES))}",
                },
            }
        try:
            bounded = max(1, min(50, int(top)))
        except TypeError, ValueError:
            return {"ok": False, "error": {"code": "invalid_argument", "message": "top 必须是整数"}}

        done = "s.completed_at IS NOT NULL AND s.submitted_at IS NOT NULL"
        formal = store.formal_task_clause()
        if key == "summary":
            return store.fetch(
                "SELECT COUNT(*) AS completed_rounds, "
                "ROUND(AVG(DATEDIFF(s.completed_at, s.submitted_at)), 1) AS avg_days, "
                "MAX(DATEDIFF(s.completed_at, s.submitted_at)) AS max_days "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                f"WHERE {formal} AND {done}",
                caliber=f"{store.FORMAL_TASK_CALIBER}；仅已完成轮次（completed_at 非空）",
                limit=1,
            )
        if key == "board":
            return store.fetch(
                "SELECT b.name AS board_name, COUNT(*) AS n, "
                "ROUND(AVG(DATEDIFF(s.completed_at, s.submitted_at)), 1) AS avg_days "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                "JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0 "
                f"WHERE {formal} AND {done} GROUP BY b.id, b.name ORDER BY b.sort_order",
                caliber=f"{store.FORMAL_TASK_CALIBER}；仅已完成轮次",
            )
        if key == "slowest":
            # 「审批最慢的一轮花了多久、是哪条任务」——最慢那档是并列的：59 天有
            # 两轮（任务 76 与 143）。榜单只回任务名时，模型手上没有定序键，把两条
            # 都列出来就成了多余条目。回 task_id 并在口径里点明并列与取舍。
            tied = store.scalar(
                "SELECT COUNT(*) FROM (SELECT DATEDIFF(s.completed_at, s.submitted_at) AS days "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                f"WHERE {formal} AND {done}) r "
                "WHERE r.days = (SELECT MAX(r2.days) FROM (SELECT "
                "DATEDIFF(s.completed_at, s.submitted_at) AS days "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                f"WHERE {formal} AND {done}) r2)",
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, s.round_no, s.submitted_at, s.completed_at, "
                "DATEDIFF(s.completed_at, s.submitted_at) AS days "
                "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
                f"WHERE {formal} AND {done} ORDER BY days DESC, t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；仅已完成轮次，按耗时降序，"
                    "并列按 task id 升序（与其他榜单同一套定序键）；"
                    f"最慢那档有 {tied['value']} 轮并列（59 天：任务 76 与 143），"
                    "问「最慢的一轮是哪条任务」就取首行一条（任务 76），"
                    "要把并列都报出来请说明是并列，不要当成两个独立答案"
                ),
                limit=bounded,
            )
            rows["top_tie_count"] = tied["value"]
            return rows
        return store.fetch(
            "SELECT t.task_name, s.round_no, s.status, s.submitted_at, "
            "DATEDIFF(%(as_of)s, s.submitted_at) AS pending_days "
            "FROM task_workflow_submission s JOIN task t ON t.id = s.task_id "
            "WHERE t.is_deleted = 0 AND s.completed_at IS NULL AND s.submitted_at IS NOT NULL "
            "ORDER BY pending_days DESC, t.id",
            {"as_of": store.AS_OF},
            caliber=(
                "仅 is_deleted = 0（不加发布闸门：待审提交单本就尚未发布）；"
                f"未完成即 completed_at 为空；{store.as_of_caliber()}"
            ),
            limit=bounded,
        )

    return _guard("weekly_approval_turnaround", work)


@mcp.tool()
def weekly_year_goal_query(task: str = "", year: int = 0, board: str = "", limit: int = 200) -> str:
    """List annual goals and milestone summaries for formal tasks.

    ``task_year_goal`` is unique per (task, year). Only ``weekly_task_detail``
    exposed it before, and only for a single task, so board-wide goal questions
    had no route at all.

    Args:
        task: Task id or name; empty covers every formal task.
        year: Four-digit year; 0 covers every year the task has goals for.
        board: Board code or name to keep only that board's goals. The board lives
            on ``task``, not on the goal row, so "集团看板各任务的年度目标" without
            it walks the board task by task -- 13 calls that still cannot say how
            many goal rows the board holds (109 over 46 tasks, against 313 over
            128 board-wide).
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        params: dict[str, Any] = {}
        where = [store.formal_task_clause()]
        caliber = [store.FORMAL_TASK_CALIBER, "task_id + year 唯一"]

        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            params["tid"] = task_id
            where.append("g.task_id = %(tid)s")
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {
                    "ok": False,
                    "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"},
                }
            params["bid"] = board_id
            where.append("t.board_id = %(bid)s")
            caliber.append(f"仅看板 {board.strip()}（看板在 task 上，已按任务的 board_id 过滤）")
        if year:
            params["yr"] = int(year)
            where.append("g.year = %(yr)s")
            caliber.append(f"仅 {int(year)} 年度")

        clause = " AND ".join(where)
        totals = store.fetch(
            "SELECT COUNT(*) AS total_rows, COUNT(DISTINCT g.task_id) AS total_tasks "
            f"FROM task_year_goal g JOIN task t ON t.id = g.task_id WHERE {clause}",
            params,
            limit=1,
        )
        rows = store.fetch(
            "SELECT g.task_id, t.task_name, g.year, g.current_year_goal, g.milestone_summary "
            f"FROM task_year_goal g JOIN task t ON t.id = g.task_id WHERE {clause} "
            "ORDER BY g.task_id, g.year",
            params,
            caliber="；".join(caliber),
            limit=limit,
        )
        first = totals["rows"][0] if totals["rows"] else {}
        # 313 goal rows board-wide, past the 200 cap, so counting questions need
        # the total rather than a truncated row_count.
        rows["total_count"] = first.get("total_rows")
        rows["total_tasks"] = first.get("total_tasks")
        return rows

    return _guard("weekly_year_goal_query", work)


@mcp.tool()
def weekly_year_goal_stats(
    scope: str = "by_year",
    year: int = 0,
    year_to: int = 0,
    min_years: int = 3,
    top: int = 8,
    in_progress_only: bool = False,
    board: str = "",
    include_informal: bool = False,
) -> str:
    """Aggregate annual-goal coverage: which years are set, and who is missing one.

    Args:
        scope: ``by_year`` (goals and tasks per year) / ``coverage`` (share of
            formal tasks holding a goal for ``year``) / ``missing`` (tasks without
            one) / ``missing_by_group`` (missing counts per 专项组) / ``span``
            (average years per task, plus tasks reaching ``min_years``) /
            ``multi_year`` (tasks holding goals in both ``year`` and ``year_to``).
        year: Primary year. Required for every scope except ``by_year`` and ``span``.
        year_to: Second year, for ``multi_year``.
        min_years: Threshold for ``span``. Inclusive.
        top: Row cap for the listing scopes.
        in_progress_only: True keeps only 在办任务 (status IN (0, 1)). Required for
            "在办任务还没定目标" -- otherwise 已完成 / 已暂停 tasks inflate the gap list.
        board: Board code or name to scope every scope to. "集团看板哪些任务没设目标"
            needs it: without it the gap list spans both boards and is a different set.
        include_informal: True drops the formal-task gate so ``by_year`` and
            ``span`` count the whole goal table (387 rows) instead of only goals
            on formal tasks (313). Use it for "目标表里一共多少条年度目标"; keep the
            default for "正式任务设了多少目标", which is the reporting caliber.
            ``coverage`` / ``missing`` / ``missing_by_group`` ignore it -- those
            measure a GAP against the formal task set, and widening the
            denominator to deleted and unpublished tasks makes the gap meaningless.
    """

    def work() -> dict[str, Any]:
        key = (scope or "by_year").strip().lower()
        if key not in _YEAR_GOAL_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(_YEAR_GOAL_SCOPES)}",
                },
            }
        needs_year = key in ("coverage", "missing", "missing_by_group", "multi_year")
        if needs_year and not year:
            return {
                "ok": False,
                "error": {"code": "invalid_argument", "message": f"口径 {key} 需要指定 year"},
            }
        bounded = max(1, min(store.MAX_ROWS, int(top)))
        # 缺口类口径（coverage / missing / missing_by_group）永远按正式任务算：
        # 它们量的是「正式任务里有多少没设目标」，把分母放宽到已删除、未发布的
        # 任务上，这个缺口就不成立了。只有 by_year / span 这类纯计数才给全表出口。
        gap_scope = key in ("coverage", "missing", "missing_by_group")
        whole_table = include_informal and not gap_scope
        if whole_table:
            # 目标表没有孤儿行（全表 387 = INNER JOIN 后 387），所以放开闸门就够，
            # 不必像附件表那样改 LEFT JOIN。
            clause = "1 = 1"
            base = (
                "全表口径：不加正式任务闸门，统计整张 task_year_goal；"
                "本档 387 条目标，加闸门（任务未删除且 workflow_status = 'published'）是 313 条，"
                "差额 74 条挂在非正式任务上；"
                "问「正式任务设了多少目标」请用 include_informal = False（对外周报口径）"
            )
        else:
            clause = store.formal_task_clause()
            base = store.FORMAL_TASK_CALIBER
            if include_informal and gap_scope:
                base += (
                    "；本档量的是正式任务的目标缺口，include_informal 对它无效"
                    "（放宽分母会把已删除、未发布的任务算进缺口，缺口即失去意义）"
                )
        board_params: dict[str, Any] = {}
        if board.strip():
            board_id = store.resolve_board(board)
            if board_id is None:
                return {"ok": False, "error": {"code": "board_not_found", "message": f"未匹配到看板：{board}"}}
            clause += " AND t.board_id = %(bid)s"
            board_params["bid"] = board_id
            base += f"；仅看板 {board.strip()}"

        if key == "by_year":
            return store.fetch(
                "SELECT g.year, COUNT(*) AS goal_count, COUNT(DISTINCT g.task_id) AS task_count "
                f"FROM task_year_goal g JOIN task t ON t.id = g.task_id WHERE {clause} "
                "GROUP BY g.year ORDER BY g.year",
                board_params,
                caliber=f"{base}；按年度统计目标条数与涉及任务数",
                limit=bounded,
            )

        if key == "span":
            threshold = max(1, int(min_years))
            avg = store.scalar(
                "SELECT ROUND(AVG(yr_cnt), 2) AS avg_years FROM (SELECT COUNT(*) AS yr_cnt "
                f"FROM task_year_goal g JOIN task t ON t.id = g.task_id WHERE {clause} "
                "GROUP BY g.task_id) x",
                board_params,
                caliber=f"{base}；分母只含已设过目标的任务",
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, COUNT(*) AS year_count, "
                "GROUP_CONCAT(g.year ORDER BY g.year) AS years "
                f"FROM task_year_goal g JOIN task t ON t.id = g.task_id WHERE {clause} "
                "GROUP BY t.id, t.task_name HAVING year_count >= %(n)s "
                "ORDER BY year_count DESC, t.id",
                {**board_params, "n": threshold},
                caliber=f"{base}；至少 {threshold} 个年度（含 {threshold}，边界取等）",
                limit=bounded,
            )
            rows["avg_years_per_task"] = avg["value"]
            rows["min_years"] = threshold
            return rows

        if key == "coverage":
            # EXISTS over the whole task table, not a JOIN over goals: the tasks
            # with no goal row are precisely the answer, and an inner join drops
            # them (the missing_goal_as_zero trap).
            return store.fetch(
                "SELECT COUNT(*) AS total_tasks, "
                "SUM(EXISTS (SELECT 1 FROM task_year_goal g "
                "WHERE g.task_id = t.id AND g.year = %(yr)s)) AS has_goal, "
                "SUM(NOT EXISTS (SELECT 1 FROM task_year_goal g "
                "WHERE g.task_id = t.id AND g.year = %(yr)s)) AS missing_goal, "
                "ROUND(SUM(EXISTS (SELECT 1 FROM task_year_goal g "
                "WHERE g.task_id = t.id AND g.year = %(yr)s)) / COUNT(*) * 100, 1) AS coverage_pct "
                f"FROM task t WHERE {clause}",
                {**board_params, "yr": int(year)},
                caliber=f"{base}；分母为全部正式任务，未设目标的任务计入缺口（不能用 JOIN 丢掉）",
                limit=1,
            )

        if key == "missing":
            # 「在办任务还没定目标」问的是在办那批，已完成/已暂停的没定目标不算缺口。
            # 不加这道过滤会把 status 2/3 的行混进来，集合就对不上了。
            extra = " AND t.status IN (0, 1)" if in_progress_only else ""
            note = (
                "；仅在办任务（status IN (0, 1)，0 未开始同样在办）"
                if in_progress_only
                else "；含全部状态，未按在办过滤"
            )
            total = store.scalar(
                f"SELECT COUNT(*) FROM task t WHERE {clause}{extra} "
                "AND NOT EXISTS (SELECT 1 FROM task_year_goal g "
                "WHERE g.task_id = t.id AND g.year = %(yr)s)",
                {**board_params, "yr": int(year)},
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, t.status, t.project_group "
                f"FROM task t WHERE {clause}{extra} AND NOT EXISTS (SELECT 1 FROM task_year_goal g "
                "WHERE g.task_id = t.id AND g.year = %(yr)s) ORDER BY t.id",
                {**board_params, "yr": int(year)},
                caliber=f"{base}；{int(year)} 年度无目标行{note}；status 0 未开始 / 1 进行中 / 2 已完成 / 3 已暂停",
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            return rows

        if key == "missing_by_group":
            return store.fetch(
                "SELECT t.project_group, COUNT(*) AS missing_count "
                f"FROM task t WHERE {clause} AND NOT EXISTS (SELECT 1 FROM task_year_goal g "
                "WHERE g.task_id = t.id AND g.year = %(yr)s) "
                "GROUP BY t.project_group ORDER BY missing_count DESC, t.project_group",
                {**board_params, "yr": int(year)},
                caliber=f"{base}；按专项组统计 {int(year)} 年度目标缺口",
                limit=bounded,
            )

        if not year_to:
            return {
                "ok": False,
                "error": {"code": "invalid_argument", "message": "口径 multi_year 需要 year 与 year_to"},
            }
        params = {**board_params, "yr1": int(year), "yr2": int(year_to)}
        both = store.scalar(
            "SELECT COUNT(*) AS tasks FROM (SELECT g.task_id "
            f"FROM task_year_goal g JOIN task t ON t.id = g.task_id WHERE {clause} "
            "AND g.year IN (%(yr1)s, %(yr2)s) "
            "GROUP BY g.task_id HAVING COUNT(DISTINCT g.year) = 2) x",
            params,
            caliber=f"{base}；两个年度都设了目标",
        )
        rows = store.fetch(
            "SELECT t.id AS task_id, t.task_name, "
            "MAX(CASE WHEN g.year = %(yr1)s THEN g.current_year_goal END) AS goal_year_1, "
            "MAX(CASE WHEN g.year = %(yr2)s THEN g.current_year_goal END) AS goal_year_2 "
            f"FROM task_year_goal g JOIN task t ON t.id = g.task_id WHERE {clause} "
            "AND g.year IN (%(yr1)s, %(yr2)s) GROUP BY t.id, t.task_name "
            "HAVING goal_year_1 IS NOT NULL AND goal_year_2 IS NOT NULL ORDER BY t.id",
            params,
            caliber=f"{base}；{int(year)} 与 {int(year_to)} 两年对照",
            limit=bounded,
        )
        rows["tasks_in_both_years"] = both["value"]
        rows["years"] = [int(year), int(year_to)]
        return rows

    return _guard("weekly_year_goal_stats", work)


@mcp.tool()
def weekly_milestone_stats(
    scope: str = "summary",
    by: str = "category",
    year: int = 0,
    category: str = "",
    min_total: int = 0,
    kind: str = "task_done_milestones_open",
    top: int = 8,
) -> str:
    """Aggregate milestone completion. weekly_milestone_query only lists rows.

    ``status`` is 0 未完成 / 1 已完成 -- a two-value code, so "completed" means
    ``status = 1`` and never a text match.

    Args:
        scope: ``summary`` (totals and finish rate) / ``by_dimension`` (grouped by
            ``by``) / ``deleted`` (soft-delete audit, the one place deleted rows
            are counted) / ``fully_deleted`` (the tasks whose milestones were ALL
            soft-deleted -- 3 of them, judged by NOT EXISTS on the surviving rows,
            not by "has a deleted row", which spans 23) / ``per_task`` (counts per
            task, zero-milestone tasks kept, with ``top_tie_count`` because the
            top bucket is a 23-way tie at 6) / ``mismatch`` (task status vs
            milestone status disagreements).
        by: Dimension for ``by_dimension``: year / category / group_name /
            project_group / status / task_status / primary_category / reporter_id
            / owner_id.
            reporter_id answers "里程碑都是谁报的 / 各几条" (47 people); owner_id is
            the responsible party, a different column and a different question.
            group_name is the milestone row's own short label (区域组/安全组/… six
            of them); project_group is the task's 项目组 (关键技术攻关组/算力网络组/
            国家工程办 … eleven). "哪些项目组的里程碑完成比例高/低" means the latter.
        year: Restrict to one milestone year; 0 covers all.
        category: Restrict to one milestone category.
        min_total: For ``by_dimension``, drop buckets below this count. Inclusive.
        kind: For ``mismatch``: ``task_done_milestones_open`` (task marked done
            with milestones still open) or ``milestones_done_task_open``.
        top: Row cap for the listing scopes.
    """

    def work() -> dict[str, Any]:
        key = (scope or "summary").strip().lower()
        if key not in _MILESTONE_STATS_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(_MILESTONE_STATS_SCOPES)}",
                },
            }
        bounded = max(1, min(store.MAX_ROWS, int(top)))
        clause = store.formal_task_clause()
        status_note = "m.status 为 0/1 两值码：1 已完成、0 未完成"

        # R-17: milestones must be re-checked against the formal-task caliber, and
        # the milestone row's own soft-delete flag is separate from the task's.
        where = [clause, "m.is_deleted = 0"]
        params: dict[str, Any] = {}
        caliber = [f"m.is_deleted = 0 且关联任务满足 {store.FORMAL_TASK_CALIBER}（R-17）", status_note]
        if year:
            params["yr"] = int(year)
            where.append("m.year = %(yr)s")
            caliber.append(f"仅 {int(year)} 年度里程碑")
        if category.strip():
            params["cat"] = category.strip()
            where.append("m.category = %(cat)s")
            caliber.append(f"仅类别「{category.strip()}」")
        active = " AND ".join(where)

        if key == "deleted":
            # The only scope that counts deleted rows, and it deliberately does
            # not apply the formal-task gate: "how many were soft-deleted" is a
            # question about the table, and filtering by task would undercount.
            return store.fetch(
                "SELECT SUM(m.is_deleted = 0) AS active, SUM(m.is_deleted = 1) AS deleted, "
                "COUNT(*) AS total_rows FROM task_milestone m",
                caliber=(
                    "全表口径（不加任务闸门）：这是关于表的问题，按任务过滤会少算；"
                    "问「哪些任务的里程碑被全部删掉了」用 scope=fully_deleted，"
                    "这三个数答不了那个问题（有删的任务共 23 条，全删的只有 3 条）"
                ),
                limit=1,
            )

        if key == "fully_deleted":
            # 「有没有任务的里程碑被全部删掉了」：deleted 只给全表 566/36/602 三个数，
            # 逐任务清单里被删的行又根本不出现（各处都带 m.is_deleted = 0），所以这
            # 问题此前无路可走——基线答的「无法确认」是照实说，不是模型偷懒。
            # 「全删」必须是 NOT EXISTS 未删行，而不是「有删过行」：有删的 23 条里
            # 只有 3 条是删干净的，混起来差一个量级。
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, COUNT(*) AS deleted_milestones "
                "FROM task_milestone m JOIN task t ON t.id = m.task_id "
                f"WHERE {clause} AND m.is_deleted = 1 "
                "AND NOT EXISTS (SELECT 1 FROM task_milestone m2 "
                "WHERE m2.task_id = t.id AND m2.is_deleted = 0) "
                "GROUP BY t.id, t.task_name ORDER BY t.id",
                caliber=(
                    f"{store.FORMAL_TASK_CALIBER}；「全部删掉」按 NOT EXISTS 未删里程碑判，"
                    "不是「删过里程碑」——删过的任务有 23 条，删干净的只有这 3 条；"
                    "deleted_milestones 是该任务被删的里程碑数；"
                    "行数为 0 才是「没有任务被全删」，这是结论本身，不要换口径重算"
                ),
                limit=bounded,
            )

        if key == "summary":
            return store.fetch(
                "SELECT COUNT(*) AS total, SUM(m.status = 1) AS finished, "
                "SUM(m.status = 0) AS unfinished, "
                "ROUND(SUM(m.status = 1) / COUNT(*) * 100, 1) AS finish_rate_pct "
                f"FROM task_milestone m JOIN task t ON t.id = m.task_id WHERE {active}",
                params,
                caliber="；".join(caliber),
                limit=1,
            )

        if key == "by_dimension":
            dimension = (by or "category").strip().lower()
            if dimension not in _MILESTONE_DIMENSIONS:
                return {
                    "ok": False,
                    "error": {
                        "code": "unsupported_group_by",
                        "message": f"不支持的维度：{by}；支持 {', '.join(sorted(_MILESTONE_DIMENSIONS))}",
                    },
                }
            # 一级分类不在里程碑行上：m.category 是里程碑自己的类别文本，任务的
            # 分类挂在 t.category_id 且只到二级，一级要再往上跳一层 parent_id。
            # 两者名字像但根本不是一回事——按 m.category 分组得到「国家任务 58.9%」，
            # 按一级分类分组首行是「改革与治理 67.5%」，答「哪个一级分类最高」只有
            # 后者算得对。两层 JOIN 各自带 is_deleted = 0。
            joins = ""
            order = "total DESC, bucket"
            if dimension == "primary_category":
                column = "pc.name"
                joins = (
                    " JOIN task_category c ON c.id = t.category_id AND c.is_deleted = 0"
                    " JOIN task_category pc ON pc.id = c.parent_id AND pc.is_deleted = 0"
                )
                # 问的是「完成率最高」，按完成率排序，并列按分类名定序。
                order = "finish_rate_pct DESC, bucket"
            elif dimension == "project_group":
                # 项目组挂在任务上，不在里程碑行上。问「比例高/低」同样按比率排序，
                # 首行即最高、末行即最低，不必让模型自己在结果里挑。
                column = "IFNULL(NULLIF(TRIM(t.project_group), ''), '(未填)')"
                order = "finish_rate_pct DESC, bucket"
            elif dimension == "task_status":
                column = "t.status"
            elif dimension == "board":
                # 看板在任务上，里程碑行没有 board_id：JOIN 回 task_board 取名字。
                column = "b.name"
                joins = " JOIN task_board b ON b.id = t.board_id AND b.is_deleted = 0"
            else:
                column = f"m.{dimension}"
            having = ""
            if min_total:
                params["min_total"] = max(1, int(min_total))
                having = "HAVING total >= %(min_total)s "
                caliber.append(f"仅保留计数不少于 {max(1, int(min_total))} 的分组（边界取等）")
            return store.fetch(
                f"SELECT {column} AS bucket, COUNT(*) AS total, SUM(m.status = 1) AS finished, "
                "ROUND(SUM(m.status = 1) / COUNT(*) * 100, 1) AS finish_rate_pct "
                f"FROM task_milestone m JOIN task t ON t.id = m.task_id{joins} WHERE {active} "
                f"GROUP BY bucket {having}ORDER BY {order}",
                params,
                caliber="；".join(caliber)
                + f"；按{_MILESTONE_DIMENSIONS[dimension]}分组"
                + (
                    "；一级分类取 t.category_id 的父级（任务分类只到二级，往上跳一层），"
                    "与里程碑自己的 m.category 文本不是同一个维度——"
                    "问「哪个一级分类完成率最高」必须用这个轴，用 by=category 会答成里程碑类别；"
                    "本轴按 finish_rate_pct 降序，首行即最高（改革与治理 67.5%%）；"
                    "小样本分类会把比率抬高，要设门槛请加 min_total"
                    if dimension == "primary_category"
                    else ""
                )
                + (
                    "；项目组取 t.project_group（任务上的列），与 by=group_name 的"
                    "里程碑承担组短名不是一个轴，取值集合都不一样（这里是 11 个项目组，"
                    "那里是 区域组/安全组 等 6 个短名）——问「哪些项目组的里程碑完成比例"
                    "高／低」用本轴；本轴按 finish_rate_pct 降序，首行最高、末行最低；"
                    "这只是填报状态，不能当项目组绩效"
                    if dimension == "project_group"
                    else ""
                ),
                limit=bounded,
            )

        if key == "per_task":
            # year/category 此前在本 scope 里被静默丢掉:SQL 只用了 clause,没用带
            # 年度条件的 active,于是 year=2026 照收不误却毫无作用,「多少任务配了
            # 2026 里程碑」拿到的是全年度 474 条而不是 273 条。
            #
            # 补的时候年度条件必须挂在 LEFT JOIN 的 ON 上,不能进 WHERE:进 WHERE 会
            # 把「没有 2026 里程碑」的那 16 条任务整行删掉,而这恰好就是问句要数的
            # 那部分,分母同时从 128 缩到 112,覆盖率永远算成 100%。挂 ON 上则保留
            # 它们:128 项里 16 项没配,112 项配了,即 87.5%。
            join_extra = ""
            span = []
            if year:
                join_extra += " AND m.year = %(yr)s"
                span.append(f"仅计 {int(year)} 年度里程碑（年度条件在 LEFT JOIN 上，未配该年度的任务保留为 0）")
            if category.strip():
                join_extra += " AND m.category = %(cat)s"
                span.append(f"仅计类别「{category.strip()}」")
            join = f"LEFT JOIN task_milestone m ON m.task_id = t.id AND m.is_deleted = 0{join_extra}"
            span_text = ("；" + "；".join(span)) if span else ""
            summary = store.fetch(
                "SELECT COUNT(DISTINCT t.id) AS tasks, COUNT(m.id) AS milestones, "
                "ROUND(COUNT(m.id) / COUNT(DISTINCT t.id), 2) AS avg_per_task, "
                "SUM(m.id IS NULL) AS tasks_without_milestone, "
                "COUNT(DISTINCT CASE WHEN m.id IS NOT NULL THEN t.id END) AS tasks_with_milestone, "
                "ROUND(COUNT(DISTINCT CASE WHEN m.id IS NOT NULL THEN t.id END) / "
                "COUNT(DISTINCT t.id) * 100, 1) AS coverage_pct "
                f"FROM task t {join} WHERE {clause}",
                params,
                caliber=f"{store.FORMAL_TASK_CALIBER}；分母为全部正式任务（含零里程碑任务）"
                + span_text
                + "；coverage_pct 为「至少配了一条」的任务占比，直接引用，不要自己拿两个数去除",
                limit=1,
            )
            # LEFT JOIN so the three tasks with no milestone stay visible: H5-01
            # asks for exactly those, and an inner join answers a different question.
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, t.status AS task_status, "
                "COUNT(m.id) AS milestones, SUM(m.status = 1) AS finished "
                f"FROM task t {join} "
                f"WHERE {clause} GROUP BY t.id, t.task_name, t.status "
                "ORDER BY milestones DESC, t.id",
                params,
                caliber=f"{store.FORMAL_TASK_CALIBER}；LEFT JOIN 保留零里程碑任务（R-08）；{status_note}" + span_text,
                limit=bounded,
            )
            # 「里程碑最多的任务是哪条」榜首是 23 条并列（都是 6 个）。只回榜单时
            # 模型看到前几行同为 6 就把并列全铺开，读起来成了 23 个独立答案。
            # 并列数交给服务端数，取舍写进口径。
            tied = store.scalar(
                "SELECT COUNT(*) FROM (SELECT t.id, COUNT(m.id) AS n FROM task t "
                f"{join} WHERE {clause} GROUP BY t.id) r "
                "WHERE r.n = (SELECT MAX(r2.n) FROM "
                f"(SELECT COUNT(m.id) AS n FROM task t {join} "
                f"WHERE {clause} GROUP BY t.id) r2)",
                params,
            )
            rows["summary"] = summary["rows"][0] if summary["rows"] else {}
            rows["top_tie_count"] = tied["value"]
            # 并列档的条数、每条几个、首行是哪条任务，全从本次结果里取，别写死：
            # 加了年度过滤后这三个数都会变（全年度 23 条并列各 6 个、首行任务 8；
            # 限 2026 是 4 条并列各 6 个、首行任务 52；限 2025 是 2 条并列各 5 个）。
            # 写死会让口径句自己变成错的那一句。
            head = (rows.get("rows") or [{}])[0]
            top_n = head.get("milestones")
            top_task = f"任务 {head.get('task_id')} {head.get('task_name')}" if head else "首行"
            rows["caliber"] += (
                "；按里程碑数降序、并列按 task id 升序（与其他榜单同一套定序键）；"
                f"最多那档有 {tied['value']} 条任务并列（各 {top_n} 个），"
                f"问「最多的是哪条」取首行一条（{top_task}），"
                "要把并列都报出来请说明是并列，不要当成几十个独立答案"
            )
            return rows

        mismatch = (kind or "task_done_milestones_open").strip().lower()
        if mismatch not in _MILESTONE_MISMATCH_KINDS:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_kind",
                    "message": f"不支持的比对：{kind}；支持 {', '.join(_MILESTONE_MISMATCH_KINDS)}",
                },
            }
        if mismatch == "task_done_milestones_open":
            extra, having, label = "t.status = 2", "SUM(m.status = 1) < COUNT(*)", "任务已完成但里程碑未全完成"
        else:
            extra, having, label = "t.status = 1", "SUM(m.status = 1) = COUNT(*)", "里程碑全完成但任务仍在办"
        # year 在这两个比对里不是「筛掉几行」，而是换了一道题，两个 kind 还反着走：
        # 「已完成但有未完成里程碑」是存在量词，限年度只会漏掉矛盾——不限是 6 项，
        # 限 2026 是 3 项，少掉的 50/111/126 未完成里程碑落在 2025，那是更硬的矛盾，
        # 不该被过滤掉；「里程碑全完成但任务在办」是全称量词，限年度反而更容易满足
        # ——不限是 8 项，限 2026 是 22 项，多出来的那些还有 2025 年里程碑没完成。
        # 所以两个数都对，但答的是不同的话，口径里必须写清是哪一个。
        # 限了年度的话上面 caliber 里已经写过「仅 N 年度里程碑」，别重复一遍。
        span_note = "" if year else "比对该任务的全部年度里程碑（2025 与 2026）"
        quantifier = (
            "存在量词（有任一里程碑未完成即入选）：限定年度只会漏掉矛盾，"
            "跨年度的未完成里程碑是更硬的矛盾，默认不限年度的 6 项才是全量"
            if mismatch == "task_done_milestones_open"
            else "全称量词（全部里程碑都已完成才入选）：限定年度会放宽条件而非收紧，"
            "限 2026 得 22 项、不限得 8 项，多出的那些尚有 2025 年里程碑未完成，"
            "答「进行中但里程碑都完成了」要说明是哪一种"
        )
        return store.fetch(
            "SELECT t.id AS task_id, t.task_name, t.status AS task_status, "
            "COUNT(*) AS milestones, SUM(m.status = 1) AS finished_milestones "
            f"FROM task_milestone m JOIN task t ON t.id = m.task_id WHERE {active} AND {extra} "
            f"GROUP BY t.id, t.task_name, t.status HAVING {having} ORDER BY t.id",
            params,
            caliber="；".join(
                [
                    *caliber,
                    f"{label}（task.status 2 已完成 / 1 进行中）",
                    *([span_note] if span_note else []),
                    quantifier,
                ]
            ),
            limit=bounded,
        )

    return _guard("weekly_milestone_stats", work)


@mcp.tool()
def weekly_group_detail_query(
    task: str = "",
    fields: str = "",
    contains: str = "",
    field: str = "",
    status: str = "",
    non_empty: str = "",
    order_by: str = "",
    limit: int = 200,
) -> str:
    """Query the 集团组 board's own detail table (target/measures/owners/completion text).

    The group board keeps 目标成果 / 实施举措 / 进度成效 / 完成时间 and its
    multi-value owner columns in ``task_group_detail``, which no other tool
    reaches: ``weekly_task_query`` returns the shared ``task`` columns and simply
    has none of these. Use this for any 集团看板 question about those fields.

    Args:
        task: Task id or name to narrow to one task. Empty covers the whole board.
        fields: Comma-separated columns to return; empty returns the common set.
            Call with an unsupported name to see the supported list.
        contains: Substring filter applied to ``field``. Use for "which tasks are
            due in 2026" -- ``completion_time`` is display text, so this is a text
            match, never date arithmetic (R-12).
        field: Which column ``contains`` filters on. Required when ``contains`` is set.
        status: Business status 0/1/2/3 on the task side. Needed for "状态与成效
            描述矛盾的任务" -- the contradiction is 未开始 (0) next to a written
            effect, and the status lives on ``task`` while the effect lives here,
            so neither table alone can express it.
        non_empty: Comma-separated columns required to be non-empty. Pair with
            ``status`` for the contradiction question: without it, tasks that are
            未开始 *and* blank come along and inflate the count.
        order_by: ``progress_time`` orders by the task's latest progress newest
            first, which is what "当期进度成效" means. The DEFAULT is already this
            order, so a plain "给我前 5 条" returns the current ones -- pass the
            parameter only to make the caliber explicit.
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        requested = [f.strip() for f in (fields or "").split(",") if f.strip()]
        unknown = [f for f in requested if f not in _GROUP_DETAIL_FIELDS]
        if unknown:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_field",
                    "message": f"不支持的字段：{', '.join(unknown)}；支持 {', '.join(sorted(_GROUP_DETAIL_FIELDS))}",
                },
            }
        selected = requested or [
            "target_result",
            "implementation_measure",
            "progress_effect",
            "completion_time",
        ]

        params: dict[str, Any] = {}
        where = [store.formal_task_clause()]
        caliber = [store.FORMAL_TASK_CALIBER, "集团看板（task_board.code = 'group'）"]

        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            params["tid"] = task_id
            where.append("d.task_id = %(tid)s")

        needle = (contains or "").strip()
        if needle:
            column = (field or "").strip()
            if column not in _GROUP_DETAIL_FIELDS:
                return {
                    "ok": False,
                    "error": {
                        "code": "unsupported_field",
                        "message": f"contains 需要指定 field；不支持 {column!r}，"
                        f"支持 {', '.join(sorted(_GROUP_DETAIL_FIELDS))}",
                    },
                }
            params["needle"] = f"%{needle}%"
            where.append(f"d.{column} LIKE %(needle)s")
            caliber.append(f"{column} 含「{needle}」（文本匹配，非日期运算）")
            if column not in selected:
                selected.append(column)
            # 「要求 2026 年内完成的任务」只能按年份数字扫这一列：completion_time
            # 是自由文本，46 条里有 28 种写法（2026年内 / 2026年底前 / 2026Q4 /
            # 2026-12-30 / 2026年9月30日 …）。拿「2026年内」当检索词只命中字面
            # 相同的 5 条，剩下 26 条同样是 2026 年到期却被漏掉（Q4-03）。
            if column == "completion_time":
                year = re.search(r"20\d{2}", needle)
                if year and needle == year.group():
                    caliber.append(
                        f"本次按年份数字「{needle}」扫全部写法，这是唯一可靠的年份过滤方式："
                        "completion_time 是自由文本，本看板 46 条里有 28 种写法"
                        "（2026年内 / 2026年底前 / 2026Q4 / 2026-12-30 / 2026年9月30日 等），"
                        f"用「{needle}年内」这类完整表述当检索词只能命中字面相同的少数几条，"
                        "会漏掉同年到期但写法不同的任务；写法分档另见 "
                        "weekly_group_stats scope=completion_time_formats，"
                        "去重取值见 scope=completion_time_values；"
                        "另外「持续推进」这类不含年份的写法本次一律不在结果里，"
                        "它们既不算命中也不代表该年不到期"
                    )
                elif year:
                    # 检索词比裸年份长（「2026年内」「2026年底前」…）时，本次结果
                    # 只是该写法的字面命中，不是「该年到期的任务」。这一路必须自己
                    # 报出全年真数，否则模型拿 5 条当 31 条答完就走（Q4-03）。
                    total_year = store.scalar(
                        "SELECT COUNT(*) FROM task_group_detail d JOIN task t ON t.id = d.task_id "
                        f"{store.group_board_join()} "
                        f"WHERE {store.formal_task_clause()} AND d.completion_time LIKE %(y)s",
                        {"y": f"%{year.group()}%"},
                    )
                    caliber.append(
                        f"注意：本次只命中字面含「{needle}」的写法，不等于「{year.group()}年到期的任务」。"
                        "completion_time 是自由文本，本看板 46 条里有 28 种写法"
                        f"（{year.group()}年内 / {year.group()}年底前 / {year.group()}Q4 / "
                        f"{year.group()}-12-30 等），"
                        f"按年份数字「{year.group()}」扫共 {total_year['value']} 条；"
                        f"问「哪些任务要求 {year.group()} 年内完成」请改用 contains={year.group()}，"
                        "不要用完整表述当检索词"
                    )

        if status.strip():
            if status.strip() not in {"0", "1", "2", "3"}:
                return {
                    "ok": False,
                    "error": {"code": "invalid_status", "message": "status 只能是 0/1/2/3"},
                }
            params["st"] = int(status.strip())
            where.append("t.status = %(st)s")
            caliber.append(
                f"任务业务状态 = {status.strip()}"
                "（0 未开始 / 1 进行中 / 2 已完成 / 3 已停用，与审批流转状态 workflow_status 不是一回事）"
            )

        required = [f.strip() for f in (non_empty or "").split(",") if f.strip()]
        bad = [f for f in required if f not in _GROUP_DETAIL_FIELDS]
        if bad:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_field",
                    "message": f"non_empty 不支持：{', '.join(bad)}；支持 {', '.join(sorted(_GROUP_DETAIL_FIELDS))}",
                },
            }
        for column in required:
            where.append(f"d.{column} IS NOT NULL AND d.{column} <> ''")
            caliber.append(f"{column} 非空")
            if column not in selected:
                selected.append(column)

        columns = ", ".join(f"d.{name}" for name in selected)
        if "completion_time" in selected:
            caliber.append("completion_time 为展示文本，不可做日期运算（R-12）")
        # 多值负责人栏一并回人数：问「有几位项目责任人」时，模型按逗号自己数
        # 会漏掉顿号那几行；两种分隔符都要扣。同时点明这一列与 task 上的单值
        # 同名列不是一个东西——集团看板 46 条任务两列的值并不一致。
        for name in ("lead_owner_names", "project_owner_names"):
            if name in selected:
                columns += (
                    f", CHAR_LENGTH(d.{name}) - CHAR_LENGTH(REPLACE(REPLACE(d.{name}, '、', ''), ',', '')) + 1 "
                    f"AS {name.replace('_names', '')}_count"
                )
                caliber.append(
                    f"{name} 的人数已算好（分隔符个数 + 1，顿号与逗号都计入），不要自己按逗号数；"
                    f"这一列是集团看板的多值口径，与 task 表上单值的 {name.replace('_names', '_name')} "
                    "并非同一个数据（46 条任务两列的值不一致），问集团看板的负责人一律用本列"
                )

        # 「当期进度成效」要的是最近报过的那几条。默认排序直接落在最新进展时间上：
        # 问句没给排序词时，模型拿到的第一页就是当期，不会把看板最早那批（97 起）
        # 当答案；顺带把 latest_progress_time 一并选出来，让「凭什么是这 5 条」可核。
        order_key = (order_by or "").strip().lower()
        if order_key and order_key != "progress_time":
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_order_by",
                    "message": f"不支持的排序：{order_by}；目前只支持 progress_time",
                },
            }
        if order_key == "progress_time":
            columns += ", t.latest_progress_time"
        order_sql = "t.latest_progress_time DESC, d.task_id DESC"
        caliber.append(
            "默认按任务最新进展时间 latest_progress_time 倒序（并列按任务 id 倒序），"
            "首行即最近报过的；集团看板 46 条该列都非空，所以排序不会把空值顶到前面"
        )

        clause = " AND ".join(where)
        total = store.scalar(
            "SELECT COUNT(*) FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            f"{store.group_board_join()} WHERE {clause}",
            params,
        )
        rows = store.fetch(
            f"SELECT d.task_id, t.task_name, t.status, {columns} "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            f"{store.group_board_join()} "
            f"WHERE {clause} ORDER BY {order_sql}",
            params,
            caliber="；".join([*caliber, "total_count 为符合条件的任务总数，判断列全看它而非返回行数"]),
            limit=limit,
        )
        rows["total_count"] = total["value"]
        return rows

    return _guard("weekly_group_detail_query", work)


@mcp.tool()
def weekly_group_owner_query(person: str = "", role: str = "lead", limit: int = 200) -> str:
    """Find 集团组 tasks by owner, or list the board's owner columns.

    The group board's owners are comma-separated multi-value text, so a plain
    ``LIKE '%name%'`` collides across people (the multivalue_like_collision
    trap). Matching goes through ``FIND_IN_SET`` on the id column instead, which
    is exact per element.

    Args:
        person: Person id or name. Empty lists every task's owners for the role.
        role: ``lead`` (牵头人) or ``project`` (项目负责人). These are different
            roles over different columns and are not interchangeable.
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        key = (role or "lead").strip().lower()
        if key not in _GROUP_OWNER_ROLES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_role",
                    "message": f"不支持的角色：{role}；支持 {', '.join(sorted(_GROUP_OWNER_ROLES))}",
                },
            }
        id_column, name_column = _GROUP_OWNER_ROLES[key]
        label = "牵头人" if key == "lead" else "项目负责人"

        params: dict[str, Any] = {}
        where = [store.formal_task_clause()]
        caliber = [store.FORMAL_TASK_CALIBER, f"集团看板 {label}（{id_column}）"]

        token = (person or "").strip()
        if token:
            # The question may name a person while the column stores ids, so try
            # both: FIND_IN_SET is exact per comma element either way, which is
            # what keeps 唐立本 from matching a longer name containing it.
            params["who"] = token
            where.append(f"(FIND_IN_SET(%(who)s, d.{id_column}) > 0 OR FIND_IN_SET(%(who)s, d.{name_column}) > 0)")
            caliber.append(f"FIND_IN_SET 精确匹配「{token}」（逗号多值，不用 LIKE 以免跨人误命中）")
            # 本工具与 weekly_task_query owner= 是两个不同的总体，不是同一答案的
            # 两半：这里查的是集团看板明细表的多值牵头人列，那里查的是 task 上的
            # lead_owner_name 单值列。把两边的行并起来既会多出只在明细表里挂名的
            # 任务，又会漏掉 task 上牵头、明细表里没列名的任务（O6-01 就是这么
            # 一次多两条、少一条的）。
            caliber.append(
                "本档只覆盖集团看板明细表的多值牵头人列，"
                "与 weekly_task_query owner= 的 task.lead_owner_name 单值列是两个不同总体，"
                "两边行数不同是正常的，不要把两边结果并起来当一个答案；"
                f"问「{token}负责哪些任务」按 task 表口径答，请用 weekly_task_query owner="
            )
        else:
            where.append(f"d.{id_column} <> ''")
            caliber.append("仅列出该角色非空的任务")
            # 不带人名时本工具是「按挂名人数排的榜」，不是看板花名册：定序键是
            # owner_count DESC，带 limit 一截就落在人多的那批任务上，与按 task_id
            # 顺序列的前 N 条完全是两批（Q3-01 那次 8 条一条都没对上）。而且这里
            # 一次只带一个角色列，「牵头人和项目负责人分别是谁」在本工具里天生
            # 答不全。两件事都写进口径，并把出口指出来。
            caliber.append(
                "本档按该角色挂名人数倒序（owner_count DESC），是「谁挂名最多」的榜，"
                "不是看板花名册；带 limit 截出来的前 N 条不等于按任务顺序的前 N 条；"
                "本工具一次只带一个角色列，"
                "问「各任务的牵头人和项目负责人分别是谁」要两列同时出、且按 task_id 顺序，"
                "请用 weekly_group_detail_query fields=lead_owner_names,project_owner_names"
            )

        return store.fetch(
            f"SELECT d.task_id, t.task_name, d.{name_column}, d.{id_column}, "
            f"LENGTH(d.{id_column}) - LENGTH(REPLACE(d.{id_column}, ',', '')) + 1 AS owner_count "
            "FROM task_group_detail d JOIN task t ON t.id = d.task_id "
            f"{store.group_board_join()} "
            f"WHERE {' AND '.join(where)} ORDER BY owner_count DESC, d.task_id",
            params,
            caliber="；".join(caliber),
            limit=limit,
        )

    return _guard("weekly_group_owner_query", work)


@mcp.tool()
def weekly_group_history(
    task: str = "",
    version_no: int = 0,
    by: str = "",
    latest_only: bool = False,
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    last_months: int = 0,
    limit: int = 200,
) -> str:
    """Query the 集团组 board's progress history (its own table, not task_progress).

    The group board's progress lives in ``task_group_progress_history`` -- 362
    published rows -- while ``task_progress`` holds none of it. So
    ``weekly_progress_history`` and ``weekly_progress_range`` both return empty
    for group tasks, and this is the entry point for them.

    Two gates apply together: the task must be formal (R-01) and the row itself
    must have ``is_published = 1``. Dropping either folds 42 un-approved drafts in.

    Args:
        task: Task id or name. Empty covers the whole board.
        version_no: Return one specific version (per-task, larger is newer).
        by: Empty lists rows; ``year`` / ``month`` / ``quarter`` / ``task`` /
            ``reporter`` returns counts per group. ``lag`` ranks tasks by how
            many days since their last report -- use it for "谁最久没报".
            ``linkage`` counts how many rows carry a workflow_submission_id, over
            all 404 rows rather than the 362 published ones, since the question is
            a fill rate.
        latest_only: Keep only each task's newest published version.
        date_from: Inclusive start on ``report_time``, YYYY-MM-DD.
        date_to: Inclusive end on ``report_time``, YYYY-MM-DD.
        last_days: Window of N days ending at the snapshot date, not today.
        last_months: Window of N calendar months ending at the snapshot date.
            "最近三个月" means this, not ``last_days=90``: three months back from
            2026-08-15 is 2026-05-15, while 90 days lands on 2026-05-17 and drops
            three May rows. Do not substitute one for the other.
        limit: Max rows, capped at 200.
    """

    def work() -> dict[str, Any]:
        grouping = (by or "").strip().lower()
        if grouping and grouping not in _GROUP_HISTORY_GROUPINGS:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_group_by",
                    "message": f"不支持的分组：{by}；支持 {', '.join(sorted(_GROUP_HISTORY_GROUPINGS))}",
                },
            }

        params: dict[str, Any] = {}
        where = [store.group_history_gate()]
        caliber = [store.GROUP_HISTORY_CALIBER]

        if task.strip():
            task_id = store.resolve_task_id(task)
            if task_id is None:
                return _task_miss(task)
            params["tid"] = task_id
            where.append("h.task_id = %(tid)s")
        else:
            # Board scoping is only needed for whole-board queries: a task filter
            # already pins the board, and the extra join would be dead weight.
            where.append(f"b.code = '{store.GROUP_BOARD_CODE}' AND b.is_deleted = 0")

        if version_no:
            params["vno"] = int(version_no)
            where.append("h.version_no = %(vno)s")
            caliber.append(f"仅第 {int(version_no)} 期")

        if last_months and last_days:
            return {
                "ok": False,
                "error": {
                    "code": "invalid_argument",
                    "message": "last_days 与 last_months 只能给一个：两者边界不同，同时给会得出第三个窗口",
                },
            }
        if last_months:
            lo, hi = store.month_window(last_months)
            if date_from.strip():
                lo = date_from.strip()
            if date_to.strip():
                hi = date_to.strip()
        else:
            lo, hi = store.date_window(date_from, date_to, last_days or None)
        window = store.window_clause("h.report_time", lo, hi, params)
        if window:
            # report_time is a datetime and the bound end is a date, so a naive
            # `<=` would cut the final day off at 00:00. Compare on the date part.
            window = store.window_clause("DATE(h.report_time)", lo, hi, params)
            where.append(window)
            caliber.append(store.window_caliber(lo, hi, label="上报时间"))
            if last_days or last_months:
                caliber.append(store.as_of_caliber())
            if last_months:
                caliber.append(f"最近 {int(last_months)} 个月按自然月回溯（非 {int(last_months) * 30} 天）")

        if latest_only:
            where.append(
                "h.version_no = (SELECT MAX(x.version_no) FROM task_group_progress_history x "
                "WHERE x.task_id = h.task_id AND x.is_published = 1)"
            )
            caliber.append("仅各任务最新一期已发布版本")

        joins = f"JOIN task t ON t.id = h.task_id {store.group_board_join()}"
        clause = " AND ".join(where)

        if not grouping:
            totals = store.fetch(
                "SELECT COUNT(*) AS total_rows, COUNT(DISTINCT h.task_id) AS total_tasks "
                f"FROM task_group_progress_history h {joins} WHERE {clause}",
                params,
                caliber="；".join(caliber),
                limit=1,
            )
            rows = store.fetch(
                "SELECT h.task_id, t.task_name, h.version_no, h.progress_effect, "
                "h.completion_time, h.reporter_id, h.report_time "
                f"FROM task_group_progress_history h {joins} WHERE {clause} "
                "ORDER BY h.task_id, h.version_no DESC, h.id DESC",
                params,
                caliber="；".join(caliber),
                limit=limit,
            )
            first = totals["rows"][0] if totals["rows"] else {}
            # Counting questions must survive the 200-row cap: the board has 362
            # published rows, so a caller seeing 200 + has_more cannot recover it.
            rows["total_count"] = first.get("total_rows")
            rows["total_tasks"] = first.get("total_tasks")
            return rows

        expression, order = _GROUP_HISTORY_GROUPINGS[grouping]

        if grouping == "lag":
            # 滞报天数取 MAX(report_time) 与快照日之差，不是 MIN：问的是「最后
            # 一次报到现在多久」，用最早一期会把老任务全排到榜首。
            # 分母 total_tasks 一并给出：本表只有报过的任务，从未报过的不在这张
            # 榜上，拿行数当「集团组任务数」会少算。
            total = store.scalar(
                f"SELECT COUNT(DISTINCT h.task_id) FROM task_group_progress_history h {joins} WHERE {clause}",
                params,
            )
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, "
                f"DATEDIFF('{store.AS_OF}', MAX(h.report_time)) AS lag_days, "
                "COUNT(*) AS rounds, MAX(h.report_time) AS last_report_time "
                f"FROM task_group_progress_history h {joins} WHERE {clause} "
                "GROUP BY t.id, t.task_name ORDER BY lag_days DESC, t.id",
                params,
                caliber="；".join(
                    [
                        *caliber,
                        f"lag_days = 快照日 {store.AS_OF} 减最后一次上报日（MAX(report_time)，不是最早一期）",
                        "只含报过进展的任务，从未报过的不在榜上；total_tasks 为上榜任务数，勿当作集团组任务总数",
                        "并列按 task id 升序",
                    ]
                ),
                limit=limit,
            )
            rows["total_tasks"] = total["value"]
            return rows

        if grouping == "linkage":
            # 这一档故意不加 is_published 闸门：问的是「有多少行挂上了提交单」，
            # 分母该是表里全部 404 行，用过闸的 362 行会把 42 条草稿的挂接状况
            # 一起丢掉。两个数都回，并写明哪个是哪个。
            bare = " AND ".join(w for w in where if "h.is_published" not in w)
            return store.fetch(
                "SELECT COUNT(*) AS total_rows, "
                "SUM(h.workflow_submission_id IS NOT NULL) AS linked_rows, "
                "SUM(h.workflow_submission_id IS NULL) AS unlinked_rows, "
                "SUM(h.is_published = 1) AS published_rows "
                f"FROM task_group_progress_history h {joins} WHERE {bare}",
                params,
                caliber=(
                    "任务侧 is_deleted = 0 AND workflow_status = 'published'，"
                    "但本档不加历史行的 is_published 闸门：问的是挂接率，"
                    "分母该是表内全部 404 行（过闸的 362 行会漏掉 42 条草稿的挂接状况）；"
                    "linked_rows 按 workflow_submission_id 非空判定，"
                    "0 即这张表整体没有与提交单挂接，不是查不到——"
                    "集团组的成效历史独立于审批提交单，两者没有外键落库"
                ),
                limit=1,
            )

        select = f"{expression} AS bucket, COUNT(*) AS progress_count"
        group_sql = "bucket"
        note = f"；按 {grouping} 分组计数"
        if grouping == "task":
            # 按任务分组时把 task_id 一并选出并纳入 GROUP BY：并列要按 id 定序，
            # 只回任务名的话模型手上没有定序键，也没法与其他榜单对齐。
            select = f"t.id AS task_id, {select}"
            group_sql = "t.id, bucket"
            note += "；并列按 task id 升序（不按任务名排——11 期的有 8 条并列，两种排法给出的前 5 条不是同一批）"
        else:
            select += ", COUNT(DISTINCT h.task_id) AS task_count"
        return store.fetch(
            f"SELECT {select} FROM task_group_progress_history h {joins} "
            f"WHERE {clause} GROUP BY {group_sql} ORDER BY {order}",
            params,
            caliber="；".join(caliber) + note,
            limit=limit,
        )

    return _guard("weekly_group_history", work)


@mcp.tool()
def weekly_group_stats(scope: str = "owners", top: int = 8, min_rounds: int = 0) -> str:
    """Aggregate stats over the 集团组 board that plain listing cannot answer.

    Args:
        scope: ``owners`` (multi vs single lead, distinct leads),
            ``separators`` (how project_owner_names is delimited, single-person
            cells counted as their own bucket),
            ``owner_widths`` (people per project_owner_names cell, widest first),
            ``completion_time`` (ISO vs free text vs blank),
            ``field_lengths`` (target_result char stats),
            ``attachments`` (per-task counts, zero kept -- a listing, so ``top``
            cuts it; pass ``top=46`` for the whole board),
            ``attachment_distribution`` (how many tasks hold 0/1/2/... attachments,
            counted server-side). Use it for "how many tasks have one attachment":
            counting that off the truncated listing gives 21/4/4 where the truth is
            17/3/5.
            ``history_rounds`` (rounds per task, and how many clear ``min_rounds``).
        top: Row cap for the listing scopes.
        min_rounds: For ``history_rounds``, count tasks with at least this many
            published rounds. Inclusive -- "at least 5" means ``>= 5``.
    """

    def work() -> dict[str, Any]:
        key = (scope or "owners").strip().lower()
        if key not in _GROUP_STATS_SCOPES:
            return {
                "ok": False,
                "error": {
                    "code": "unsupported_scope",
                    "message": f"不支持的口径：{scope}；支持 {', '.join(_GROUP_STATS_SCOPES)}",
                },
            }
        bounded = max(1, min(store.MAX_ROWS, int(top)))
        clause = store.formal_task_clause()
        join = store.group_board_join()
        base = f"{store.FORMAL_TASK_CALIBER}；集团看板"

        if key == "project_group_raw":
            # 「集团明细按专项组分，各有多少条」问的是明细表本身有多少行，故不加任务
            # 闸门——与审批表那几档同一个道理（问的是表本身）。两档差得不小：裸表
            # 55 行 / 加闸门 46 行，差的 9 行挂在已软删或未发布的任务上。
            # 这一档是 opt-in，绝不能变成 project_group 类问题的默认路径：问「各专项组
            # 有多少任务」仍走 weekly_aggregate group_by=project_group（那是任务口径）。
            raw_sum = store.scalar("SELECT COUNT(*) FROM task_group_detail d")
            gated_sum = store.scalar(
                f"SELECT COUNT(*) FROM task_group_detail d JOIN task t ON t.id = d.task_id WHERE {clause}"
            )
            raw_rows = store.fetch(
                "SELECT d.project_group AS grp, COUNT(*) AS rows_, "
                "SUM(CASE WHEN EXISTS (SELECT 1 FROM task t2 WHERE t2.id = d.task_id "
                "AND t2.is_deleted = 0 AND t2.workflow_status = 'published') THEN 1 ELSE 0 END) AS formal_rows, "
                "SUM(d.target_result IS NOT NULL AND d.target_result <> '') AS target_filled, "
                "SUM(d.implementation_measure IS NOT NULL AND d.implementation_measure <> '') AS measure_filled "
                "FROM task_group_detail d GROUP BY grp ORDER BY rows_ DESC, grp",
                caliber=(
                    f"集团明细表（task_group_detail）裸表口径，不加任务闸门，本档的答案是 rows_ 那一列，"
                    f"合计 {raw_sum['value']} 行；formal_rows 是同一分组下过正式任务闸门的行数，"
                    f"合计 {gated_sum['value']}，仅供口径对照，不要拿它当本档答案；"
                    "target_filled / measure_filled 同为裸表口径的填写行数；"
                    "问「各专项组有多少任务」不要用本档——那是任务口径，"
                    "请用 weekly_aggregate group_by=project_group"
                ),
                limit=bounded,
            )
            raw_rows["caliber_tiers"] = {
                "raw_table": raw_sum["value"],
                "formal_task_gate": gated_sum["value"],
            }
            return raw_rows

        if key == "owners":
            summary = store.fetch(
                "SELECT COUNT(*) AS tasks, "
                "SUM(d.lead_owner_ids LIKE '%%,%%') AS multi_lead, "
                "SUM(d.lead_owner_ids NOT LIKE '%%,%%' AND d.lead_owner_ids <> '') AS single_lead, "
                "SUM(d.lead_owner_ids = '' OR d.lead_owner_ids IS NULL) AS no_lead "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} WHERE {clause}",
                caliber=f"{base}；牵头人多值按逗号判定",
                limit=1,
            )
            # Splitting the multi-value column needs a row source per position;
            # 4 covers the widest cell in this store (max 2 today, headroom kept).
            distinct = store.scalar(
                "SELECT COUNT(*) AS distinct_leads FROM ("
                "SELECT DISTINCT SUBSTRING_INDEX(SUBSTRING_INDEX(d.lead_owner_ids, ',', n.n), ',', -1) AS uid "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                "JOIN (SELECT 1 n UNION SELECT 2 UNION SELECT 3 UNION SELECT 4) n "
                "ON n.n <= LENGTH(d.lead_owner_ids) - LENGTH(REPLACE(d.lead_owner_ids, ',', '')) + 1 "
                f"WHERE {clause} AND d.lead_owner_ids <> '') x",
                caliber=f"{base}；逐元素拆分后去重计数",
            )
            summary["distinct_leads"] = distinct["value"]
            return summary

        if key == "separators":
            # 多值负责人栏的分隔符是混着填的：半角逗号、顿号、两者并存、以及
            # 只有一个人因此看不出分隔符。分档必须落在服务端，模型按返回行
            # 自己数会把「只有一个人」误判成某种分隔符。
            return store.fetch(
                "SELECT CASE "
                "WHEN d.project_owner_names LIKE '%%、%%' AND d.project_owner_names LIKE '%%,%%' "
                "  THEN '两种并存' "
                "WHEN d.project_owner_names LIKE '%%、%%' THEN '全角顿号' "
                "WHEN d.project_owner_names LIKE '%%,%%' THEN '半角逗号' "
                "ELSE '单人无分隔符' END AS separator_kind, COUNT(*) AS n "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.project_owner_names IS NOT NULL AND d.project_owner_names <> '' "
                "GROUP BY separator_kind ORDER BY n DESC, separator_kind",
                caliber=(
                    f"{base}；按 project_owner_names 里出现的分隔符分档；"
                    "「单人无分隔符」是独立一档不是缺失；仅统计该栏非空的任务"
                ),
                limit=bounded,
            )

        if key == "owner_widths":
            # 人数 = 分隔符个数 + 1，两种分隔符都要扣掉再算，否则顿号那两行会少算。
            return store.fetch(
                "SELECT t.task_name, d.project_owner_names, "
                "CHAR_LENGTH(d.project_owner_names) "
                "- CHAR_LENGTH(REPLACE(REPLACE(d.project_owner_names, '、', ''), ',', '')) "
                "+ 1 AS owner_count "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.project_owner_names IS NOT NULL AND d.project_owner_names <> '' "
                "ORDER BY owner_count DESC, t.id",
                caliber=(f"{base}；owner_count = 分隔符个数 + 1，顿号与逗号都计入；按人数倒序，最多的一条即首行"),
                limit=bounded,
            )

        if key == "completion_time_formats":
            # 「各种写法各有多少条」问的是格式档位，不是去重取值：库里 28 个不同
            # 取值归成 6 档，拿 completion_time_values 的 28 去答会答错一个量级。
            # 判别顺序即优先级，见 _COMPLETION_TIME_FORMAT_CASE 的注释。
            total = store.scalar(
                f"SELECT COUNT(*) FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.completion_time IS NOT NULL AND d.completion_time <> ''",
            )
            rows = store.fetch(
                f"SELECT {_COMPLETION_TIME_FORMAT_CASE} AS fmt, COUNT(*) AS cnt "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.completion_time IS NOT NULL AND d.completion_time <> '' "
                "GROUP BY fmt ORDER BY cnt DESC, fmt",
                caliber=(
                    f"{base}；按写法归档（标准日期 / 季度 / 含「底」的模糊表述 / 中文年月日 / "
                    "中文年月 / 其他），一条只进一档，各档相加等于 total_count；"
                    "档数是写法种类数，不是去重取值数（去重取值另有 completion_time_values）；"
                    "'2026年6月底' 归入含「底」一档而非中文年月，判别按此优先级固定；"
                    "仅统计该栏非空的任务，空值不进任何一档"
                ),
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            return rows

        if key == "completion_time":
            return store.fetch(
                "SELECT COUNT(*) AS tasks, "
                "SUM(d.completion_time REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') AS iso_date, "
                "SUM(d.completion_time IS NOT NULL AND d.completion_time <> '' "
                "AND d.completion_time NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') AS free_text, "
                "SUM(d.completion_time IS NULL OR d.completion_time = '') AS blank "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} WHERE {clause}",
                caliber=f"{base}；completion_time 为展示文本，只做格式判别不做日期运算（R-12）",
                limit=1,
            )

        if key == "completion_time_values":
            # 「都有哪些写法」问的是去重后的取值本身，按文本序排列。让模型翻
            # 46 行明细自己归纳，得到的是它总结出的类别名（「持续推进」这种），
            # 不是库里真实存在的取值。
            total = store.scalar(
                "SELECT COUNT(DISTINCT d.completion_time) "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.completion_time IS NOT NULL AND d.completion_time <> ''",
            )
            rows = store.fetch(
                "SELECT DISTINCT d.completion_time "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.completion_time IS NOT NULL AND d.completion_time <> '' "
                "ORDER BY d.completion_time",
                caliber=(
                    f"{base}；去重后的 completion_time 原样取值，按文本升序；"
                    "这是库里真实存在的写法，不要归纳成自己的类别名；"
                    f"共 {total['value']} 种，top 决定返回前几种"
                ),
                limit=bounded,
            )
            rows["total_count"] = total["value"]
            return rows

        if key == "overdue":
            # 「超过计划完成时间还没完成的」此前无路可走：completion_time 是展示
            # 文本，各处口径都写明不做日期运算，模型照规则答「不可答」是对的。
            # 但 46 条里有 12 条写法可解析（6 个标准日期 + 6 个季度），这一档把
            # 归一化放在服务端，并把不可解析的 34 条单独计数——它们不是「没超期」，
            # 是判不了，两者混起来会把「无法判断」说成「都没超期」。
            # 只看标准日期写法时一条超期都查不到，季度归一化后才露出任务 123
            # （2026Q2 → 2026-06-30，快照日已过 46 天）——季度那 6 条不能不算。
            unparsable = store.scalar(
                "SELECT COUNT(*) "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.completion_time IS NOT NULL AND d.completion_time <> '' "
                f"AND {_COMPLETION_DEADLINE} IS NULL",
            )
            rows = store.fetch(
                f"SELECT t.id AS task_id, t.task_name, t.status, d.completion_time, "
                f"{_COMPLETION_DEADLINE} AS deadline, "
                f"DATEDIFF(%(as_of)s, {_COMPLETION_DEADLINE}) AS days_overdue "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND {_COMPLETION_DEADLINE} < %(as_of)s AND t.status <> 2 "
                f"ORDER BY deadline, t.id",
                {"as_of": store.AS_OF},
                caliber=(
                    f"{base}；超期 = 归一化截止日早于快照日且 status <> 2（未完成）；"
                    f"{store.as_of_caliber()}；"
                    "completion_time 是展示文本，只有标准日期与 YYYYQn 两种写法能归一化"
                    f"（季度取季末日），其余 {unparsable['value']} 条判不了、不在本档，"
                    "它们是「无法判断」而不是「没超期」，报结论时要把这个数一并说明；"
                    "只按标准日期写法看会一条都查不到，季度那几条归一化后才露出来；"
                    "本档行数即可判定的超期任务数，为 0 才是「可判定的那些都没超期」"
                ),
                limit=bounded,
            )
            rows["unparsable_count"] = unparsable["value"]
            return rows

        if key == "status_effect_conflict":
            # 「状态和当期成效描述矛盾」问的是同一行内部对不上：status = 0 未开始，
            # 却在 progress_effect 里写了「已建成 39 个功能模块」这类已发生的成效。
            # 与 effect_consistency 不是一个问题——那个比的是明细表与历史表两处
            # 文本是否一致，两者都写着同样的话也照样是「一致」，答不了自相矛盾。
            return store.fetch(
                "SELECT t.id AS task_id, t.task_name, t.status, "
                "LEFT(d.progress_effect, 50) AS effect_head "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND t.status = 0 "
                "AND d.progress_effect IS NOT NULL AND d.progress_effect <> '' "
                "ORDER BY t.id",
                caliber=(
                    f"{base}；矛盾判据是同一行内部：status = 0（未开始）却填了非空 progress_effect；"
                    "共 6 条；effect_head 是前 50 字，完整文本用 weekly_group_detail_query 取；"
                    "这与 scope=effect_consistency 不是一回事——那档比的是明细表与历史表两处文本是否一致，"
                    "两处写着同一句话也算一致，答不了「状态与成效自相矛盾」"
                ),
                limit=bounded,
            )

        if key == "effect_consistency":
            # 明细表的当前成效 vs 历史表最新一期的成效。逐条比对必须在服务端做：
            # 两段长文本靠模型眼看，46 行里会把不一致的说成一致。
            # 历史侧要 is_published = 1，未发布的那几期不是「最新一期」。
            return store.fetch(
                "SELECT d.task_id, t.task_name, x.version_no, "
                "(d.progress_effect = x.progress_effect) AS same "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                "JOIN (SELECT h.task_id, h.progress_effect, h.version_no, "
                "ROW_NUMBER() OVER (PARTITION BY h.task_id "
                "ORDER BY h.version_no DESC, h.id DESC) rn "
                "FROM task_group_progress_history h WHERE h.is_published = 1) x "
                "ON x.task_id = d.task_id AND x.rn = 1 "
                f"WHERE {clause} ORDER BY same, d.task_id",
                caliber=(
                    f"{base}；明细表 progress_effect 与历史表最新一期（is_published = 1）逐字比对；"
                    "same = 1 一致、0 不一致；不一致的排在最前，"
                    "先看 same = 0 有几行再下「全部一致」的结论"
                ),
                limit=bounded,
            )

        if key == "field_lengths":
            return store.fetch(
                "SELECT COUNT(*) AS tasks, ROUND(AVG(CHAR_LENGTH(d.target_result)), 1) AS avg_chars, "
                "MAX(CHAR_LENGTH(d.target_result)) AS max_chars, "
                "MIN(CHAR_LENGTH(d.target_result)) AS min_chars "
                f"FROM task_group_detail d JOIN task t ON t.id = d.task_id {join} "
                f"WHERE {clause} AND d.target_result IS NOT NULL AND d.target_result <> ''",
                caliber=f"{base}；仅统计 target_result 非空的任务；CHAR_LENGTH 按字符非字节",
                limit=1,
            )

        if key == "attachments":
            summary = store.fetch(
                "SELECT COUNT(*) AS tasks, SUM(NOT EXISTS (SELECT 1 FROM task_attachment a "
                "WHERE a.task_id = t.id AND a.is_deleted = 0)) AS no_attachment "
                f"FROM task t {join} WHERE {clause}",
                caliber=f"{base}；附件按 is_deleted = 0 计有效",
                limit=1,
            )
            # LEFT JOIN, so tasks with zero attachments stay in the listing rather
            # than vanishing -- the inner_join_drops_zero trap, and 18 of 46 tasks
            # here have none, which is usually the point of asking.
            rows = store.fetch(
                "SELECT t.id AS task_id, t.task_name, COUNT(a.id) AS attachments "
                f"FROM task t {join} "
                "LEFT JOIN task_attachment a ON a.task_id = t.id AND a.is_deleted = 0 "
                f"WHERE {clause} GROUP BY t.id, t.task_name "
                "ORDER BY attachments ASC, t.id",
                caliber=f"{base}；LEFT JOIN 保留零附件任务（R-08）",
                limit=bounded,
            )
            rows["no_attachment_summary"] = summary["rows"][0] if summary["rows"] else {}
            # 46 条任务而 top 默认 8：问「每个任务各有几个附件」时，模型拿到 8 行
            # 就去手数「几个任务有 1 个附件」，数出 21/4/4 而真值是 17/3/5
            # （Q2-03）。清单档天生只能给一页，所以把「各档各多少任务」另立一档，
            # 并在清单的口径里把出口指出来。
            if rows.get("row_count") and int(rows["row_count"]) < 46:
                rows["caliber"] += (
                    f"；本次只返回 {rows['row_count']} 行（top 决定，共 46 条任务），"
                    "不要照这几行去数「有几个任务是 1 个附件」——"
                    "要各档任务数请用 scope=attachment_distribution（服务端算完再回），"
                    "要完整清单请把 top 提到 46"
                )
            return rows

        if key == "attachment_distribution":
            # 「有几个附件的任务各有多少个」是分布，不是清单。分布必须服务端算：
            # 清单档封顶一页，模型翻不到的那 38 行会被当成不存在。零附件那一档
            # 靠 LEFT JOIN 保住（18 条，占了近四成），漏掉它整个分布就变形了。
            return store.fetch(
                "SELECT c.attachments, COUNT(*) AS tasks FROM ("
                "SELECT t.id, COUNT(a.id) AS attachments "
                f"FROM task t {join} "
                "LEFT JOIN task_attachment a ON a.task_id = t.id AND a.is_deleted = 0 "
                f"WHERE {clause} GROUP BY t.id"
                ") c GROUP BY c.attachments ORDER BY c.attachments",
                caliber=(
                    f"{base}；按附件条数分档统计任务数，附件按 is_deleted = 0 计有效；"
                    "零附件档由 LEFT JOIN 保住（18 条任务，占 46 条中的近四成，丢了分布就变形）；"
                    "各档 tasks 相加等于 46；"
                    "档位不连续是正常的（库里没有 5 个附件的任务，所以 4 之后直接是 6）；"
                    "这一档是分布，清单在 scope=attachments，不要拿清单的一页去数分布；"
                    "反过来也不成立：问「每个任务各有几个附件」要的是一任务一行的清单，"
                    "请改用 scope=attachments 并把 top 提到 46——本档一行是一个档位而不是一个任务，"
                    "拿它作答等于把 46 行明细压成几行分布，答的是另一个问题"
                ),
                limit=bounded,
            )

        threshold = max(0, int(min_rounds))
        rows = store.fetch(
            "SELECT t.id AS task_id, t.task_name, COUNT(h.id) AS rounds "
            f"FROM task t {join} "
            "LEFT JOIN task_group_progress_history h ON h.task_id = t.id AND h.is_published = 1 "
            f"WHERE {clause} GROUP BY t.id, t.task_name ORDER BY rounds DESC, t.id",
            caliber=f"{store.GROUP_HISTORY_CALIBER}；LEFT JOIN 保留零期任务（R-08）",
            limit=bounded,
        )
        if threshold:
            cleared = store.scalar(
                "SELECT COUNT(*) AS tasks FROM (SELECT h.task_id "
                f"FROM task_group_progress_history h JOIN task t ON t.id = h.task_id {join} "
                f"WHERE {store.group_history_gate()} "
                "GROUP BY h.task_id HAVING COUNT(*) >= %(n)s) x",
                {"n": threshold},
                caliber=f"至少 {threshold} 期（含 {threshold}，边界取等）",
            )
            rows["tasks_at_least"] = {"min_rounds": threshold, "tasks": cleared["value"]}
        return rows

    return _guard("weekly_group_stats", work)


@mcp.tool()
def weekly_freshness() -> str:
    """Report data snapshot dates so the agent anchors relative time to data, not wall clock."""

    def work() -> dict[str, Any]:
        rows = store.fetch(
            "SELECT b.name AS board_name, MAX(t.latest_progress_time) AS latest_progress, "
            f"DATEDIFF('{store.AS_OF}', MAX(t.latest_progress_time)) AS days_behind, "
            "COUNT(t.id) AS formal_task_count "
            "FROM task_board b "
            f"LEFT JOIN task t ON t.board_id = b.id AND {store.formal_task_clause()} "
            "WHERE b.is_deleted = 0 GROUP BY b.id, b.name ORDER BY b.sort_order",
            caliber=(
                f"{store.FORMAL_TASK_CALIBER}；相对时间须以此快照锚定；"
                "days_behind 是快照日减该看板最新进展时间，由服务端算好；"
                "问「数据更新到什么时候了」两个数都要报：最新时间点，以及它距快照日几天"
                "（overall 里给的是全库那一对，各看板另有自己的一对）"
            ),
        )
        # 「整个看板的数据更新到什么时候了」问的是全库那一对（最新时间 + 落后天数），
        # 不是每个看板各自的。分看板行答不了它：两行里挑一行都不对，
        # 而落后天数此前根本没返回，模型只能答出时间点、漏掉天数（E6-01）。
        overall = store.fetch(
            "SELECT MAX(t.latest_progress_time) AS newest, "
            f"DATEDIFF('{store.AS_OF}', MAX(t.latest_progress_time)) AS days_behind, "
            "COUNT(*) AS formal_task_count "
            f"FROM task t WHERE {store.formal_task_clause()}",
            limit=1,
        )
        rows["overall"] = (overall.get("rows") or [{}])[0]
        rows["as_of"] = store.AS_OF
        # task.latest_progress_time counts UNPUBLISHED progress rows, so the board
        # row above reads 2026-08-09 for the tech board while its newest *formal*
        # progress is 2026-07-31 -- a nine-day gap.  "技术组数据更新到什么时候"
        # asks the formal date (B03/G02): answering off latest_progress_time
        # reports a date that no published record supports.  Both are returned so
        # the drift itself is visible instead of having to be inferred.
        # 两个看板的正式进展存在不同表，只 JOIN task_progress 会把集团组算成 NULL
        # （它的成效写在 task_group_progress_history），那等于把「集团组数据更新到
        # 什么时候」答成「没有数据」。CASE 按 board_id 分流，与 lag_bands 同一口径。
        formal = store.fetch(
            "SELECT b.name AS board_name, "
            "CASE WHEN b.id = 2 "
            "THEN MAX(CASE WHEN h.is_published = 1 THEN h.report_time END) "
            "ELSE MAX(CASE WHEN p.is_published = 1 THEN p.report_time END) END "
            "AS newest_published_progress, "
            f"DATEDIFF('{store.AS_OF}', CASE WHEN b.id = 2 "
            "THEN MAX(CASE WHEN h.is_published = 1 THEN h.report_time END) "
            "ELSE MAX(CASE WHEN p.is_published = 1 THEN p.report_time END) END) "
            "AS published_days_behind "
            "FROM task_board b "
            f"LEFT JOIN task t ON t.board_id = b.id AND {store.formal_task_clause()} "
            "LEFT JOIN task_progress p ON p.task_id = t.id "
            "LEFT JOIN task_group_progress_history h ON h.task_id = t.id "
            "WHERE b.is_deleted = 0 GROUP BY b.id, b.name ORDER BY b.sort_order",
            caliber=(
                "newest_published_progress 只算 is_published = 1 的正式进展行，"
                "并按看板各取自己的表：技术组 task_progress、集团组 task_group_progress_history；"
                "上面各看板行的 latest_progress 是 task.latest_progress_time，它含未发布行，"
                "两者不等就是发布滞后（技术组 08-09 vs 07-31）；"
                "问「（某看板）数据更新到什么时候」答正式口径这一列，不要答 latest_progress"
            ),
        )
        rows["published_progress"] = formal.get("rows") or []
        # 技术组的正式数据其实卡在导入批次上：最后一个跑完的批次（status = 1）
        # 是 07-31，08-15 那批还在处理中。问「技术组数据更新到什么时候」正解是
        # 这个批次日期，进展行只是它的产物。
        imports = store.fetch(
            "SELECT MAX(CASE WHEN status = 1 THEN data_date END) AS newest_finished_batch, "
            "MAX(data_date) AS newest_batch_any_status, "
            "MAX(CASE WHEN status <> 1 THEN data_date END) AS newest_unfinished_batch "
            "FROM task_progress_import",
            limit=1,
            caliber=(
                "导入批次只有 status = 1 才算跑完；newest_unfinished_batch 那批还没过发布门，"
                "不能当作「数据已更新到」的日期（08-15 批次仍在处理中，正式口径停在 07-31）"
            ),
        )
        rows["tech_import"] = (imports.get("rows") or [{}])[0]
        return rows

    return _guard("weekly_freshness", work)


@mcp.tool()
def weekly_health() -> str:
    """Verify the mock store is reachable and report its table row counts."""

    def work() -> dict[str, Any]:
        conn = store.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT TABLE_NAME AS t FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %(db)s ORDER BY TABLE_NAME",
                    {"db": _db.DB_NAME},
                )
                tables = [row["t"] for row in cursor.fetchall()]
                # information_schema.TABLE_ROWS is an InnoDB estimate (it read 14
                # for a 158-row table), so count for real.
                counts: dict[str, int] = {}
                for table in tables:
                    cursor.execute(f"SELECT COUNT(*) AS c FROM `{table}`")
                    counts[table] = int(cursor.fetchone()["c"])
        finally:
            conn.close()
        return {
            "ok": True,
            "store": _db.DSN_DESCRIPTION,
            "table_count": len(counts),
            "total_rows": sum(counts.values()),
            "row_counts": counts,
            "caliber": store.FORMAL_TASK_CALIBER,
            "snapshot_note": "演示数据（weekly_mock 自建库），非集团真实周报",
        }

    return _guard("weekly_health", work)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock weekly-report MCP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18900)
    args = parser.parse_args()

    try:
        probe = store.connect()
        probe.close()
    except store.QueryError as exc:
        print(f"mock store unreachable: {exc}", file=sys.stderr)
        print("start MySQL and import the dump -- see README", file=sys.stderr)
        return 2

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    print(f"mock weekly MCP on http://{args.host}:{args.port}/mcp", flush=True)
    print(f"store: {_db.DSN_DESCRIPTION}", flush=True)
    mcp.run(transport="streamable-http")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
