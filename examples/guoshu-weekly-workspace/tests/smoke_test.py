"""Deterministic contract tests for the guoshu-weekly demo.

These run without an LLM: they exercise the MCP client and the service's caliber
rules directly, so a regression in the取数 contract is caught without spending
model tokens.  The LLM-dependent accuracy baseline (396/200 questions) is a
separate harness -- see README.

Run (with the mock service already up):
    GUOSHU_WEEKLY_MCP_URL=http://127.0.0.1:18900/mcp \
    GUOSHU_WEEKLY_MCP_TOKEN=demo-token \
    python tests/smoke_test.py
"""

# ruff: noqa: RUF001, RUF003  中文口径文案与注释里的全角标点是给人看的正文, 不能换成半角。
# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

WORKSPACE = Path(__file__).resolve().parent.parent
REPO_SRC = WORKSPACE.parent.parent / "src"
sys.path.insert(0, str(REPO_SRC))

from psi_agent.session.tool_registry import ToolRegistry  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"

_results: list[tuple[str, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((PASS if condition else FAIL, name, detail))


def _first(result: dict[str, Any], field: str) -> Any:
    """First row's ``field``, or ``None`` when the result came back empty."""
    rows = result.get("rows") or []
    return rows[0].get(field) if rows else None


async def _call(registry: ToolRegistry, tool: str, **kwargs: Any) -> dict[str, Any]:
    func = registry.get(tool)
    if func is None:
        raise AssertionError(f"tool not registered: {tool}")
    return json.loads(await func(**kwargs))


async def _probe_with_token(token: str) -> dict[str, Any] | None:
    """Call the service directly with a different bearer token.

    The workspace client takes its token from the environment on purpose -- the
    agent must not be able to pick its own credential -- so exercising the
    elevated branch means bypassing the client, not adding a parameter to it.
    """
    url = os.environ.get("GUOSHU_WEEKLY_MCP_URL", "")
    if not url:
        return None
    try:
        async with (
            httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30.0) as http_client,
            streamable_http_client(url, http_client=http_client) as (read, write, *_),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("weekly_workflow_query", {"task": "2", "limit": 3})
            # Content blocks are a union; only TextContent carries .text, so read
            # it defensively rather than assuming the first block is text.
            text = next((getattr(b, "text", "") for b in result.content or [] if getattr(b, "text", "")), "")
            return json.loads(text) if text else None
    except Exception:
        return None


async def run() -> int:
    registry = await ToolRegistry.load(WORKSPACE / "tools", "smoke")

    expected_tools = {
        "weekly_schema",
        "weekly_task_query",
        "weekly_task_detail",
        "weekly_progress_history",
        "weekly_progress_range",
        "weekly_task_lifecycle",
        "weekly_freshness_distribution",
        "weekly_approval_turnaround",
        "weekly_aggregate",
        "weekly_milestone_query",
        "weekly_workflow_query",
        "weekly_submission_query",
        "weekly_owner_roles",
        "weekly_attachment_query",
        "weekly_field_completeness",
        "weekly_progress_coverage",
        "weekly_task_ranking",
        "weekly_import_audit",
        "weekly_group_detail_query",
        "weekly_group_owner_query",
        "weekly_group_history",
        "weekly_group_stats",
        "weekly_year_goal_query",
        "weekly_year_goal_stats",
        "weekly_milestone_stats",
        "weekly_freshness",
        "weekly_health",
        "weekly_person_stats",
        "weekly_attachment_stats",
        "weekly_rank",
        "weekly_scale",
    }
    loaded = set(registry.tools)
    check(
        "所有 31 个工具注册，且无 helper 泄漏为工具",
        loaded == expected_tools,
        f"缺 {sorted(expected_tools - loaded)}；多 {sorted(loaded - expected_tools)}",
    )

    health = await _call(registry, "weekly_health")
    check("weekly_health 连通", health.get("ok") is True, str(health.get("error", ""))[:120])
    if health.get("ok") is not True:
        report()
        return 1
    check(
        "mock 库 12 张表齐全",
        health.get("table_count") == 12,
        f"table_count={health.get('table_count')}",
    )

    # --- envelope shape -----------------------------------------------------
    agg = await _call(registry, "weekly_aggregate", group_by="status")
    check(
        "返回是解包后的信封（不是 JSON 字符串套 JSON）",
        isinstance(agg.get("rows"), list) and "result" not in agg,
        f"keys={list(agg)[:8]}",
    )
    check(
        "每个结果自带 caliber 口径元信息",
        bool(agg.get("caliber")),
        str(agg.get("caliber"))[:100],
    )
    check(
        "每个结果自带演示数据声明",
        "演示数据" in str(agg.get("snapshot_note", "")),
        str(agg.get("snapshot_note"))[:80],
    )

    # --- R-01 formal-task caliber ------------------------------------------
    check(
        "R-01 正式任务口径已固化并回传",
        "workflow_status = 'published'" in str(agg.get("caliber")) and "is_deleted = 0" in str(agg.get("caliber")),
        str(agg.get("caliber"))[:120],
    )
    statuses = {r["group_name"]: r["cnt"] for r in agg["rows"]}
    total_by_status = sum(statuses.values())
    boards = await _call(registry, "weekly_aggregate", group_by="board")
    total_by_board = sum(r["cnt"] for r in boards["rows"])
    check(
        "两种分组维度的正式任务总数一致",
        total_by_status == total_by_board,
        f"status 合计={total_by_status} board 合计={total_by_board}",
    )

    # --- R-02 / R-08 empty groups survive ----------------------------------
    categories = await _call(registry, "weekly_aggregate", group_by="category")
    check(
        "R-02/R-08 LEFT JOIN 保留空分组（存在 cnt=0 的分类）",
        any(r["cnt"] == 0 for r in categories["rows"]),
        f"分类数={len(categories['rows'])} 零任务分类数={sum(1 for r in categories['rows'] if r['cnt'] == 0)}",
    )

    # --- permission gating (R-04 / R-14) -----------------------------------
    workflow = await _call(registry, "weekly_workflow_query", limit=5)
    opinions = [r.get("opinion") for r in workflow.get("rows", [])]
    check(
        "R-04/R-14 审批意见按权限遮蔽",
        bool(opinions) and all(o == "[按权限不展示]" for o in opinions),
        f"samples={opinions[:2]}",
    )

    # --- blocked fields never cross the boundary ---------------------------
    attachments = await _call(registry, "weekly_attachment_query", limit=5)
    rows = attachments.get("rows", [])
    check(
        "storage_path 绝不外泄",
        bool(rows) and all("storage_path" not in r for r in rows),
        f"字段={list(rows[0]) if rows else []}",
    )
    # Only the data rows matter here: the caliber text legitimately *names*
    # storage_path to state that it is withheld.
    rows_only = json.dumps(rows, ensure_ascii=False)
    check(
        "附件数据行中不含 storage_path（口径说明里提及是正常的）",
        "storage_path" not in rows_only,
    )

    detail = await _call(registry, "weekly_task_detail", task="行业可信数据空间建设")
    check(
        "任务详情不含 storage_path / payload",
        "storage_path" not in json.dumps(detail, ensure_ascii=False)
        and '"payload"' not in json.dumps(detail, ensure_ascii=False),
    )
    check(
        "R-12 completion_time 标注为文本不可运算",
        "不可做日期运算" in json.dumps(detail, ensure_ascii=False),
        "缺少 R-12 口径提示",
    )

    # --- permission tier is decided by the bearer token --------------------
    # R-04/R-14 say "by permission", so BOTH branches must be exercisable:
    # blanket redaction fails the requirement as surely as blanket exposure, and
    # makes the capability untestable. The deciding input is the transport's
    # Authorization header -- nothing the model says can widen it.
    workflow = await _call(registry, "weekly_workflow_query", task="2", limit=3)
    caliber_text = str(workflow.get("caliber", ""))
    check(
        "敏感字段权限状态在 caliber 中如实声明",
        "按权限展示" in caliber_text and ("无敏感字段权限" in caliber_text or "有敏感字段权限" in caliber_text),
        caliber_text[:120],
    )
    elevated = await _probe_with_token(os.environ.get("SMOKE_ADMIN_TOKEN", "demo-admin-token"))
    if elevated is None:
        check("提权凭证可读到 opinion 原文", False, "提权探测失败（服务未起或 token 不符）")
    else:
        opinions = [r.get("opinion") for r in elevated.get("rows", [])]
        revealed = [o for o in opinions if o and o != "[按权限不展示]"]
        check(
            "提权凭证可读到 opinion 原文（权限分级真的分级了）",
            bool(revealed),
            f"opinions={json.dumps(opinions, ensure_ascii=False)[:110]}",
        )
        check(
            "普通凭证与提权凭证结果确有差异",
            [r.get("opinion") for r in workflow.get("rows", [])] != opinions,
        )

    # --- truncation is reported, not silent --------------------------------
    small = await _call(registry, "weekly_task_query", board="tech", limit=2)
    check(
        "截断显式上报 has_more + total_count",
        small.get("has_more") is True
        and isinstance(small.get("total_count"), int)
        and small["total_count"] > small["row_count"],
        f"row_count={small.get('row_count')} total={small.get('total_count')} has_more={small.get('has_more')}",
    )

    # --- progress ordering and draft exclusion -----------------------------
    history = await _call(registry, "weekly_progress_history", task="行业可信数据空间建设", limit=10)
    versions = [r["version_no"] for r in history.get("rows", [])]
    check(
        "进展版本按 version_no 倒序（第一条即当期）",
        versions == sorted(versions, reverse=True) and bool(versions),
        f"versions={versions[:5]}",
    )
    check(
        "默认只返回正式发布的进展（is_published=1）",
        all(r.get("is_published") == 1 for r in history.get("rows", [])),
    )

    # --- milestone re-checks the formal caliber (R-17) ----------------------
    milestones = await _call(registry, "weekly_milestone_query", year="2026", limit=5)
    check(
        "R-17 里程碑关联任务表复核正式任务口径",
        milestones.get("ok") is True and "is_deleted = 0" in str(milestones.get("caliber")),
        str(milestones.get("caliber"))[:110],
    )

    # --- import reconciliation (R-09 / R-10) -------------------------------
    audit = await _call(registry, "weekly_import_audit", limit=5)
    recon = audit.get("reconciliation", {})
    check(
        "R-09/R-10 导入批次对账字段齐全",
        {"batch_count", "distinct_dates", "distinct_import_times"} <= set(recon),
        f"recon={recon}",
    )

    # --- submission forms: a separate table from the action log -------------
    # Regression guard for a real defect: resolve_task() gated task_id on R-01
    # and fell through to fuzzy name matching on a miss, so task="2" returned a
    # DIFFERENT task's submissions (5 rows, all published) in place of task 2's
    # own (2 rows, both rejected).
    subs = await _call(registry, "weekly_submission_query", task="2")
    task_ids = {r.get("task_id") for r in subs.get("rows", [])}
    check(
        "提交单按 task_id 外键取数，不被正式任务口径截断",
        subs.get("ok") is True and task_ids == {2},
        f"task_ids={sorted(task_ids)} row_count={subs.get('row_count')}",
    )
    check(
        "提交单返回 round_no 与 status（动作流水无法聚合出这两项）",
        bool(subs.get("rows"))
        and {"round_no", "status"} <= set(subs["rows"][0])
        and isinstance(subs.get("status_breakdown"), list),
    )
    check(
        "提交单不返回 payload 草稿快照",
        '"payload"' not in json.dumps(subs.get("rows", []), ensure_ascii=False),
    )
    pending = await _call(registry, "weekly_submission_query", reporter="u3208", exclude_status="approved")
    check(
        "提交单支持按填报人过滤 + 排除状态",
        pending.get("ok") is True and pending.get("row_count", 0) > 0,
        f"row_count={pending.get('row_count')}",
    )

    # --- A1: 闸门类问题的三处失分 ------------------------------------------
    # M3-03. approved 不在提交单状态值域内（已发布叫 published），所以这个过滤
    # 静默失效、结果等于未过滤。工具必须自己说出来，否则会被当成「已排除」。
    check(
        "M3-03 提交单状态值域随结果返回",
        pending.get("status_domain")
        == [
            "cancelled",
            "pending_audit",
            "pending_fill",
            "pending_leader",
            "published",
            "rejected",
            "signing",
        ],
        f"status_domain={pending.get('status_domain')}",
    )
    pending_caliber = str(pending.get("caliber", ""))
    check(
        "M3-03 值域外的过滤词被显式点名为未生效",
        "approved" in pending_caliber and "未筛掉任何行" in pending_caliber,
        f"caliber={pending_caliber[-120:]}",
    )
    check(
        "M3-03 u3208 共 29 条，total_count 与 row_count 一致且未截断",
        pending.get("total_count") == 29 and pending.get("row_count") == 29 and pending.get("has_more") is False,
        f"total={pending.get('total_count')} rows={pending.get('row_count')}",
    )
    pending_states = {r.get("status") for r in pending.get("rows", [])}
    check(
        "M3-03 结果含 published（说明过滤确实没生效，别按题面反推）",
        "published" in pending_states,
        f"states={sorted(pending_states)}",
    )
    check(
        "M3-03 口径要求清单类问题逐条列全",
        "逐条列全" in pending_caliber,
    )

    # M1-01. 附件大小是字节，模型此前换算成「约 3.8MB」而与精确值不一致。
    att19 = await _call(registry, "weekly_attachment_query", task="19")
    att_rows = att19.get("rows", [])
    check(
        "M1-01 任务 19 有 2 个未删除附件",
        att19.get("ok") is True and att19.get("row_count") == 2,
        f"row_count={att19.get('row_count')}",
    )
    check(
        "M1-01 file_size 为精确字节 3995969 / 1637494",
        [str(r.get("file_size")) for r in att_rows] == ["3995969", "1637494"],
        f"sizes={[r.get('file_size') for r in att_rows]}",
    )
    att_caliber = str(att19.get("caliber", ""))
    check(
        "M1-01 口径明说字节原样不换算",
        "字节" in att_caliber and "不要换算" in att_caliber,
        f"caliber={att_caliber}",
    )

    # N3-04. 各组牵头人数此前要模型自己数人名，参考 9 被答成 14。
    groups = await _call(registry, "weekly_aggregate", group_by="project_group")
    grows = groups.get("rows", [])
    check(
        "N3-04 专项组聚合 11 组，任务数合计 128",
        groups.get("ok") is True and len(grows) == 11 and sum(int(r["cnt"]) for r in grows) == 128,
        f"groups={len(grows)} sum={sum(int(r['cnt']) for r in grows) if grows else 0}",
    )
    check(
        "N3-04 服务端直接给出去重后的牵头人数与责任人数",
        bool(grows) and {"cnt", "lead_owner_count", "project_owner_count"} <= set(grows[0]),
        f"keys={sorted(grows[0]) if grows else []}",
    )
    check(
        "N3-04 各组牵头人数与 gold 一致",
        [int(r["lead_owner_count"]) for r in grows] == [9, 10, 11, 9, 9, 8, 7, 6, 6, 6, 5],
        f"leads={[r.get('lead_owner_count') for r in grows]}",
    )

    # L2-04. 完成率与分母同排返回，且要能按率定序——按任务数排给不出「完成率最低的 3 组」。
    low_rate = await _call(
        registry, "weekly_aggregate", group_by="project_group", order_by="finish_rate", ascending=True, top=3
    )
    check(
        "L2-04 完成率最低 3 组：标准安全组 1/19=5.3、数据基础设施组 2/15=13.3、治理合规组 2/10=20.0",
        [
            (r.get("group_name"), str(r.get("finished")), str(r.get("finish_rate_pct")))
            for r in (low_rate.get("rows") or [])
        ]
        == [("标准安全组", "1", "5.3"), ("数据基础设施组", "2", "13.3"), ("治理合规组", "2", "20.0")]
        and str(low_rate.get("total_groups")) == "11",
        f"rows={low_rate.get('rows')}",
    )
    check(
        "L2-04 默认按任务数定序时口径指路 order_by=finish_rate",
        "请加 order_by=finish_rate" in str(groups.get("caliber", ""))
        and "完成数最少的组不等于完成率最低的组" in str(groups.get("caliber", "")),
    )

    # L4-02. 被排名的单位是二级分类而不是任务：与 weekly_rank per_group 的胜出者类型不同。
    top_sub = await _call(registry, "weekly_aggregate", group_by="top_sub_per_primary")
    check(
        "L4-02 每个一级分类下任务数最多的二级分类共 11 行，首行产业生态→数据标注基地 4",
        top_sub.get("row_count") == 11
        and (top_sub.get("rows") or [{}])[0].get("group_name") == "产业生态"
        and (top_sub.get("rows") or [{}])[0].get("sub_name") == "数据标注基地"
        and str((top_sub.get("rows") or [{}])[0].get("cnt")) == "4",
        f"head={(top_sub.get('rows') or [{}])[:2]}",
    )
    top_task = await _call(
        registry, "weekly_rank", metric="progress_rounds", mode="per_group", group_by="primary_category"
    )
    check(
        "L4-02 同为「每组第一」但胜出者一个是分类一个是任务，两档不可互答",
        {r.get("sub_name") for r in (top_sub.get("rows") or [])}
        != {r.get("task_name") for r in (top_task.get("rows") or [])}
        and "被排名的单位是二级分类而不是任务" in str(top_sub.get("caliber", "")),
    )

    # L5-03 / L5-04. 分位与分档不是名次题：拿 cut 的前几行手算中位会答成 14（真值 6）。
    dist = await _call(registry, "weekly_rank", metric="progress_rounds", mode="distribution")
    drow = (dist.get("rows") or [{}])[0]
    check(
        "L5-03 五数概括 q1=2 中位 6 q3=15 极值 0-18 均值 7.37 分母 128",
        (
            str(drow.get("q1")),
            str(drow.get("median")),
            str(drow.get("q3")),
            str(drow.get("min_rounds")),
            str(drow.get("max_rounds")),
            str(drow.get("avg_rounds")),
            str(drow.get("task_total")),
        )
        == ("2", "6", "15", "0", "18", "7.37", "128"),
        f"row={drow}",
    )
    check(
        "L5-03 口径点明不要拿名次档前几行手算中位",
        "会答成 14" in str(dist.get("caliber", "")),
    )
    quart = await _call(registry, "weekly_rank", metric="progress_rounds", mode="quartiles")
    check(
        "L5-04 NTILE(4) 等量四档各 32 条，区间 0-0 / 0-5 / 6-14 / 14-18",
        [
            (str(r.get("quartile")), str(r.get("tasks")), str(r.get("min_rounds")), str(r.get("max_rounds")))
            for r in (quart.get("rows") or [])
        ]
        == [("1", "32", "0", "0"), ("2", "32", "0", "5"), ("3", "32", "6", "14"), ("4", "32", "14", "18")],
        f"rows={quart.get('rows')}",
    )
    check(
        "L5-04 口径区分等量分档与等宽分档，并说明边界跨档重复不是错",
        "会得 17/39/41/31" in str(quart.get("caliber", "")) and "跨档重复出现" in str(quart.get("caliber", "")),
    )
    bad_mode = await _call(registry, "weekly_rank", metric="progress_rounds", mode="deciles")
    check(
        "排名不支持的 mode 显式报错并列出全部五档",
        bad_mode.get("ok") is False
        and bad_mode.get("error", {}).get("code") == "unsupported_mode"
        and "distribution" in str(bad_mode.get("error", {}).get("message", ""))
        and "quartiles" in str(bad_mode.get("error", {}).get("message", "")),
        f"err={bad_mode.get('error')}",
    )
    check(
        "N3-04 任务数序列与 gold 一致（同数按组名定序，避免并列漂移）",
        [int(r["cnt"]) for r in grows] == [19, 15, 15, 14, 12, 11, 10, 10, 8, 8, 6],
        f"cnt={[r.get('cnt') for r in grows]}",
    )
    check(
        "N3-04 牵头人数不超过任务数",
        all(int(r["lead_owner_count"]) <= int(r["cnt"]) for r in grows),
    )
    group_caliber = str(groups.get("caliber", ""))
    check(
        "N3-04 口径注明计数已由服务端去重，禁止自行数人名",
        "去重" in group_caliber and "不要自己数" in group_caliber,
        f"caliber={group_caliber[-100:]}",
    )
    other_agg = await _call(registry, "weekly_aggregate", group_by="owner")
    check(
        "其他聚合维度不受影响，仍只返回 group_name/cnt",
        other_agg.get("ok") is True and "lead_owner_count" not in (other_agg.get("rows") or [{}])[0],
    )

    # --- A2: F 类负责人与组织 ------------------------------------------------
    # F3-01/02/04. 姓名列 128 条全满、ID 列只有 119 条：只暴露姓名列时模型会
    # 如实报「无缺失」，与 gold 的 9 条直接矛盾。缺口只能从 ID 列看见。
    owner_id_comp = await _call(registry, "weekly_field_completeness", field="project_owner_id")
    comp_id_row = (owner_id_comp.get("rows") or [{}])[0]
    check(
        "F3-04 project_owner_id 完整度 128/119/9",
        [str(comp_id_row.get(k)) for k in ("total", "filled", "missing")] == ["128", "119", "9"],
        f"row={comp_id_row}",
    )
    missing_owners = await _call(registry, "weekly_field_completeness", field="project_owner_id", list_missing=True)
    check(
        "F3-01 缺责任人 ID 的 9 条任务可逐条列出，id 与 gold 一致",
        missing_owners.get("total_count") == 9
        and [r.get("id") for r in missing_owners.get("rows", [])] == [21, 27, 29, 33, 73, 99, 125, 149, 150],
        f"ids={[r.get('id') for r in missing_owners.get('rows', [])]}",
    )
    check(
        "F3-01 缺项清单带姓名列，便于说明「有名字但没 ID」",
        bool(missing_owners.get("rows"))
        and {"task_name", "project_owner_name", "lead_owner_name"} <= set(missing_owners["rows"][0]),
        f"keys={sorted(missing_owners.get('rows', [{}])[0])}",
    )

    # F2-02/03/04. 牵头人任务量此前靠模型翻明细自己数，首位被答成 6 条。
    workload = await _call(registry, "weekly_person_stats", scope="workload", top=3)
    check(
        "F2-02 牵头人任务量榜首 吴晓东 14 条（并列按姓名定序）",
        (workload.get("rows") or [{}])[0].get("person") == "吴晓东"
        and int((workload.get("rows") or [{}])[0].get("task_count", 0)) == 14,
        f"top={workload.get('rows')}",
    )
    wl_summary = await _call(registry, "weekly_person_stats", scope="workload_summary")
    wl_row = (wl_summary.get("rows") or [{}])[0]
    check(
        "F2-03 人均任务数 8.00 由服务端算（128 任务 / 16 人）",
        str(wl_row.get("avg_tasks_per_person")) == "8.00"
        and int(wl_row.get("tasks", 0)) == 128
        and int(wl_row.get("people", 0)) == 16,
        f"row={wl_row}",
    )
    single = await _call(registry, "weekly_person_stats", scope="single_task")
    check(
        "F2-04 只带一条任务的 4 位牵头人由 HAVING 判定",
        [r.get("person") for r in single.get("rows", [])]
        == ["project_lead_a", "project_lead_b", "project_lead_c", "余承志"],
        f"rows={[r.get('person') for r in single.get('rows', [])]}",
    )

    # F4-01/02/03/04. 标识写法异构：三档相加 128 但空标识不进任何档。
    id_fmt = await _call(registry, "weekly_person_stats", scope="id_format")
    check(
        "F4-01/02 标识格式分档 纯数字 69 / u 前缀 50 / NDG 域账号 9",
        [(r.get("id_format"), int(r.get("task_count", 0))) for r in id_fmt.get("rows", [])]
        == [("纯数字工号", 69), ("u 前缀账号", 50), ("NDG 域账号", 9)],
        f"rows={id_fmt.get('rows')}",
    )
    check(
        "F4-01 口径提醒各档相加不等于任务总数（空标识不进档）",
        "各档相加不等于任务总数" in str(id_fmt.get("caliber", "")),
    )
    variants = await _call(registry, "weekly_person_stats", scope="id_variants")
    check(
        "F4-03 同名多标识为 0 行，且口径说明 0 行即不存在",
        variants.get("row_count") == 0 and "不存在这种人" in str(variants.get("caliber", "")),
        f"rows={variants.get('row_count')}",
    )
    longest = await _call(registry, "weekly_person_stats", scope="id_longest", top=4)
    check(
        "F4-04 最长标识 11 字符且口径点明存在并列",
        int((longest.get("rows") or [{}])[0].get("id_length", 0)) == 11 and "并列" in str(longest.get("caliber", "")),
        f"top={longest.get('rows')}",
    )

    # F6-01/02/03/04. 填报在 task_progress 上，任务闸门之外还有行级 is_published。
    reporters = await _call(registry, "weekly_person_stats", scope="reporters", top=3)
    check(
        "F6-01 填报最多 10515 共 63 轮 / 4 个任务",
        [(r.get("reporter_id"), int(r.get("reported_rounds", 0))) for r in reporters.get("rows", [])]
        == [("10515", 63), ("10445", 57), ("10564", 50)],
        f"rows={reporters.get('rows')}",
    )
    check(
        "F6-01 口径写明任务闸门与进展行发布闸门是两道",
        "p.is_published = 1" in str(reporters.get("caliber", "")),
    )
    rep_count = await _call(registry, "weekly_person_stats", scope="reporter_count")
    check(
        "F6-02 去重填报人 43 位由服务端算",
        int((rep_count.get("rows") or [{}])[0].get("reporter_count", 0)) == 43,
        f"row={rep_count.get('rows')}",
    )
    reviewers = await _call(registry, "weekly_person_stats", scope="reviewers", top=3)
    check(
        "F6-03 审核口径不加 is_published，首位 10277/10291 各 119 条",
        [(r.get("reviewer_id"), int(r.get("reviewed", 0))) for r in reviewers.get("rows", [])]
        == [("10277", 119), ("10291", 119), ("10270", 116)],
        f"rows={reviewers.get('rows')}",
    )
    check(
        "F6-03 口径解释为何审核不加发布闸门",
        "审过但未发布的进展同样算审过" in str(reviewers.get("caliber", "")),
    )
    self_rev = await _call(registry, "weekly_person_stats", scope="self_review")
    check(
        "F6-04 自审 7 条，按 ID 相等判定而非姓名",
        self_rev.get("row_count") == 7 and "不按姓名" in str(self_rev.get("caliber", "")),
        f"row_count={self_rev.get('row_count')}",
    )

    # F7-02/04. 跨组与双角色此前被答成 4 组、人名也不对。
    cross = await _call(registry, "weekly_person_stats", scope="cross_group", top=3)
    check(
        "F7-02 跨组榜首 吴晓东 跨 8 组，group_count 已去重",
        (cross.get("rows") or [{}])[0].get("person") == "吴晓东"
        and int((cross.get("rows") or [{}])[0].get("group_count", 0)) == 8,
        f"top={cross.get('rows')}",
    )
    dual = await _call(registry, "weekly_person_stats", scope="dual_role")
    check(
        "F7-04 双角色 6 人，两列各自计数不可相加",
        [(r.get("person"), int(r.get("as_lead", 0)), int(r.get("as_project_owner", 0))) for r in dual.get("rows", [])]
        == [
            ("吴晓东", 14, 1),
            ("孙立群", 12, 2),
            ("马跃进", 11, 2),
            ("周文斌", 9, 4),
            ("胡建国", 8, 1),
            ("余承志", 1, 7),
        ],
        f"rows={dual.get('rows')}",
    )
    check(
        "F7-04 口径提醒不要把两个角色的计数相加",
        "别把两列相加" in str(dual.get("caliber", "")),
    )

    # F5-02/04. 多值负责人栏的分隔符是混填的，「单人」必须是独立一档。
    seps = await _call(registry, "weekly_group_stats", scope="separators")
    check(
        "F5-02 分隔符分档 半角逗号 26 / 单人 18 / 全角顿号 2",
        [(r.get("separator_kind"), int(r.get("n", 0))) for r in seps.get("rows", [])]
        == [("半角逗号", 26), ("单人无分隔符", 18), ("全角顿号", 2)],
        f"rows={seps.get('rows')}",
    )
    check(
        "F5-02 口径说明「单人无分隔符」是一档而非缺失",
        "是独立一档不是缺失" in str(seps.get("caliber", "")),
    )
    widths = await _call(registry, "weekly_group_stats", scope="owner_widths", top=3)
    top_width = (widths.get("rows") or [{}])[0]
    check(
        "F5-04 责任人最多的一条是 数据资产入表试点推进（3 人）",
        top_width.get("task_name") == "数据资产入表试点推进"
        and top_width.get("project_owner_names") == "胡建国,方永康,邓少华"
        and int(top_width.get("owner_count", 0)) == 3,
        f"top={top_width}",
    )

    # F5-01. 牵头人与责任人要同表并列取出，还得带专项组。
    # task_name 由服务端固定带上，不在 fields 白名单里，写进去会报 unsupported_field。
    group_owners = await _call(
        registry,
        "weekly_group_detail_query",
        fields="lead_owner_names,project_owner_names,project_group",
        limit=8,
    )
    check(
        "F5-01 三列可一次取全并带 project_owner_names 的原始多值形态",
        bool(group_owners.get("rows"))
        and {"task_name", "lead_owner_names", "project_owner_names", "project_group"} <= set(group_owners["rows"][0]),
        f"keys={sorted(group_owners.get('rows', [{}])[0])}",
    )
    check(
        "F5-01 前 8 条按最新进展时间定序，首条与 gold 一致",
        [r.get("task_name") for r in group_owners.get("rows", [])][:2]
        == ["重点行业数据空间试点", "数字中国建设重点工程支撑（2期）"]
        and group_owners["rows"][0].get("project_owner_names") == "吴晓东",
        f"first={group_owners.get('rows', [{}])[0]}",
    )

    # --- A3: J 类附件 -------------------------------------------------------
    # J2-01/02/03. 明细上限 200 条，靠翻行求和必然少算（真实 454 条）。
    att_sum = await _call(registry, "weekly_attachment_stats", scope="summary")
    sum_row = (att_sum.get("rows") or [{}])[0]
    check(
        "J2-01 附件 454 条 / 1863.8MB / 均值 4203.9KB，字节为权威值",
        int(sum_row.get("attachment_count", 0)) == 454
        and str(sum_row.get("total_bytes")) == "1954375767"
        and str(sum_row.get("total_mb")) == "1863.8"
        and str(sum_row.get("avg_kb")) == "4203.9",
        f"row={sum_row}",
    )
    by_ext = await _call(registry, "weekly_attachment_stats", scope="by_ext")
    check(
        "J2-02 类型分布 pptx 130 / xlsx 116 / pdf 107 / docx 101",
        [(r.get("ext"), int(r.get("n", 0))) for r in by_ext.get("rows", [])]
        == [("pptx", 130), ("xlsx", 116), ("pdf", 107), ("docx", 101)],
        f"rows={by_ext.get('rows')}",
    )
    largest = await _call(registry, "weekly_attachment_stats", scope="largest", top=2)
    big = (largest.get("rows") or [{}])[0]
    check(
        "J2-03 最大附件 行业数据标注基地能力建设-会议纪要.pdf（7.99MB / 8379724 字节）",
        big.get("file_name") == "行业数据标注基地能力建设-会议纪要.pdf"
        and str(big.get("file_size")) == "8379724"
        and str(big.get("size_mb")) == "7.99",
        f"top={big}",
    )

    # J3-01/02/03/04. 挂载归属四问，一条附件只进一档；孤儿行必须走 NOT EXISTS。
    by_link = await _call(registry, "weekly_attachment_stats", scope="by_link")
    check(
        "J3-01 挂载分布 进展 315 / 任务本体 81 / 提交单 58，合计等于 454",
        [(r.get("link_type"), int(r.get("n", 0))) for r in by_link.get("rows", [])]
        == [("挂在进展", 315), ("挂在任务本体", 81), ("挂在提交单", 58)]
        and sum(int(r.get("n", 0)) for r in by_link.get("rows", [])) == 454,
        f"rows={by_link.get('rows')}",
    )
    open_sub = await _call(registry, "weekly_attachment_stats", scope="on_open_submission")
    check(
        "J3-02 在途提交单附件 58 个（按提交单自己的码值判，不用 workflow_status）",
        int((open_sub.get("rows") or [{}])[0].get("attachment_count", 0)) == 58
        and "s.status <> 'published'" in str(open_sub.get("caliber", "")),
        f"row={open_sub.get('rows')}",
    )
    by_prog = await _call(registry, "weekly_attachment_stats", scope="by_progress", top=8)
    check(
        "J3-03 已发布进展带附件 Top8 首两条各 3 个，且按任务 id/期号定序",
        [
            (r.get("task_name"), int(r.get("version_no", 0)), int(r.get("attachment_count", 0)))
            for r in by_prog.get("rows", [])
        ][:3]
        == [
            ("数据要素标准国际对标", 15, 3),
            ("数据交易平台功能迭代（2期）", 2, 3),
            ("全国一体化算力网调度平台建设", 10, 2),
        ],
        f"rows={by_prog.get('rows')}",
    )
    orphan = await _call(registry, "weekly_attachment_stats", scope="orphan")
    check(
        "J3-04 孤儿附件 3 条，口径点明 JOIN 会恒等于 0",
        int((orphan.get("rows") or [{}])[0].get("orphan_count", 0)) == 3
        and "NOT EXISTS" in str(orphan.get("caliber", "")),
        f"row={orphan.get('rows')}",
    )

    # J4-02/03. 软删审计问的是表本身，加任务闸门会少算。
    deleted = await _call(registry, "weekly_attachment_stats", scope="deleted")
    del_row = (deleted.get("rows") or [{}])[0]
    check(
        "J4-03 已删附件 33 条 / 116.4MB，全表 543 行",
        int(del_row.get("deleted", 0)) == 33
        and str(del_row.get("deleted_mb")) == "116.4"
        and int(del_row.get("total_rows", 0)) == 543,
        f"row={del_row}",
    )
    check(
        "J4-03 口径说明这是全表口径不加任务闸门",
        "不加任务闸门" in str(deleted.get("caliber", "")),
    )
    del_link = await _call(registry, "weekly_attachment_stats", scope="deleted_by_link")
    check(
        "J4-02 已删附件挂载分布 进展 20 / 提交单 7 / 任务本体 6",
        [(r.get("link_type"), int(r.get("n", 0))) for r in del_link.get("rows", [])]
        == [("挂在进展", 20), ("挂在提交单", 7), ("挂在任务本体", 6)],
        f"rows={del_link.get('rows')}",
    )

    # J5-01/02/03. 上传人维度此前被当成任务维度答，月度也漏了起始月过滤。
    uploaders = await _call(registry, "weekly_attachment_stats", scope="by_uploader", top=3)
    check(
        "J5-01 上传最多 10354 共 26 个 / 98.2MB",
        [(r.get("uploader_id"), int(r.get("upload_count", 0))) for r in uploaders.get("rows", [])]
        == [("10354", 26), ("10515", 24), ("10438", 23)],
        f"rows={uploaders.get('rows')}",
    )
    up_count = await _call(registry, "weekly_attachment_stats", scope="uploader_count")
    check(
        "J5-02 上传过附件的 46 人由服务端去重",
        int((up_count.get("rows") or [{}])[0].get("uploader_count", 0)) == 46,
        f"row={up_count.get('rows')}",
    )
    by_month = await _call(registry, "weekly_attachment_stats", scope="by_month", date_from="2026-01-01")
    check(
        "J5-03 2026 年逐月 23/26/32/31/28/16/18/3 且未截断",
        [int(r.get("n", 0)) for r in by_month.get("rows", [])] == [23, 26, 32, 31, 28, 16, 18, 3]
        and by_month.get("has_more") is False,
        f"rows={[(r.get('ym'), r.get('n')) for r in by_month.get('rows', [])]}",
    )
    check(
        "J5-03 口径回显起始月，未限月时明说含全部历史",
        "仅 2026-01-01 起" in str(by_month.get("caliber", "")),
    )

    # J1-01. 基线里三个字节数都被答成「约 7.5MB」这类约数，对不上参考答案。
    # 明细口径已写明「原样报出，不要换算成 KB/MB 也不要写「约」」，钉住这句话
    # 与三个原始字节值：一旦谁把 file_size 改成换算后的数，这条先红。
    att_one = await _call(registry, "weekly_attachment_query", task="隐私计算平台自主可控攻关")
    check(
        "J1-01 该任务 3 个附件按原始字节返回，口径禁止换算与约数",
        sorted(str(r.get("file_size")) for r in (att_one.get("rows") or [])) == ["6166957", "6769427", "7903110"]
        and "不要换算成 KB/MB" in str(att_one.get("caliber", "")),
        f"sizes={[r.get('file_size') for r in (att_one.get('rows') or [])]}",
    )

    # K1-03. 看板轴的滞后/活跃两侧共用一个分母，两侧相加须等于 total。窗口取 90 天
    # ——「活跃度」按季度看，30 天窗口下集团看板也有滞后，答不出「全活跃」这个对比。
    fresh_board = await _call(registry, "weekly_freshness_distribution", stale_days=90, by="board")
    board_rows = {str(r.get("bucket")): r for r in (fresh_board.get("rows") or [])}
    tech = board_rows.get("技术组重点任务进展", {})
    grp = board_rows.get("集团重点任务调度", {})
    check(
        "K1-03 按看板分滞后：技术组重点任务进展 61/82 活跃 74.4%，集团重点任务调度 46/46 全活跃",
        str(tech.get("active_count")) == "61"
        and str(tech.get("total")) == "82"
        and str(tech.get("active_pct")) == "74.4"
        and str(grp.get("active_count")) == "46"
        and str(grp.get("active_pct")) == "100.0",
        f"tech={tech} grp={grp}",
    )
    check(
        "K1-03 每组滞后与活跃相加等于 total",
        all(
            int(r.get("stale_count") or 0) + int(r.get("active_count") or 0) == int(r.get("total") or 0)
            for r in (fresh_board.get("rows") or [])
        ),
        f"rows={fresh_board.get('rows')}",
    )

    # K2-03. 占比题的反例本体：条数最多的组不是占比最高的组。首行必须按 stale_pct 排出来。
    # 同样取 90 天窗口，且不加在办闸门（问「占比」问的是全部正式任务，分母 128）。
    fresh_pg = await _call(registry, "weekly_freshness_distribution", stale_days=90, by="project_group")
    pg_rows = fresh_pg.get("rows") or []
    pg_map = {str(r.get("bucket")): r for r in pg_rows}
    check(
        "K2-03 专项组滞后占比首行是国家工程办 4/15=26.7%，压过条数更多的标准安全组 5/19=26.3%",
        str(pg_rows[0].get("bucket")) == "国家工程办"
        and str(pg_rows[0].get("stale_pct")) == "26.7"
        and str(pg_map.get("标准安全组", {}).get("stale_count")) == "5"
        and str(pg_map.get("标准安全组", {}).get("stale_pct")) == "26.3",
        f"head={pg_rows[:2]}",
    )
    check(
        "K2-03 口径说明占比由服务端算且条数最多未必占比最高",
        "不要拿滞后条数跟别处的任务数手工相除" in str(fresh_pg.get("caliber", ""))
        and "条数最多的那组未必占比最高" in str(fresh_pg.get("caliber", "")),
    )
    bad_axis = await _call(registry, "weekly_freshness_distribution", stale_days=30, by="owner")
    check(
        "滞后分组不支持的轴显式报错并列出支持值",
        bad_axis.get("ok") is False
        and bad_axis.get("error", {}).get("code") == "unsupported_group_by"
        and "project_group" in str(bad_axis.get("error", {}).get("message", "")),
        f"err={bad_axis.get('error')}",
    )

    # K3-04. 「各年完成多少」的两个数要同排给出，且各年已完成相加等于全库 31 条。
    life_year = await _call(registry, "weekly_task_lifecycle", by="year")
    year_map = {str(r.get("bucket")): r for r in (life_year.get("rows") or [])}
    check(
        "K3-04 按年：2025 建 105/当前完成 26，2026 建 23/完成 5，两年完成相加为 31",
        str(year_map.get("2025", {}).get("created_count")) == "105"
        and str(year_map.get("2025", {}).get("currently_finished")) == "26"
        and str(year_map.get("2026", {}).get("created_count")) == "23"
        and str(year_map.get("2026", {}).get("currently_finished")) == "5"
        and sum(int(r.get("currently_finished") or 0) for r in (life_year.get("rows") or [])) == 31,
        f"rows={life_year.get('rows')}",
    )
    check(
        "K3-04 口径说明这是按建单档看当前状态而非那一年完成的",
        "不是「那一年完成的任务数」" in str(life_year.get("caliber", "")),
    )

    # K5-03. 一级分类与里程碑自己的 category 是两个轴，同一问题给出两个不同首行。
    prim = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="primary_category")
    cat = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="category")
    prim_head = (prim.get("rows") or [{}])[0]
    cat_head = (cat.get("rows") or [{}])[0]
    check(
        "K5-03 一级分类完成率首行改革与治理 40/27=67.5%，与 by=category 的国家任务 58.9% 不是同一个轴",
        str(prim_head.get("bucket")) == "改革与治理"
        and str(prim_head.get("total")) == "40"
        and str(prim_head.get("finish_rate_pct")) == "67.5"
        and str(cat_head.get("bucket")) == "国家任务"
        and str(cat_head.get("finish_rate_pct")) == "58.9",
        f"prim={prim_head} cat={cat_head}",
    )
    check(
        "K5-03 口径点明用 by=category 会答成里程碑类别",
        "用 by=category 会答成里程碑类别" in str(prim.get("caliber", "")),
    )

    # K4-01. 超期只能判可归一化的那部分：季度归一化后才露出任务 123，34 条判不了要单独报。
    overdue = await _call(registry, "weekly_group_stats", scope="overdue")
    od_rows = overdue.get("rows") or []
    check(
        "K4-01 超期 1 条为任务 123（2026Q2→2026-06-30，逾期 46 天），另有 34 条写法判不了",
        len(od_rows) == 1
        and str(od_rows[0].get("task_id")) == "123"
        and str(od_rows[0].get("deadline")) == "2026-06-30"
        and str(od_rows[0].get("days_overdue")) == "46"
        and str(overdue.get("unparsable_count")) == "34",
        f"rows={od_rows} unparsable={overdue.get('unparsable_count')}",
    )
    check(
        "K4-01 口径把「无法判断」与「没超期」分开",
        "是「无法判断」而不是「没超期」" in str(overdue.get("caliber", "")),
    )

    # K4-04. 行内自相矛盾（未开始却写了成效）与两表文本一致性是两个不同的口径。
    conflict = await _call(registry, "weekly_group_stats", scope="status_effect_conflict")
    check(
        "K4-04 状态与成效自相矛盾恰为 6 条任务 97/108/130/137/140/142",
        sorted(int(r.get("task_id")) for r in (conflict.get("rows") or [])) == [97, 108, 130, 137, 140, 142],
        f"ids={[r.get('task_id') for r in (conflict.get('rows') or [])]}",
    )
    check(
        "K4-04 口径区分本档与 effect_consistency",
        "与 scope=effect_consistency 不是一回事" in str(conflict.get("caliber", "")),
    )

    # 未知口径必须报错并列出支持值，而不是静默退回默认档。
    bad_scope = await _call(registry, "weekly_attachment_stats", scope="by_weekday")
    check(
        "附件统计不支持的口径显式报错并列出支持值",
        bad_scope.get("ok") is False
        and bad_scope.get("error", {}).get("code") == "unsupported_scope"
        and "by_progress" in str(bad_scope.get("error", {}).get("message", "")),
        f"err={bad_scope.get('error')}",
    )
    bad_person = await _call(registry, "weekly_person_stats", scope="salary")
    check(
        "人员统计不支持的口径显式报错",
        bad_person.get("ok") is False and bad_person.get("error", {}).get("code") == "unsupported_scope",
        f"err={bad_person.get('error')}",
    )

    # --- A4: 集合边界 --------------------------------------------------------
    # L 类。同一份数据、同一个度量，三种并列口径的行数各不相同。这三条断言绑在
    # 一起才有意义：任何一档退化成另一档，都会让「前 3 名」答出别人的行数。
    cut3 = await _call(registry, "weekly_rank", metric="progress_rounds", mode="cut", top=3)
    check(
        "L2-03 cut 硬切 3 条，口径写明边界外并列不补列",
        cut3.get("row_count") == 3
        and "硬切前 3 条" in str(cut3.get("caliber", ""))
        and "不要补列" in str(cut3.get("caliber", "")),
        f"row_count={cut3.get('row_count')} caliber={str(cut3.get('caliber'))[:160]}",
    )
    ties3 = await _call(registry, "weekly_rank", metric="progress_rounds", mode="keep_ties", top=3)
    check(
        "L3-01 keep_ties 前 3 名共 12 行（并列全列），且每行带 rk",
        ties3.get("row_count") == 12
        and all("rk" in r for r in ties3.get("rows", []))
        and ties3.get("has_more") is False,
        f"row_count={ties3.get('row_count')}",
    )
    per_group = await _call(
        registry, "weekly_rank", metric="progress_rounds", mode="per_group", group_by="project_group"
    )
    check(
        "L4-01 per_group 一组一行共 11 行，不受 top 影响且未截断",
        per_group.get("row_count") == 11
        and per_group.get("has_more") is False
        and len({r.get("bucket") for r in per_group.get("rows", [])}) == 11,
        f"row_count={per_group.get('row_count')} has_more={per_group.get('has_more')}",
    )
    # ascending 那一端必须留住零值行：期数为 0 的任务正是「最少」的答案，
    # INNER JOIN 会把它们整行丢掉（inner_join_drops_zero）。
    fewest = await _call(registry, "weekly_rank", metric="progress_rounds", mode="cut", top=5, ascending=True)
    check(
        "L 类 ascending 保留零期数任务（LEFT JOIN，非 INNER）",
        fewest.get("row_count") == 5 and all(int(r.get("metric_value", -1)) == 0 for r in fewest.get("rows", [])),
        f"rows={[(r.get('task_id'), r.get('metric_value')) for r in fewest.get('rows', [])]}",
    )
    bad_metric = await _call(registry, "weekly_rank", metric="salary")
    check(
        "weekly_rank 未知 metric 报错并列出值域",
        bad_metric.get("ok") is False
        and bad_metric.get("error", {}).get("code") == "unsupported_metric"
        and "progress_rounds" in str(bad_metric.get("error", {}).get("message", "")),
        f"err={bad_metric.get('error')}",
    )
    bad_mode = await _call(registry, "weekly_rank", mode="dense_rank")
    check(
        "weekly_rank 未知 mode 报错并列出三档",
        bad_mode.get("ok") is False and bad_mode.get("error", {}).get("code") == "unsupported_mode",
        f"err={bad_mode.get('error')}",
    )
    no_axis = await _call(registry, "weekly_rank", mode="per_group")
    check(
        "per_group 缺 group_by 报错而非静默退回全局排名",
        no_axis.get("ok") is False and no_axis.get("error", {}).get("code") == "unsupported_group_by",
        f"err={no_axis.get('error')}",
    )

    # B4-01. 任务只挂二级分类，一级分类要经 parent_id 上跳；按 category 分组
    # 会返回 47 档，那是另一个问题的答案。
    pcat = await _call(registry, "weekly_aggregate", group_by="primary_category", board="tech")
    check(
        "B4-01 技术组一级分类 6 档，首档 关键技术攻关 18",
        pcat.get("row_count") == 6
        and (pcat.get("rows") or [{}])[0].get("group_name") == "关键技术攻关"
        and int((pcat.get("rows") or [{}])[0].get("cnt", 0)) == 18,
        f"rows={[(r.get('group_name'), r.get('cnt')) for r in pcat.get('rows', [])]}",
    )
    check(
        "一级分类口径点明不是二级分类，且看板过滤走分类树",
        "不是二级分类" in str(pcat.get("caliber", "")),
    )
    # B4-03. 9 个二级分类并列 5 个任务，「前 5」必须硬切，并告知总组数。
    cat5 = await _call(registry, "weekly_aggregate", group_by="category", top=5)
    check(
        "B4-03 二级分类硬切 5 组并回显共 47 组",
        cat5.get("row_count") == 5 and cat5.get("total_groups") == 47 and "共 47 组" in str(cat5.get("caliber", "")),
        f"row_count={cat5.get('row_count')} total_groups={cat5.get('total_groups')}",
    )

    # G2-01. 「在办任务没定目标」问的是 status IN (0, 1) 那批；不加这道过滤会
    # 把已完成/已暂停的混进来（11 行 vs 10 行）。
    miss_all = await _call(registry, "weekly_year_goal_stats", scope="missing", year=2026, top=200)
    miss_open = await _call(
        registry, "weekly_year_goal_stats", scope="missing", year=2026, top=200, in_progress_only=True
    )
    check(
        "G2-01 2026 无目标共 11 个，其中在办 10 个（0 未开始同样在办）",
        miss_all.get("total_count") == 11
        and miss_open.get("total_count") == 10
        and "仅在办任务" in str(miss_open.get("caliber", "")),
        f"all={miss_all.get('total_count')} open={miss_open.get('total_count')}",
    )

    # O4-01. 单任务里程碑此前没有 task 参数，问「任务 19 有哪些里程碑」会拿回
    # 全board首页——一个完整、像样、但属于另一个问题的答案。
    ms19 = await _call(registry, "weekly_milestone_query", task="19")
    check(
        "O4-01 任务 19 恰好 2 条里程碑，按任务内 sort_order 编排",
        ms19.get("total_count") == 2
        and {int(r.get("task_id", 0)) for r in ms19.get("rows", [])} == {19}
        and [int(r.get("sort_order", 0)) for r in ms19.get("rows", [])] == [1, 2]
        and "sort_order" in str(ms19.get("caliber", "")),
        f"total={ms19.get('total_count')} "
        f"rows={[(r.get('task_id'), r.get('sort_order')) for r in ms19.get('rows', [])]}",
    )
    ms_all = await _call(registry, "weekly_milestone_query")
    check(
        "里程碑不带 task 时覆盖全部 474 条（明细截断但总数精确）",
        ms_all.get("total_count") == 474 and ms_all.get("has_more") is True,
        f"total={ms_all.get('total_count')}",
    )

    # C6-02. 未发布进展的 status 是进展行自己的审批码值，与任务的
    # workflow_status 是两套词汇，套错就答成 published/pending_audit。
    unpub = await _call(registry, "weekly_progress_coverage", scope="unpublished")
    check(
        "C6-02 未发布进展 草稿 26 / 待审核 58 / 驳回 39，合计 123",
        [(int(r.get("status", -1)), int(r.get("cnt", 0))) for r in unpub.get("rows", [])] == [(0, 26), (1, 58), (2, 39)]
        and unpub.get("total_count") == 123,
        f"rows={unpub.get('rows')} total={unpub.get('total_count')}",
    )
    check(
        "未发布进展口径显式排除拿 workflow_status 来套",
        "不要拿任务的 workflow_status" in str(unpub.get("caliber", "")),
    )
    gaps = await _call(registry, "weekly_progress_coverage", scope="version_gaps")
    check(
        "期号缺号 5 个任务，missing_count = 最大期号 - 实际期数",
        gaps.get("row_count") == 5
        and all(
            int(r.get("max_version", 0)) - int(r.get("rounds", 0)) == int(r.get("missing_count", -1))
            for r in gaps.get("rows", [])
        ),
        f"rows={gaps.get('rows')}",
    )

    # 库里真实存在的 completion_time 写法有 28 种，含「持续推进」这类非日期文本。
    ct_values = await _call(registry, "weekly_group_stats", scope="completion_time_values", top=200)
    check(
        "completion_time 去重后 28 种，含非日期文本且原样返回",
        ct_values.get("total_count") == 28
        and "持续推进" in {str(r.get("completion_time")) for r in ct_values.get("rows", [])}
        and "不要归纳成自己的类别名" in str(ct_values.get("caliber", "")),
        f"total={ct_values.get('total_count')}",
    )
    effect = await _call(registry, "weekly_group_stats", scope="effect_consistency", top=200)
    check(
        "成效一致性 46 行，不一致的排最前（same 升序）",
        effect.get("row_count") == 46
        and [int(r.get("same", -1)) for r in effect.get("rows", [])]
        == sorted(int(r.get("same", -1)) for r in effect.get("rows", [])),
        f"row_count={effect.get('row_count')}",
    )

    # 任务 workflow_status 与最新提交单 status 是两套码值，跨两次调用用眼比对
    # 会把行集混掉；这一档专门做这次比较，并且故意不加发布闸门。
    mismatch = await _call(registry, "weekly_submission_query", status_mismatch=True)
    check(
        "任务状态与最新提交单状态不一致 16 个，口径说明不加发布闸门",
        mismatch.get("row_count") == 16
        and "不加发布闸门" in str(mismatch.get("caliber", ""))
        and all(r.get("workflow_status") != r.get("latest_submission_status") for r in mismatch.get("rows", [])),
        f"row_count={mismatch.get('row_count')}",
    )

    # 动作日志比 200 行上限长，所以过滤必须落在服务端；值域外的 action 报错，
    # 不能静默不过滤——那会返回全量并看着像答案。
    bad_action = await _call(registry, "weekly_workflow_query", action="submit")
    check(
        "action 值域外报错并列出四个真实取值（不静默退回全量）",
        bad_action.get("ok") is False
        and bad_action.get("error", {}).get("code") == "unsupported_action"
        and "submitted" in str(bad_action.get("error", {}).get("message", ""))
        and "[" not in str(bad_action.get("error", {}).get("message", "")),
        f"err={bad_action.get('error')}",
    )
    by_task = await _call(registry, "weekly_workflow_query", by_task=True)
    check(
        "动作日志按任务聚合 150 行，口径点明 action_count 是次数",
        by_task.get("row_count") == 150 and "次数不是任务数" in str(by_task.get("caliber", "")),
        f"row_count={by_task.get('row_count')}",
    )

    # 「哪个月上报最多」的裁决落服务端：返回 19 行让模型自己挑，它会把
    # 2026-02(61) 和名次靠后的月份看成并列。
    peak = await _call(registry, "weekly_progress_range", by="month", peak=True)
    check(
        "峰值月 2026-02 共 61 条，单行返回且无假截断信号",
        peak.get("row_count") == 1
        and (peak.get("rows") or [{}])[0].get("bucket") == "2026-02"
        and int((peak.get("rows") or [{}])[0].get("progress_count", 0)) == 61
        and peak.get("has_more") is False,
        f"rows={peak.get('rows')} has_more={peak.get('has_more')}",
    )

    # 滞后清单：在办含 status 0，从未上报（NULL）算滞后且排最前。
    stale = await _call(registry, "weekly_freshness_distribution", stale_days=30)
    check(
        "滞后 30 天的在办任务 46 个，从未上报排最前且 days_since 为空",
        stale.get("total_count") == 46
        and (stale.get("rows") or [{}])[0].get("latest_progress_time") is None
        and (stale.get("rows") or [{}])[0].get("days_since") is None
        and "0 未开始同样在办" in str(stale.get("caliber", "")),
        f"total={stale.get('total_count')} first={(stale.get('rows') or [{}])[0]}",
    )
    # 与上面那份在办清单同源要显式加 in_flight：分组视图默认不加在办闸门
    # （占比题的分母是全部 128 条正式任务），加了闸门分母才是 92、滞后才是 46。
    stale_pg = await _call(registry, "weekly_freshness_distribution", stale_days=30, by="project_group", in_flight=True)
    check(
        "滞后按专项组分组 11 组，在办闸门下各组相加等于 46",
        stale_pg.get("row_count") == 11
        and sum(int(r.get("stale_count", 0)) for r in stale_pg.get("rows", [])) == 46
        and sum(int(r.get("total", 0)) for r in stale_pg.get("rows", [])) == 92,
        f"rows={[(r.get('bucket'), r.get('stale_count'), r.get('total')) for r in stale_pg.get('rows', [])]}",
    )
    # 不加闸门时分母回到 128、滞后 65：两个数各自成立，差别只在闸门，
    # 一旦谁把默认改回自带在办，这条会连同 K2-03 的首行一起红。
    stale_pg_all = await _call(registry, "weekly_freshness_distribution", stale_days=30, by="project_group")
    check(
        "同一分组视图不加在办闸门时分母 128、滞后 65",
        sum(int(r.get("total", 0)) for r in stale_pg_all.get("rows", [])) == 128
        and sum(int(r.get("stale_count", 0)) for r in stale_pg_all.get("rows", [])) == 65
        and "要只看在办请加 in_flight=true" in str(stale_pg_all.get("caliber", "")),
        f"rows={[(r.get('bucket'), r.get('stale_count'), r.get('total')) for r in stale_pg_all.get('rows', [])]}",
    )
    # L2-01. 「最久没上报的 5 个」与「从来没报过的」是两问：不排除从未上报的，
    # 前 5 行会被 8 条无天数可比的占满，与真答案零交集。两侧都钉住。
    oldest = await _call(registry, "weekly_freshness_distribution", stale_days=1, reported_only=True, limit=5)
    check(
        "L2-01 排除从未上报后最久那 5 条按天数倒序，首条任务 1 达 250 天",
        [(r.get("id"), r.get("days_since")) for r in (oldest.get("rows") or [])]
        == [(1, 250), (24, 225), (60, 218), (87, 190), (11, 166)]
        and str(oldest.get("never_reported_count")) == "8",
        f"rows={[(r.get('id'), r.get('days_since')) for r in (oldest.get('rows') or [])]}",
    )
    oldest_all = await _call(registry, "weekly_freshness_distribution", stale_days=1, limit=5)
    check(
        "L2-01 不排除时前 5 行全是从未上报（days_since 为空），与上面零交集",
        all(r.get("days_since") is None for r in (oldest_all.get("rows") or []))
        and not ({r.get("id") for r in (oldest_all.get("rows") or [])} & {1, 24, 60, 87, 11})
        and "应加 reported_only=true" in str(oldest_all.get("caliber", "")),
        f"ids={[r.get('id') for r in (oldest_all.get('rows') or [])]}",
    )

    recent = await _call(registry, "weekly_freshness_distribution", recent_days=7)
    check(
        "近 7 天上报 23 个任务，不加 status 过滤（问的是有无上报）",
        recent.get("row_count") == 23 and "不加 status 过滤" in str(recent.get("caliber", "")),
        f"row_count={recent.get('row_count')}",
    )
    bad_by = await _call(registry, "weekly_freshness_distribution", stale_days=30, by="owner")
    check(
        "滞后清单不支持的分组轴显式报错",
        bad_by.get("ok") is False and bad_by.get("error", {}).get("code") == "unsupported_group_by",
        f"err={bad_by.get('error')}",
    )

    # --- role-split counting (weekly_task_query ORs the owner columns) ------
    roles = await _call(registry, "weekly_owner_roles", person="u3208")
    role_row = (roles.get("rows") or [{}])[0]
    check(
        "按角色分别计数，四个维度齐全",
        {"as_owner", "as_project_owner", "as_lead_owner", "any_role"} <= set(role_row),
        f"keys={sorted(role_row)}",
    )
    check(
        "any_role 是三角色去重并集，不小于任一单角色",
        bool(role_row)
        and int(role_row["any_role"])
        >= max(int(role_row["as_owner"]), int(role_row["as_project_owner"]), int(role_row["as_lead_owner"])),
        f"row={role_row}",
    )

    # --- schema exposes column lists, minus blocked fields -----------------
    schema = await _call(registry, "weekly_schema")
    table_columns = schema.get("table_columns") or {}
    attachment_columns = table_columns.get("task_attachment") or []
    check(
        "字段清单可查（此前只能从样例行反推，漏掉 is_deleted）",
        "is_deleted" in attachment_columns and len(attachment_columns) >= 9,
        f"task_attachment={attachment_columns}",
    )
    check(
        "字段清单本身不含禁止外泄字段",
        all("storage_path" not in cols and "payload" not in cols for cols in table_columns.values()),
    )

    # --- aggregate capabilities that replace hand-counting ------------------
    # Without these the agent walked every task one detail call at a time (43
    # calls on one question) and ran out of tool rounds before answering.
    completeness = await _call(registry, "weekly_field_completeness", field="overall_goal")
    comp_row = (completeness.get("rows") or [{}])[0]
    check(
        "R-07/R-19 字段填报完整度一次调用可得",
        {"total", "filled", "missing"} <= set(comp_row),
        f"row={comp_row}",
    )
    check(
        "完整度统计自洽：filled + missing = total",
        bool(comp_row) and int(comp_row["filled"]) + int(comp_row["missing"]) == int(comp_row["total"]),
        f"row={comp_row}",
    )
    listing = await _call(registry, "weekly_field_completeness")
    check(
        "空参数返回支持字段清单而非报错",
        listing.get("ok") is True and bool(listing.get("supported_fields")),
    )
    bad_field = await _call(registry, "weekly_field_completeness", field="task_name; DROP TABLE task")
    check(
        "字段名走白名单，注入式入参被拒",
        bad_field.get("ok") is False and bad_field["error"]["code"] == "unsupported_field",
        str(bad_field.get("error"))[:90],
    )

    coverage = await _call(registry, "weekly_progress_coverage")
    cov_row = (coverage.get("rows") or [{}])[0]
    check(
        "进展覆盖度含行数/任务数/起止日期/最大版本",
        {"progress_rows", "tasks_covered", "earliest", "latest", "max_version"} <= set(cov_row),
        f"keys={sorted(cov_row)}",
    )

    ranking = await _call(registry, "weekly_task_ranking", metric="attachments", top=5)
    counts = [int(r["cnt"]) for r in ranking.get("rows", [])]
    check(
        "任务排名按计数降序且可复现",
        counts == sorted(counts, reverse=True) and len(counts) == 5,
        f"counts={counts}",
    )
    bad_metric = await _call(registry, "weekly_task_ranking", metric="不支持")
    check(
        "不支持的排名指标明确报错",
        bad_metric.get("ok") is False and bad_metric["error"]["code"] == "unsupported_metric",
    )

    # --- freshness anchors relative time -----------------------------------
    fresh = await _call(registry, "weekly_freshness")
    check(
        "weekly_freshness 给出快照时间用于锚定相对时间",
        bool(fresh.get("rows")) and all(r.get("latest_progress") for r in fresh["rows"]),
        f"rows={fresh.get('rows')}",
    )
    # G-B03/B04. latest_progress_time 含未发布行,技术组因此读成 08-09,而它最新的
    # 正式进展是 07-31 —— 九天差。问「数据更新到什么时候」要的是正式口径,答
    # latest_progress 就是报了一个没有正式记录支撑的日期。两个看板各取自己的表,
    # 集团组在 task_group_progress_history,漏了它会把集团组算成 NULL(即「没数据」)。
    pub = {r["board_name"]: r for r in fresh.get("published_progress") or []}
    tech_pub = next((v for k, v in pub.items() if "技术组" in k), {})
    grp_pub = next((v for k, v in pub.items() if "集团" in k), {})
    check(
        "G-B03 技术组正式进展日期取 is_published=1 的 07-31,不跟 latest_progress 的 08-09",
        str(tech_pub.get("newest_published_progress", "")).startswith("2026-07-31"),
        f"tech={tech_pub}",
    )
    check(
        "G-B04 集团组正式进展日期从 task_group_progress_history 取到 08-14,而非 NULL",
        str(grp_pub.get("newest_published_progress", "")).startswith("2026-08-14"),
        f"group={grp_pub}",
    )
    check(
        "G-B03 技术组导入批次分开报「已完成」和「仍在处理」两个日期",
        (fresh.get("tech_import") or {}).get("newest_finished_batch") == "2026-07-31"
        and (fresh.get("tech_import") or {}).get("newest_unfinished_batch") == "2026-08-15",
        f"tech_import={fresh.get('tech_import')}",
    )

    # G-B05/B06. 落后天数分档:两个看板的正式进展在不同表,分档必须按 board_id 分流。
    # 技术组各档与快照日无关(最新正式进展 07-31 已落在 15 天外),集团组会随快照日
    # 移动 —— 08-15 是 17/28/1,金标绑的 08-17 是 14/28/4,三项跨过 7 天线。这里断言
    # 默认锚点,跨档迁移由「总数守恒」兜住。
    bands = await _call(registry, "weekly_freshness_distribution", lag_bands=True)
    band_map = {(r["board_name"], r["lag_band"]): r["task_count"] for r in bands.get("rows") or []}
    tech_bands = {k[1]: v for k, v in band_map.items() if "技术组" in k[0]}
    grp_bands = {k[1]: v for k, v in band_map.items() if "集团" in k[0]}
    check(
        "G-B05 技术组 82 项分档为 15-30 天 17 项 / 超 30 天 56 项 / 无正式进展 9 项",
        tech_bands.get("3 15-30 天") == 17
        and tech_bands.get("4 超过 30 天") == 56
        and tech_bands.get("5 无正式进展") == 9,
        f"tech_bands={tech_bands}",
    )
    check(
        "G-B06 集团组 46 项全有正式进展,总数守恒且无「无正式进展」档",
        sum(grp_bands.values()) == 46 and "5 无正式进展" not in grp_bands,
        f"grp_bands={grp_bands}",
    )
    check(
        "G-B05/B06 分档总数等于两看板正式任务数 82+46,不漏不重",
        sum(tech_bands.values()) == 82 and sum(band_map.values()) == 128,
        f"tech={sum(tech_bands.values())} all={sum(band_map.values())}",
    )

    # --- error paths are explicit, never fabricated -------------------------
    missing = await _call(registry, "weekly_task_detail", task="根本不存在的任务zzz999")
    check(
        "查不到的任务如实报错而非编造",
        missing.get("ok") is False and missing["error"]["code"] == "task_not_found",
        str(missing.get("error"))[:100],
    )
    # M2-01. 「不属正式任务」和「库里没这行」是两种错，混成一句话就是死路：
    # 任务 2 存在且有完整审批流水，只是 workflow_status='rejected' 没过 R-01。
    not_formal = await _call(registry, "weekly_task_detail", task="2")
    nf_msg = str((not_formal.get("error") or {}).get("message", ""))
    check(
        "M2-01 存在但非正式的任务报 task_not_formal 并写明卡在哪个 workflow_status",
        not_formal.get("ok") is False
        and (not_formal.get("error") or {}).get("code") == "task_not_formal"
        and "rejected" in nf_msg
        and "R-01" in nf_msg,
        nf_msg[:160],
    )
    check(
        "M2-01 报错同时指路三个外键工具，并明确警告不要改用按名字搜",
        all(name in nf_msg for name in ("weekly_submission_query", "weekly_workflow_query", "weekly_attachment_query"))
        and "不要改用按名字搜" in nf_msg,
        nf_msg[:200],
    )
    absent = await _call(registry, "weekly_task_detail", task="9999")
    check(
        "M2-01 库里真没这行时报 task_not_found，与非正式任务区分开",
        absent.get("ok") is False
        and (absent.get("error") or {}).get("code") == "task_not_found"
        and "确实没有这行" in str((absent.get("error") or {}).get("message", "")),
        str(absent.get("error"))[:120],
    )
    # 指路的三个工具必须真能答出任务 2 的审批流，否则上面那句指路是空头承诺。
    flow2 = await _call(registry, "weekly_workflow_query", task="2")
    check(
        "M2-01 非正式任务的审批流水仍按 task_id 外键查得到，5 条到 rejected 为止",
        [(r.get("node_type"), r.get("action")) for r in (flow2.get("rows") or [])]
        == [
            ("admin", "created"),
            ("fill", "submitted"),
            ("sign", "approved"),
            ("audit", "rejected"),
            ("audit", "rejected"),
        ],
        f"rows={[(r.get('node_type'), r.get('action')) for r in (flow2.get('rows') or [])]}",
    )
    # M2-03. 意见原文属敏感字段，用户自称审批人不能放宽——遮蔽是设计内行为。
    check(
        "M2-03 demo 凭证下 opinion 一律遮蔽，且 caliber 说明是按权限而非无数据",
        all(r.get("opinion") == "[按权限不展示]" for r in (flow2.get("rows") or []))
        and "按权限展示" in str(flow2.get("caliber", "")),
        str(flow2.get("caliber"))[:120],
    )
    # M3-03. 域外过滤词等于没过滤：approved 不在提交单状态值域里（那是审批动作的词）。
    excl_pub = await _call(registry, "weekly_submission_query", reporter="u3208", exclude_status="published")
    check(
        "M3-03 「提交但还没发布」用 exclude_status=published 得 4 条",
        [(r.get("task_id"), r.get("round_no"), r.get("status")) for r in (excl_pub.get("rows") or [])]
        == [(67, 1, "pending_fill"), (90, 3, "rejected"), (111, 3, "rejected"), (122, 1, "pending_audit")],
        f"rows={[(r.get('task_id'), r.get('status')) for r in (excl_pub.get('rows') or [])]}",
    )
    excl_appr = await _call(registry, "weekly_submission_query", reporter="u3208", exclude_status="approved")
    check(
        "M3-03 拿域外词 approved 去排等于没排：29 条全量，caliber 点明不能说成已排除",
        excl_appr.get("total_count") == 29
        and "不在提交单状态值域" in str(excl_appr.get("caliber", ""))
        and "不要说成「已排除」" in str(excl_appr.get("caliber", "")),
        f"total={excl_appr.get('total_count')}",
    )
    # O2-02. 看板过滤只落在计数上时，行清单永远是跨看板的 47 个分类，另一看板的
    # 19 个只是变成 cnt=0，和「本看板确实没有任务的分类」长得一样。
    cat_tech = await _call(registry, "weekly_aggregate", group_by="category", board="tech", top=200)
    cat_group = await _call(registry, "weekly_aggregate", group_by="category", board="group", top=200)
    tech_rows = cat_tech.get("rows") or []
    group_rows = cat_group.get("rows") or []
    check(
        "O2-02 技术组分类清单 28 个（7 个一级 + 21 个二级），不含另一看板的分类",
        cat_tech.get("row_count") == 28
        and sum(1 for r in tech_rows if not r.get("parent_id")) == 7
        and sum(1 for r in tech_rows if r.get("parent_id")) == 21,
        f"row_count={cat_tech.get('row_count')} 一级={sum(1 for r in tech_rows if not r.get('parent_id'))}",
    )
    check(
        "O2-02 集团组分类清单 19 个（5 + 14），两看板相加等于跨看板的 47",
        cat_group.get("row_count") == 19
        and sum(1 for r in group_rows if not r.get("parent_id")) == 5
        and sum(1 for r in group_rows if r.get("parent_id")) == 14
        and int(cat_tech.get("row_count") or 0) + int(cat_group.get("row_count") or 0) == 47,
        f"row_count={cat_group.get('row_count')} 一级={sum(1 for r in group_rows if not r.get('parent_id'))}",
    )
    check(
        "O2-02 口径点明一二级要分别报，且 cnt=0 不等于「属于另一个看板」",
        "要按这两级分别报" in str(cat_tech.get("caliber", ""))
        and "另一看板的分类根本不在清单里" in str(cat_tech.get("caliber", "")),
        str(cat_tech.get("caliber", ""))[:160],
    )
    # 分类树上的任务数不受过滤影响：技术组 82 + 集团组 46 = 128 个正式任务。
    tech_sum = sum(int(r.get("cnt") or 0) for r in tech_rows)
    group_sum = sum(int(r.get("cnt") or 0) for r in group_rows)
    check(
        "O2-02 两看板分类下任务数合计仍是 128，过滤没把任务丢掉",
        tech_sum == 82 and group_sum == 46,
        f"tech={tech_sum} group={group_sum}",
    )

    # O3-03. 「任务 2 的附件一共多大」原先没有单任务档，只能翻清单手工加总。
    att2 = await _call(registry, "weekly_attachment_stats", scope="summary", task="2")
    att2_row = (att2.get("rows") or [{}])[0]
    check(
        "O3-03 单任务附件总量一次调用得出：2 个文件 6914081 字节",
        att2_row.get("attachment_count") == 2 and att2_row.get("total_bytes") == "6914081",
        f"row={att2_row}",
    )
    check(
        "O3-03 单任务档不加正式任务闸门，口径写明原因（任务 2 是 rejected）",
        "本档不加正式任务闸门" in str(att2.get("caliber", "")),
        str(att2.get("caliber", ""))[:140],
    )
    # 单任务档不能污染全局档：454 条是全部正式任务上的活跃附件。
    att_all = await _call(registry, "weekly_attachment_stats", scope="summary")
    check(
        "O3-03 不传 task 时仍是全局 454 条，单任务档没串味",
        (att_all.get("rows") or [{}])[0].get("attachment_count") == 454,
        f"row={(att_all.get('rows') or [{}])[0]}",
    )
    # 跨任务/全表档传 task 要报错而不是悄悄忽略：静默忽略比报错更糟。
    att_bad = await _call(registry, "weekly_attachment_stats", scope="zero_attachment", task="2")
    check(
        "O3-03 全表口径传 task 报 task_not_applicable，不做静默忽略",
        att_bad.get("ok") is False
        and (att_bad.get("error") or {}).get("code") == "task_not_applicable"
        and "scope=summary" in str((att_bad.get("error") or {}).get("message", "")),
        str(att_bad.get("error"))[:140],
    )
    att_absent = await _call(registry, "weekly_attachment_stats", scope="summary", task="9999")
    check(
        "O3-03 库里没这个 id 时报 task_not_found，不回一行 count=0",
        att_absent.get("ok") is False and (att_absent.get("error") or {}).get("code") == "task_not_found",
        str(att_absent.get("error"))[:120],
    )

    # O7-03. 进展行按月报，短窗口在 task_progress 上恒为 0；那个 0 是真的，但答不了
    # 「最近一周哪些任务更新了进展」——那问的是 task.latest_progress_time。
    rng7 = await _call(registry, "weekly_progress_range", last_days=7)
    check(
        "O7-03 近 7 天进展行 0 条，口径点明是月度节奏而非「没人报」并指向 recent_days",
        rng7.get("total_count") == 0
        and "不是「没人报进展」" in str(rng7.get("caliber", ""))
        and "recent_days=7" in str(rng7.get("caliber", ""))
        and "也不要退而报" in str(rng7.get("caliber", "")),
        str(rng7.get("caliber", ""))[-200:],
    )
    recent7 = await _call(registry, "weekly_freshness_distribution", recent_days=7)
    check(
        "O7-03 指路的 recent_days=7 真能答出 23 条，首行是任务 103（08-14）",
        recent7.get("row_count") == 23
        and (recent7.get("rows") or [{}])[0].get("id") == 103
        and (recent7.get("rows") or [{}])[0].get("days_since") == 1,
        f"row_count={recent7.get('row_count')} first={(recent7.get('rows') or [{}])[0]}",
    )
    # 分组档同样要带上这句，否则 by=task 的 0 行还是死路。
    rng7_task = await _call(registry, "weekly_progress_range", last_days=7, by="task")
    check(
        "O7-03 by=task 的空窗口也带同一句指路",
        rng7_task.get("row_count") == 0 and "recent_days=7" in str(rng7_task.get("caliber", "")),
        str(rng7_task.get("caliber", ""))[-120:],
    )
    # 长窗口不该被这句话污染：近 60 天有行，提示不出现。
    rng60 = await _call(registry, "weekly_progress_range", last_days=60, by="month")
    check(
        "O7-03 有行的窗口不加空窗提示",
        rng60.get("row_count", 0) > 0 and "不是「没人报进展」" not in str(rng60.get("caliber", "")),
        f"row_count={rng60.get('row_count')}",
    )

    # O6-01. 「李建华负责哪些任务」有两个总体：task 上的单值 lead_owner_name（14 条）
    # 与集团看板明细表的多值牵头人列（3 条）。基线把两边并起来，于是多两条少一条。
    own_task = await _call(registry, "weekly_task_query", owner="李建华", limit=200)
    own_group = await _call(registry, "weekly_group_owner_query", person="李建华")
    check(
        "O6-01 task 表口径 14 条，与 gold 的 id 集合一致",
        sorted(r.get("id") for r in (own_task.get("rows") or []))
        == [4, 5, 11, 32, 39, 44, 46, 52, 61, 74, 88, 91, 127, 136],
        f"ids={sorted(r.get('id') for r in (own_task.get('rows') or []))}",
    )
    check(
        "O6-01 明细表多值口径只有 3 条，且与 task 口径不是同一集合",
        own_group.get("row_count") == 3
        and {r.get("task_id") for r in (own_group.get("rows") or [])} == {117, 127, 145}
        and "两个不同总体" in str(own_group.get("caliber", ""))
        and "不要把两边结果并起来" in str(own_group.get("caliber", "")),
        f"ids={[r.get('task_id') for r in (own_group.get('rows') or [])]}",
    )

    # Q5-04. 「有多少条进展是手工填的」判据只有 task_progress.import_id 一列，
    # 此前没有任何工具暴露它，模型只能答「无法精确统计」，而真答案是 0。
    imp_split = await _call(registry, "weekly_progress_coverage", scope="import_split")
    imp_row = (imp_split.get("rows") or [{}])[0]
    check(
        "Q5-04 已发布进展 943 全部来自导入，手工 0 条",
        int(imp_row.get("total") or 0) == 943
        and int(imp_row.get("from_import") or 0) == 943
        and int(imp_row.get("manual") or -1) == 0,
        f"total={imp_row.get('total')} import={imp_row.get('from_import')} manual={imp_row.get('manual')}",
    )
    check(
        "Q5-04 口径写明这个 0 是查得出来的，且给出另一套闸门的数",
        "不是取不到数" in str(imp_split.get("caliber", ""))
        and "948" in str(imp_split.get("caliber", ""))
        and "118" in str(imp_split.get("caliber", "")),
        str(imp_split.get("caliber", ""))[-140:],
    )

    # Q2-03. 附件清单档天生只给一页（top 默认 8，共 46 条任务），基线照那 8 行
    # 手数分布，数出 21/4/4 而真值是 17/3/5。分布必须服务端算完再回。
    att_dist = await _call(registry, "weekly_group_stats", scope="attachment_distribution", top=20)
    dist = {r.get("attachments"): r.get("tasks") for r in (att_dist.get("rows") or [])}
    check(
        "Q2-03 附件分布 0→18 / 1→17 / 2→3 / 3→5 / 4→2 / 6→1，各档相加 46",
        dist == {0: 18, 1: 17, 2: 3, 3: 5, 4: 2, 6: 1} and sum(dist.values()) == 46,
        f"dist={dist}",
    )
    check(
        "Q2-03 分布档说明零附件由 LEFT JOIN 保住、档位不连续属正常",
        "零附件档由 LEFT JOIN 保住" in str(att_dist.get("caliber", ""))
        and "档位不连续" in str(att_dist.get("caliber", "")),
        str(att_dist.get("caliber", ""))[-140:],
    )
    # 截断的清单必须自报截断并指向分布档；给满 46 行时这句话不该出现。
    att8 = await _call(registry, "weekly_group_stats", scope="attachments", top=8)
    att46 = await _call(registry, "weekly_group_stats", scope="attachments", top=46)
    check(
        "Q2-03 截断的清单自报只有一页并指向分布档",
        att8.get("row_count") == 8
        and "attachment_distribution" in str(att8.get("caliber", ""))
        and "共 46 条任务" in str(att8.get("caliber", "")),
        str(att8.get("caliber", ""))[-120:],
    )
    check(
        "Q2-03 给满 46 行时不加截断提示",
        att46.get("row_count") == 46 and "attachment_distribution" not in str(att46.get("caliber", "")),
        f"row_count={att46.get('row_count')}",
    )

    # Q3-01. 不带人名的 owner 榜按 owner_count DESC 排且一次只带一个角色列，
    # 「前 8 条」落在人多的那批（101,105,…）而非 gold 的 task_id 顺序 97..104。
    roster = await _call(registry, "weekly_group_owner_query", role="lead", limit=8)
    check(
        "Q3-01 owner 榜前 8 条按挂名人数排，不是 task_id 顺序",
        [r.get("task_id") for r in (roster.get("rows") or [])][:2] == [101, 105]
        and "不是看板花名册" in str(roster.get("caliber", ""))
        and "weekly_group_detail_query fields=lead_owner_names,project_owner_names" in str(roster.get("caliber", "")),
        f"ids={[r.get('task_id') for r in (roster.get('rows') or [])]}",
    )
    both = await _call(
        registry,
        "weekly_group_detail_query",
        fields="lead_owner_names,project_owner_names",
        limit=8,
    )
    check(
        "Q3-01 明细表一次出两列且按最新进展时间定序，前 8 条与 gold 一致",
        [r.get("task_id") for r in (both.get("rows") or [])] == [103, 128, 113, 149, 116, 150, 138, 101]
        and all(r.get("lead_owner_names") and r.get("project_owner_names") for r in (both.get("rows") or [])),
        f"ids={[r.get('task_id') for r in (both.get('rows') or [])]}",
    )

    # Q4-03. completion_time 是自由文本（46 条 28 种写法），「2026 年内完成」只能
    # 按年份数字扫得 31 条；拿「2026年内」当检索词只命中字面相同的 5 条。
    ct_year = await _call(registry, "weekly_group_detail_query", contains="2026", field="completion_time", limit=200)
    ct_text = await _call(
        registry, "weekly_group_detail_query", contains="2026年内", field="completion_time", limit=200
    )
    check(
        "Q4-03 按年份数字扫得 31 条，并说明这是唯一可靠的年份过滤方式",
        ct_year.get("total_count") == 31
        and "唯一可靠的年份过滤方式" in str(ct_year.get("caliber", ""))
        and "28 种写法" in str(ct_year.get("caliber", "")),
        f"total={ct_year.get('total_count')}",
    )
    check(
        "Q4-03 用完整表述只得 5 条，口径自报全年真数 31 并给出改法",
        ct_text.get("total_count") == 5
        and "不等于「2026年到期的任务」" in str(ct_text.get("caliber", ""))
        and "共 31 条" in str(ct_text.get("caliber", ""))
        and "contains=2026" in str(ct_text.get("caliber", "")),
        str(ct_text.get("caliber", ""))[-140:],
    )

    # Q1-02. 集团看板任务在 task_progress 里 0 行，空的 recent_progress 不说明
    # 「进展在另一张表」就会被当成没报过进展——基线为此耗了 6 轮 13 次调用，
    # 而答案就在同一次返回的 group_detail.progress_effect 里。
    td99 = await _call(registry, "weekly_task_detail", task="99")
    td4 = await _call(registry, "weekly_task_detail", task="4")
    check(
        "Q1-02 集团任务的空 recent_progress 自报进展在 group_detail 里",
        td99.get("recent_progress") == []
        and bool((td99.get("group_detail") or [{}])[0].get("progress_effect"))
        and "recent_progress 为空不代表没报过进展" in str(td99.get("caliber", ""))
        and "weekly_group_history" in str(td99.get("caliber", "")),
        str(td99.get("caliber", ""))[-140:],
    )
    check(
        "Q1-02 技术看板任务有进展行，不加这句提示",
        len(td4.get("recent_progress") or []) == 3 and "recent_progress 为空不代表" not in str(td4.get("caliber", "")),
        f"n={len(td4.get('recent_progress') or [])}",
    )

    # R3-05. 提交单表里没有 board_id，看板在 task 上：不按看板筛，「我提交但还没
    # 发布的集团任务」只能从 462 张单里手挑，基线因此只报了 1 条（真值 18 条）。
    sub_group = await _call(registry, "weekly_submission_query", reporter="宋佳明", board="group", limit=50)
    sub_all = await _call(registry, "weekly_submission_query", reporter="宋佳明", limit=50)
    check(
        "R3-05 按看板筛出宋佳明的 18 条集团提交单，口径写明按 task 的 board_id 过滤",
        sub_group.get("total_count") == 18
        and len(sub_group.get("rows") or []) == 18
        and "仅看板 group" in str(sub_group.get("caliber", "")),
        f"total={sub_group.get('total_count')}",
    )
    check(
        "R3-05 不带看板是跨看板 32 条，两个口径不可互代",
        sub_all.get("total_count") == 32 and "仅看板" not in str(sub_all.get("caliber", "")),
        f"total={sub_all.get('total_count')}",
    )
    sub_bad_board = await _call(registry, "weekly_submission_query", board="不存在的看板")
    check(
        "R3-05 看板名不匹配时明确报错，不静默返回全量",
        sub_bad_board.get("ok") is False and sub_bad_board["error"]["code"] == "board_not_found",
        str(sub_bad_board.get("error"))[:100],
    )

    # R4-01. 年度目标行上没有看板：基线为了「集团看板各任务的年度目标」把
    # weekly_year_goal_query 循环调了 13 次，仍答不出该看板自己的总数。
    goal_group = await _call(registry, "weekly_year_goal_query", board="group", limit=10)
    goal_all = await _call(registry, "weekly_year_goal_query", limit=3)
    check(
        "R4-01 集团看板年度目标 109 行 / 46 任务，前 10 条从 97 号任务起",
        goal_group.get("total_count") == 109
        and goal_group.get("total_tasks") == 46
        and [row["task_id"] for row in goal_group["rows"]][:2] == [97, 97]
        and "仅看板 group" in str(goal_group.get("caliber", "")),
        f"total={goal_group.get('total_count')} tasks={goal_group.get('total_tasks')}",
    )
    check(
        "R4-01 不带看板是全量 313 行 / 128 任务，与看板档不是一个口径",
        goal_all.get("total_count") == 313 and goal_all.get("total_tasks") == 128,
        f"total={goal_all.get('total_count')}",
    )

    # R7-03. 「最近一批跑完的」两个词都是条件：按 data_date 最新的是第 20 批，
    # 它 status 0、实落 0 行，拿它答等于答了一批没跑的（基线六轮耗尽也没给出答案）。
    fin = await _call(registry, "weekly_import_audit", latest_finished=True, limit=50)
    check(
        "R7-03 最近跑完的是第 19 批，影响 17 个任务，选批与列任务一次做完",
        (fin.get("batch") or {}).get("id") == 19
        and (fin.get("batch") or {}).get("status") == 1
        and fin.get("row_count") == 17
        and [row["task_id"] for row in fin["rows"]][:3] == [5, 17, 21],
        f"batch={(fin.get('batch') or {}).get('id')} n={fin.get('row_count')}",
    )
    check(
        "R7-03 口径点名第 20 批 status 0 实落 0 行，不能只按日期取最新",
        "「跑完」是 status = 1，不能只按日期取最新" in str(fin.get("caliber", ""))
        and "第 20 批" in str(fin.get("caliber", "")),
        str(fin.get("caliber", ""))[:140],
    )

    # R8-02. 同一次返回里有两套负责人列，集团看板 46 条任务两边的值全不一样：
    # 基线照 task 行答了「陈志远」，真值是集团明细的「刘海涛,韩雪峰」。
    td101 = await _call(registry, "weekly_task_detail", task="101")
    check(
        "R8-02 两套负责人列不一致时，口径判给 group_detail 的多值列并把两边都点出来",
        td101["task"]["lead_owner_name"] == "陈志远"
        and (td101.get("group_detail") or [{}])[0].get("lead_owner_names") == "刘海涛,韩雪峰"
        and "负责人一律按 group_detail 的多值列" in str(td101.get("caliber", ""))
        and "「陈志远」" in str(td101.get("caliber", ""))
        and "「刘海涛,韩雪峰」" in str(td101.get("caliber", "")),
        str(td101.get("caliber", ""))[:160],
    )
    check(
        "R8-02 技术看板任务没有集团明细行，不加这句提示",
        not (td4.get("group_detail") or []) and "负责人一律按 group_detail" not in str(td4.get("caliber", "")),
        str(td4.get("caliber", ""))[:120],
    )

    # 回归簇一：never_reported 的两个口径必须并列返回，不能只给一个。
    never = await _call(registry, "weekly_progress_coverage", scope="never_reported")
    check(
        "L2-02/O7-04 never_reported 同时给 55 与 9 两个口径",
        never.get("total_count") == 55 and never.get("never_reported_either_table") == 9,
        f"total_count={never.get('total_count')} either={never.get('never_reported_either_table')}",
    )
    check(
        "L2-02/O7-04 has_group_history = 0 的行数正好是 9",
        sum(1 for r in never["rows"] if not r["has_group_history"]) == 9,
        str(sum(1 for r in never["rows"] if not r["has_group_history"])),
    )
    never_cal = str(never.get("caliber", ""))
    check(
        "L2-02/O7-04 caliber 并列两档且不再否掉 9 条",
        "两个口径并列" in never_cal and "共 9 条" in never_cal and "那样只得 9 条" not in never_cal,
        never_cal[:200],
    )
    check(
        "E3-02/K3-02 caliber 点明行数类问题只算 task_progress",
        "一律只算 task_progress" in never_cal,
        never_cal[-200:],
    )

    # 回归簇二之一：在途单的状态 x 类型九档（与看板 x 状态是两个维度）。
    kind = await _call(registry, "weekly_submission_query", scope="inflight_by_kind")
    by_kind = {(r["status"], r["submission_kind"]): r["submission_count"] for r in kind["rows"]}
    check(
        "I1-01 在途单按状态 + 类型分 9 档，相加等于 61",
        len(kind["rows"]) == 9 and sum(by_kind.values()) == 61,
        f"{len(kind['rows'])} 档 合计 {sum(by_kind.values())}",
    )
    check(
        "I1-01 pending_audit 拆成 initial 7 / progress 14",
        by_kind.get(("pending_audit", "initial")) == 7 and by_kind.get(("pending_audit", "progress")) == 14,
        str(sorted(by_kind.items())[:4]),
    )
    check(
        "I1-01 caliber 点明与 inflight_by_board 是两个维度",
        "不要互答" in str(kind.get("caliber", "")) and "pending_fill 只有 initial" in str(kind.get("caliber", "")),
        str(kind.get("caliber", ""))[:200],
    )

    # 回归簇二之二：按看板的驳回率，分子分母都在提交单上。
    rej = await _call(registry, "weekly_submission_query", scope="rejected_by_board")
    rej_rows = {r["board_code"]: r for r in rej["rows"]}
    check(
        "K1-04 技术组驳回率 3.07% 高于集团组 2.37%",
        rej["rows"][0]["board_code"] == "tech"
        and str(rej_rows["tech"]["rejected_pct"]) == "3.07"
        and str(rej_rows["group"]["rejected_pct"]) == "2.37",
        str([(r["board_code"], r["rejected_pct"]) for r in rej["rows"]]),
    )
    check(
        "K1-04 分母是该看板全部单（293 / 169）",
        rej_rows["tech"]["submissions"] == 293 and rej_rows["group"]["submissions"] == 169,
        str([(r["board_code"], r["submissions"], r["rejected"]) for r in rej["rows"]]),
    )
    check(
        "K1-04 caliber 否掉拿 13 条驳回动作当分子",
        "13 条" in str(rej.get("caliber", "")) and "动作条数" in str(rej.get("caliber", "")),
        str(rej.get("caliber", ""))[:200],
    )

    # payload 四档：键名可列、键值绝不外泄。断言分两组——数字对不对，以及
    # 有没有把填报正文带出来。后者是安全边界，比数字更要紧，所以正文样本直接写死。
    combos = await _call(registry, "weekly_submission_query", scope="payload_key_combos")
    combo_map = {r["payload_keys"]: r["submission_count"] for r in combos["rows"]}
    check(
        "I7-02 payload 键组合 4 种，条数 196/150/111/3",
        len(combo_map) == 4 and sorted(combo_map.values(), reverse=True) == [196, 150, 111, 3],
        str(combo_map),
    )
    leak_samples = ("（快照）本轮填报进展", "（快照）本轮进度成效", "2026年12月底")
    check(
        "payload_key_combos 不带出任何键值",
        not any(s in json.dumps(combos, ensure_ascii=False) for s in leak_samples),
        json.dumps(combos, ensure_ascii=False)[:200],
    )
    by_board = await _call(registry, "weekly_submission_query", scope="payload_keys_by_board")
    check(
        "I6-03 按看板列键组合共 5 行",
        by_board["row_count"] == 5,
        str(by_board["row_count"]),
    )
    check(
        "I6-03 两看板键不同名且不带键值",
        "progressEffect" in json.dumps(by_board, ensure_ascii=False)
        and "latestProgress" in json.dumps(by_board, ensure_ascii=False)
        and not any(s in json.dumps(by_board, ensure_ascii=False) for s in leak_samples),
        json.dumps(by_board, ensure_ascii=False)[:200],
    )
    absent = await _call(registry, "weekly_submission_query", scope="payload_absent")
    check(
        "I7-03 没有 payload 的单 = 2",
        absent["value"] == 2,
        str(absent.get("value")),
    )
    missing = await _call(registry, "weekly_submission_query", scope="payload_missing_progress_key")
    check(
        "I7-01 缺两个进展键 = 153 张（150 建单 + 3 只有 completionTime）",
        missing["row_count"] == 153,
        str(missing["row_count"]),
    )
    check(
        "I7-01 caliber 点明 153 且提醒别读成 150，不带键值",
        "153" in str(missing.get("caliber", ""))
        and "别把它当成 150" in str(missing.get("caliber", ""))
        and not any(s in json.dumps(missing, ensure_ascii=False) for s in leak_samples),
        str(missing.get("caliber", ""))[:200],
    )

    trace = await _call(registry, "weekly_workflow_query", task="数据资源登记体系建设")
    trace_rows = trace.get("rows", [])
    trace_times = [str(r.get("created_at")) for r in trace_rows]
    check(
        "I3-02 审批轨迹 13 条且按 created_at 升序",
        trace["row_count"] == 13 and trace_times == sorted(trace_times),
        str(trace["row_count"]) + " " + str(trace_times[:5]),
    )
    check(
        "I3-02 轮次号不等于时间序：第 3 轮排在第 2 轮之前",
        [r.get("round_no") for r in trace_rows] == [1, 1, 1, 1, 3, 3, 3, 2, 2, 2, 4, 4, 4],
        str([r.get("round_no") for r in trace_rows]),
    )
    check(
        "I3-02 caliber 点明按时间不按轮次",
        "created_at" in str(trace.get("caliber", "")) and "round_no" in str(trace.get("caliber", "")),
        str(trace.get("caliber", ""))[:200],
    )

    bad_group = await _call(registry, "weekly_aggregate", group_by="不支持的维度")
    check(
        "不支持的聚合维度明确报错",
        bad_group.get("ok") is False and bad_group["error"]["code"] == "unsupported_group_by",
        str(bad_group.get("error"))[:100],
    )
    bad_status = await _call(registry, "weekly_task_query", status="9")
    check(
        "非法 status 明确报错",
        bad_status.get("ok") is False,
        str(bad_status.get("error"))[:100],
    )
    empty_task = await _call(registry, "weekly_task_detail", task="  ")
    check(
        "空参数在客户端侧就被拒（不打远端）",
        empty_task.get("ok") is False and empty_task["error"]["code"] == "invalid_argument",
        str(empty_task.get("error"))[:100],
    )

    # --- R-13 multi-value owner matching -----------------------------------
    owners = await _call(registry, "weekly_aggregate", group_by="owner")
    check(
        "R-11/R-13 分管领导按填法枚举计数",
        owners.get("ok") is True and bool(owners.get("rows")),
        f"填法数={len(owners.get('rows', []))}",
    )
    named = await _call(registry, "weekly_task_query", owner="王振国", limit=5)
    check(
        "按负责人姓名可检出任务（去空格匹配）",
        named.get("ok") is True and named.get("total_count", 0) > 0,
        f"total={named.get('total_count')}",
    )

    # 时间维度：相对窗以数据快照日为锚，不是机器墙钟。两者相差十余天，
    # 用 CURDATE() 会把窗口滑出数据、算出偏小的数。
    w30 = await _call(registry, "weekly_progress_range", last_days=30)
    check(
        "最近 30 天进展期数与任务数（锚定快照日）",
        w30.get("total_count") == 17 and w30.get("total_tasks") == 17,
        f"total_count={w30.get('total_count')} total_tasks={w30.get('total_tasks')}",
    )
    check(
        "相对时间窗口径显式声明以快照日为基准",
        "2026-08-15" in str(w30.get("caliber", "")),
        str(w30.get("caliber"))[:120],
    )
    ytd = await _call(registry, "weekly_progress_range", date_from="2026-01-01", date_to="2026-08-15")
    check(
        "计数不受 200 行截断影响（今年以来 366 期）",
        ytd.get("total_count") == 366 and ytd.get("row_count") == 200 and ytd.get("has_more") is True,
        f"total={ytd.get('total_count')} rows={ytd.get('row_count')} has_more={ytd.get('has_more')}",
    )
    months = await _call(registry, "weekly_progress_range", date_from="2026-01-01", date_to="2026-08-15", by="month")
    check(
        "按月分组趋势可直答（7 个月桶）",
        months.get("ok") is True and months.get("row_count") == 7,
        f"rows={months.get('row_count')}",
    )
    check(
        "K3-01 环比 prev_count / mom_change 由服务端算好，首月为空",
        [r.get("mom_change") for r in months.get("rows", [])] == [None, 1, -2, 1, 1, -13, -31]
        and [r.get("prev_count") for r in months.get("rows", [])] == [None, 60, 61, 59, 60, 61, 48],
        str([(r.get("bucket"), r.get("prev_count"), r.get("mom_change")) for r in months.get("rows", [])]),
    )
    all_months = await _call(registry, "weekly_progress_range", by="month")
    worst = min(
        ((r["bucket"], int(r["mom_change"])) for r in all_months.get("rows", []) if r.get("mom_change") is not None),
        key=lambda t: t[1],
        default=("", 0),
    )
    check(
        "K3-03 降幅最大取 mom_change 最负的一档（2026-07 的 -31）",
        all_months.get("row_count") == 19 and worst == ("2026-07", -31),
        f"rows={all_months.get('row_count')} worst={worst}",
    )
    check(
        "环比 caliber 点明勿自行错位相减，且降幅取最负不取绝对值",
        "不要自己把行错位相减" in str(all_months.get("caliber", ""))
        and "不是绝对值最大" in str(all_months.get("caliber", "")),
        str(all_months.get("caliber", ""))[-200:],
    )
    # K3-01. 不限窗口时 2026-01 的 prev_count 会取到 2025-12 的 58，那是跨年口径。
    jan = next((r for r in all_months.get("rows", []) if r.get("bucket") == "2026-01"), {})
    check(
        "K3-01 反例 不限窗口时首月对照跨到上一年（2025-12 的 58）",
        int(jan.get("prev_count") or -1) == 58 and "跨年口径" in str(all_months.get("caliber", "")),
        str(jan),
    )
    mom3 = await _call(registry, "weekly_progress_history", task="隐私计算平台自主可控攻关", limit=3)
    mom3_rows = mom3.get("rows", [])
    full_hist = await _call(registry, "weekly_progress_history", task="隐私计算平台自主可控攻关")
    gap_summary = full_hist.get("gap_summary") or {}
    check(
        "D4-03 平均间隔由服务端 ROUND 到一位小数（30.3，17 个间隔）",
        str(gap_summary.get("avg_gap_days")) == "30.3" and int(gap_summary.get("gap_count", -1)) == 17,
        str(gap_summary),
    )
    check(
        "D4-03 caliber 点明勿自行平均（30.29 与口径 30.3 不一致）",
        "gap_summary.avg_gap_days" in str(full_hist.get("caliber", ""))
        and "30.29" in str(full_hist.get("caliber", "")),
        str(full_hist.get("caliber", ""))[-200:],
    )
    check(
        "D4-01 最近三期自带上一期对照 prev_progress 与 gap_days",
        mom3.get("row_count") == 3
        and [r.get("version_no") for r in mom3_rows] == [18, 17, 16]
        and [r.get("gap_days") for r in mom3_rows] == [30, 31, 30]
        # 第 n 行的 prev_progress 必须等于第 n+1 行的正文，错位一格就是抄串了。
        and all(mom3_rows[i].get("prev_progress") == mom3_rows[i + 1].get("latest_progress") for i in range(2)),
        str([(r.get("version_no"), r.get("gap_days")) for r in mom3_rows]),
    )
    # progress_date 与 report_time 不可互换：补报时两者相差数十天。
    late = await _call(
        registry,
        "weekly_progress_range",
        date_from="2026-07-01",
        date_to="2026-07-31",
        date_field="report_time",
    )
    check(
        "按上报时间可查出补报更早周期的进展（lag_days>0）",
        late.get("ok") is True and sum(1 for r in late.get("rows", []) if int(r.get("lag_days") or 0) > 0) == 3,
        f"rows={late.get('row_count')}",
    )
    check(
        "不支持的 date_field 明确报错，不静默改口径",
        (await _call(registry, "weekly_progress_range", date_field="created_at")).get("error", {}).get("code")
        == "unsupported_field",
        "",
    )
    life = await _call(registry, "weekly_task_lifecycle")
    check(
        "任务创建到发布的时长汇总（128 条 / 均 30.3 天 / 最长 60 天）",
        life.get("ok") is True
        # SUM() 是 Decimal，经 JSON 序列化成字符串，取数一律先转 int/str 再比。
        and int(life["rows"][0]["with_published_at"]) == 128
        and str(life["rows"][0]["avg_days_to_publish"]) == "30.3"
        and int(life["rows"][0]["max_days_to_publish"]) == 60,
        str(life.get("rows"))[:140],
    )
    fresh = await _call(registry, "weekly_freshness_distribution")
    check(
        "新鲜度分档带全局最新时间与落后天数",
        fresh.get("row_count") == 5 and fresh.get("days_behind") == 1,
        f"buckets={fresh.get('row_count')} days_behind={fresh.get('days_behind')}",
    )
    check(
        "自定义天窗可答分档表达不了的区间（7 天内 23 条）",
        (await _call(registry, "weekly_freshness_distribution", within_days=7))["rows"][0]["task_count"] == 23,
        "",
    )
    drift = await _call(registry, "weekly_freshness_distribution", drift=True, limit=8)
    check(
        "冗余列漂移可检出（latest_progress_time 与实际最新不一致）",
        drift.get("ok") is True and drift.get("row_count") == 8,
        f"rows={drift.get('row_count')}",
    )
    turn = await _call(registry, "weekly_approval_turnaround", scope="summary")
    check(
        "审批时效汇总（400 轮 / 均 14.7 天 / 最长 59 天）",
        turn["rows"][0]["completed_rounds"] == 400
        and str(turn["rows"][0]["avg_days"]) == "14.7"
        and turn["rows"][0]["max_days"] == 59,
        str(turn.get("rows"))[:140],
    )
    # 待审提交单本就尚未发布，加 R-01 闸门会把积压查成空。
    pending = await _call(registry, "weekly_approval_turnaround", scope="pending", top=8)
    check(
        "待审积压不套发布闸门，否则查成空",
        pending.get("row_count") == 8 and pending["rows"][0]["pending_days"] == 583,
        f"rows={pending.get('row_count')} 首行={_first(pending, 'pending_days')}",
    )

    # 集团组两张专表：现有工具一条都读不到，Q/R 两类 56 题全靠这四个入口。
    gd = await _call(registry, "weekly_group_detail_query", limit=8)
    check(
        "集团明细默认按最新进展时间倒序（首行 103 重点行业数据空间试点）",
        gd.get("row_count") == 8
        and _first(gd, "task_id") == 103
        and "重点行业数据空间" in str(_first(gd, "task_name")),
        f"rows={gd.get('row_count')} 首行={str(_first(gd, 'task_name'))[:30]}",
    )
    check(
        "集团明细默认口径声明完成时间不可做日期运算（R-12）",
        "R-12" in str(gd.get("caliber")),
        str(gd.get("caliber"))[:120],
    )
    due26 = await _call(registry, "weekly_group_detail_query", contains="2026", field="completion_time", limit=200)
    check(
        "完成时间按文本匹配 2026（Q4-03 = 31 条，非日期运算）",
        due26.get("row_count") == 31,
        f"rows={due26.get('row_count')}",
    )
    bad_field = await _call(registry, "weekly_group_detail_query", fields="storage_path")
    check(
        "集团明细字段走白名单，未收录字段拒绝",
        bad_field.get("ok") is False and bad_field.get("error", {}).get("code") == "unsupported_field",
        str(bad_field.get("error"))[:120],
    )

    lead = await _call(registry, "weekly_group_owner_query", person="唐立本", role="lead")
    proj = await _call(registry, "weekly_group_owner_query", person="唐立本", role="project")
    check(
        "牵头人与项目负责人是两个角色，不可混用（R1-01 = 5 / R1-02 = 3）",
        lead.get("row_count") == 5 and proj.get("row_count") == 3,
        f"lead={lead.get('row_count')} project={proj.get('row_count')}",
    )
    check(
        "多值负责人按元素精确匹配，不跨人误命中",
        "FIND_IN_SET" in str(lead.get("caliber")),
        str(lead.get("caliber"))[:140],
    )

    hist = await _call(registry, "weekly_group_history", limit=1)
    check(
        "集团历史进展双闸门后 362 行（漏 is_published 会多出 42 行草稿）",
        int(hist.get("total_count") or 0) == 362,
        f"total_count={hist.get('total_count')} total_tasks={hist.get('total_tasks')}",
    )
    check(
        "集团历史口径写明两道闸门缺一不可",
        "两道闸门" in str(hist.get("caliber")),
        str(hist.get("caliber"))[:140],
    )
    t99 = await _call(registry, "weekly_group_history", task="99")
    check(
        "单任务历史 7 期，版本号倒序（Q2-01）",
        t99.get("row_count") == 7 and _first(t99, "version_no") == 7,
        f"rows={t99.get('row_count')} 首版={_first(t99, 'version_no')}",
    )
    v3 = await _call(registry, "weekly_group_history", task="99", version_no=3)
    check(
        "可定位到指定期次（R2-03 第 3 期）",
        v3.get("row_count") == 1 and _first(v3, "version_no") == 3,
        f"rows={v3.get('row_count')} 版本={_first(v3, 'version_no')}",
    )
    by_year = await _call(registry, "weekly_group_history", by="year")
    years = {str(r.get("bucket")): r.get("progress_count") for r in by_year.get("rows") or []}
    check(
        "按年份分布 2025=137 / 2026=225（Q6-01）",
        years.get("2025") == 137 and years.get("2026") == 225,
        str(years),
    )
    latest = await _call(registry, "weekly_group_history", latest_only=True, limit=200)
    check(
        "各任务最新一期共 46 条，与看板任务数一致（R2-01）",
        latest.get("row_count") == 46,
        f"rows={latest.get('row_count')}",
    )
    bad_by = await _call(registry, "weekly_group_history", by="week")
    check(
        "集团历史不支持的分组明确报错",
        bad_by.get("ok") is False and bad_by.get("error", {}).get("code") == "unsupported_group_by",
        str(bad_by.get("error"))[:120],
    )

    owners = await _call(registry, "weekly_group_stats", scope="owners")
    orow = (owners.get("rows") or [{}])[0]
    check(
        "牵头人构成 46 / 多人 19 / 单人 27 / 去重 24 位（R1-03、R1-04）",
        int(orow.get("tasks") or 0) == 46
        and int(orow.get("multi_lead") or 0) == 19
        and int(orow.get("single_lead") or 0) == 27
        and int(owners.get("distinct_leads") or 0) == 24,
        f"{orow} distinct={owners.get('distinct_leads')}",
    )
    ct = await _call(registry, "weekly_group_stats", scope="completion_time")
    crow = (ct.get("rows") or [{}])[0]
    check(
        "完成时间格式分布 ISO 6 / 自由文本 40 / 空 0（Q4-02、Q4-04）",
        int(crow.get("iso_date") or 0) == 6
        and int(crow.get("free_text") or 0) == 40
        and int(crow.get("blank") or 0) == 0,
        str(crow),
    )
    lens = await _call(registry, "weekly_group_stats", scope="field_lengths")
    lrow = (lens.get("rows") or [{}])[0]
    check(
        "目标成果字数 均 45.6 / 最长 51（R5-03）",
        str(lrow.get("avg_chars")) == "45.6" and int(lrow.get("max_chars") or 0) == 51,
        str(lrow),
    )
    att = await _call(registry, "weekly_group_stats", scope="attachments", top=8)
    check(
        "零附件任务保留在清单里 18/46（inner_join_drops_zero）",
        int(att.get("no_attachment_summary", {}).get("no_attachment") or 0) == 18 and _first(att, "attachments") == 0,
        f"summary={att.get('no_attachment_summary')} 首行附件={_first(att, 'attachments')}",
    )
    rounds = await _call(registry, "weekly_group_stats", scope="history_rounds", top=8, min_rounds=5)
    check(
        "至少 5 期的任务 46 个，边界取等（Q6-03）",
        int(rounds.get("tasks_at_least", {}).get("tasks") or 0) == 46,
        str(rounds.get("tasks_at_least")),
    )
    bad_scope = await _call(registry, "weekly_group_stats", scope="whatever")
    check(
        "集团统计不支持的口径明确报错",
        bad_scope.get("ok") is False and bad_scope.get("error", {}).get("code") == "unsupported_scope",
        str(bad_scope.get("error"))[:120],
    )

    # 年度目标：原先只在单任务详情里露一角，全盘覆盖率类问题无路可走。
    goals = await _call(registry, "weekly_year_goal_query", limit=1)
    check(
        "年度目标全盘 313 条，计数不受 200 行截断影响（G3-02）",
        int(goals.get("total_count") or 0) == 313,
        f"total_count={goals.get('total_count')} total_tasks={goals.get('total_tasks')}",
    )
    g2026 = await _call(registry, "weekly_year_goal_query", task="隐私计算平台自主可控攻关", year=2026)
    check(
        "单任务单年度目标可定位，附里程碑摘要（G1-01、G4-03）",
        g2026.get("row_count") == 1
        and _first(g2026, "year") == 2026
        and "3项标志性成果" in str(_first(g2026, "milestone_summary")),
        f"rows={g2026.get('row_count')} 摘要={str(_first(g2026, 'milestone_summary'))[:40]}",
    )
    gby = await _call(registry, "weekly_year_goal_stats", scope="by_year")
    gyears = {str(r.get("year")): r.get("goal_count") for r in gby.get("rows") or []}
    check(
        "各年度目标条数 2025=128 / 2026=117 / 2027=68（G3-01、E7-01）",
        gyears.get("2025") == 128 and gyears.get("2026") == 117 and gyears.get("2027") == 68,
        str(gyears),
    )
    cov = await _call(registry, "weekly_year_goal_stats", scope="coverage", year=2026)
    crow = (cov.get("rows") or [{}])[0]
    check(
        "2026 覆盖率 分母 128 / 有 117 / 缺 11 / 91.4%（G2-02、G2-03）",
        int(crow.get("total_tasks") or 0) == 128
        and int(crow.get("has_goal") or 0) == 117
        and int(crow.get("missing_goal") or 0) == 11
        and str(crow.get("coverage_pct")) == "91.4",
        str(crow),
    )
    miss = await _call(registry, "weekly_year_goal_stats", scope="missing", year=2026, top=200)
    check(
        "缺 2026 目标的任务列得出来（G2-01，用 JOIN 会把它们丢掉）",
        miss.get("row_count") == 11,
        f"rows={miss.get('row_count')}",
    )
    mg = await _call(registry, "weekly_year_goal_stats", scope="missing_by_group", year=2026, top=20)
    check(
        "缺口按专项组分布 6 组，首组 2 条（G2-04）",
        mg.get("row_count") == 6 and int(_first(mg, "missing_count") or 0) == 2,
        f"rows={mg.get('row_count')} 首组={_first(mg, 'missing_count')}",
    )
    span = await _call(registry, "weekly_year_goal_stats", scope="span", min_years=3, top=8)
    check(
        "平均每任务 2.45 个年度，三年及以上边界取等（G3-03、G3-04）",
        str(span.get("avg_years_per_task")) == "2.45"
        and span.get("row_count") == 8
        and int(_first(span, "year_count") or 0) == 3,
        f"avg={span.get('avg_years_per_task')} rows={span.get('row_count')}",
    )
    both = await _call(registry, "weekly_year_goal_stats", scope="multi_year", year=2025, year_to=2026, top=5)
    check(
        "连续两年都设目标的任务 117 条（G5-01、G5-02）",
        int(both.get("tasks_in_both_years") or 0) == 117 and both.get("row_count") == 5,
        f"both={both.get('tasks_in_both_years')} rows={both.get('row_count')}",
    )
    need_year = await _call(registry, "weekly_year_goal_stats", scope="coverage")
    check(
        "覆盖率口径缺 year 时明确报错，不静默按全年算",
        need_year.get("ok") is False and need_year.get("error", {}).get("code") == "invalid_argument",
        str(need_year.get("error"))[:120],
    )

    # 里程碑：原有 weekly_milestone_query 只能列行，完成率类问题只能手数。
    ms = await _call(registry, "weekly_milestone_stats", scope="summary")
    srow = (ms.get("rows") or [{}])[0]
    check(
        "里程碑总体 474 / 完成 242 / 完成率 51.1%（H2-01、H4-04）",
        int(srow.get("total") or 0) == 474
        and int(srow.get("finished") or 0) == 242
        and str(srow.get("finish_rate_pct")) == "51.1",
        str(srow),
    )
    check(
        "里程碑口径同时声明 R-17 复核与 status 两值码",
        "R-17" in str(ms.get("caliber")) and "两值码" in str(ms.get("caliber")),
        str(ms.get("caliber"))[:140],
    )
    ms26 = await _call(registry, "weekly_milestone_stats", scope="summary", year=2026)
    m26 = (ms26.get("rows") or [{}])[0]
    check(
        "2026 里程碑 273 / 完成 142 / 未完 131（H2-03）",
        int(m26.get("total") or 0) == 273 and int(m26.get("unfinished") or 0) == 131,
        str(m26),
    )
    by_yr = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="year")
    ybk = {str(r.get("bucket")): r.get("total") for r in by_yr.get("rows") or []}
    check(
        "里程碑按年份 2025=201 / 2026=273（E7-03、H2-04）",
        ybk.get("2025") == 201 and ybk.get("2026") == 273,
        str(ybk),
    )
    by_cat = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="category", top=8)
    check(
        "里程碑按类别 6 类，首类国家任务 90 / 完成 53（H3-01、H3-02）",
        by_cat.get("row_count") == 6
        and str(_first(by_cat, "bucket")) == "国家任务"
        and int(_first(by_cat, "total") or 0) == 90
        and int(_first(by_cat, "finished") or 0) == 53,
        f"rows={by_cat.get('row_count')} 首类={_first(by_cat, 'bucket')}",
    )
    by_grp = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="group_name", top=8)
    check(
        "里程碑按承担组 6 组，带完成率（H3-03）",
        by_grp.get("row_count") == 6 and _first(by_grp, "finish_rate_pct") is not None,
        f"rows={by_grp.get('row_count')} 首组={_first(by_grp, 'bucket')}",
    )
    by_board = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="board", top=8)
    board_map = {(r.get("bucket")): str(r.get("total")) for r in by_board.get("rows") or []}
    check(
        "里程碑按看板 2 组：技术组 294、集团组 180（G-C05）",
        by_board.get("row_count") == 2
        and board_map.get("技术组重点任务进展") == "294"
        and board_map.get("集团重点任务调度") == "180",
        str(by_board.get("rows")),
    )
    floor20 = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="category", min_total=20, top=20)
    lowest = min(
        (r for r in floor20.get("rows") or []),
        key=lambda r: float(r.get("finish_rate_pct") or 0),
        default={},
    )
    check(
        "计数不少于 20 的类别里完成率最低是平台上线 45.2%（H3-04）",
        str(lowest.get("bucket")) == "平台上线" and str(lowest.get("finish_rate_pct")) == "45.2",
        str(lowest),
    )
    deleted = await _call(registry, "weekly_milestone_stats", scope="deleted")
    drow = (deleted.get("rows") or [{}])[0]
    check(
        "软删除审计 有效 566 / 已删 36 / 全表 602（H4-01、H4-02）",
        int(drow.get("active") or 0) == 566
        and int(drow.get("deleted") or 0) == 36
        and int(drow.get("total_rows") or 0) == 602,
        str(drow),
    )
    per_task = await _call(registry, "weekly_milestone_stats", scope="per_task", top=8)
    psum = per_task.get("summary") or {}
    check(
        "每任务均 3.70 个里程碑，3 个任务一个都没设（H5-02、H5-03）",
        str(psum.get("avg_per_task")) == "3.70" and int(psum.get("tasks_without_milestone") or 0) == 3,
        str(psum),
    )
    check(
        "里程碑最多的任务 6 个（H5-04）",
        int(_first(per_task, "milestones") or 0) == 6,
        f"首行={_first(per_task, 'task_name')} {_first(per_task, 'milestones')}",
    )
    # G-C03. year 此前在 per_task 里被静默丢掉（SQL 只用 clause，没用带年度的
    # active），「多少任务配了 2026 里程碑」拿到的是全年度 474 条。补的时候年度条件
    # 必须挂 LEFT JOIN 的 ON 上：进 WHERE 会把「没有 2026 里程碑」的 16 条任务整行
    # 删掉，而这正是问句要数的那部分，分母同时从 128 缩到 112，覆盖率永远算成 100%。
    pt26 = await _call(registry, "weekly_milestone_stats", scope="per_task", year=2026, top=8)
    s26 = pt26.get("summary") or {}
    check(
        "G-C03 限 2026 后分母仍是 128，112 项配了、16 项没配，覆盖率 87.5%",
        int(s26.get("tasks") or 0) == 128
        and int(s26.get("tasks_with_milestone") or 0) == 112
        and int(s26.get("tasks_without_milestone") or 0) == 16
        and str(s26.get("coverage_pct")) == "87.5",
        str(s26),
    )
    check(
        "G-C03 年度条件生效：2026 计 273 条、2025 计 201 条，合计等于全年度 474",
        int(s26.get("milestones") or 0) == 273
        and int(psum.get("milestones") or 0) == 474
        and int(
            (
                (await _call(registry, "weekly_milestone_stats", scope="per_task", year=2025, top=1)).get("summary")
                or {}
            ).get("milestones")
            or 0
        )
        == 201,
        f"2026={s26.get('milestones')} all={psum.get('milestones')}",
    )
    check(
        "G-C03 口径写明年度条件挂在 LEFT JOIN 上、未配该年度的任务保留为 0",
        "年度条件在 LEFT JOIN 上" in str(pt26.get("caliber", "")),
        str(pt26.get("caliber"))[:160],
    )
    # 并列档那句口径必须跟着数据走：全年度是 23 条并列各 6 个、首行任务 8；限 2026
    # 变成 4 条各 6 个、首行任务 52。写死就会让口径自己变成错的那一句。
    check(
        "G-C03 并列档口径随年度重算，不复用全年度的 23 条与任务 8",
        pt26.get("top_tie_count") == 4
        and per_task.get("top_tie_count") == 23
        and "任务 52" in str(pt26.get("caliber", ""))
        and "任务 8 " in str(per_task.get("caliber", "")),
        f"tie26={pt26.get('top_tie_count')} tieAll={per_task.get('top_tie_count')}",
    )
    mm1 = await _call(registry, "weekly_milestone_stats", scope="mismatch", top=20)
    check(
        "任务标完成但里程碑未全完成 6 条（H6-01）",
        mm1.get("row_count") == 6,
        f"rows={mm1.get('row_count')}",
    )
    mm2 = await _call(registry, "weekly_milestone_stats", scope="mismatch", kind="milestones_done_task_open", top=20)
    check(
        "里程碑全完成但任务仍在办 8 条（H6-02）",
        mm2.get("row_count") == 8,
        f"rows={mm2.get('row_count')}",
    )
    by_ts = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="task_status", top=8)
    ts = {str(r.get("bucket")): str(r.get("finish_rate_pct")) for r in by_ts.get("rows") or []}
    check(
        "按任务状态看里程碑完成率 已完成 93.8% 高于在办 44.6%（H6-03）",
        ts.get("2") == "93.8" and ts.get("1") == "44.6",
        str(ts),
    )
    bad_dim = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="owner")
    check(
        "里程碑不支持的维度明确报错",
        bad_dim.get("ok") is False and bad_dim.get("error", {}).get("code") == "unsupported_group_by",
        str(bad_dim.get("error"))[:120],
    )
    # G-C09/C06. year 在 mismatch 下不是筛行，是换题，而且两个 kind 反着走：
    # 存在量词（已完成但有未完成里程碑）限年度只会漏掉矛盾，6 → 3，掉的三项
    # （50/111/126）未完成里程碑在 2025，那是更硬的矛盾；全称量词（里程碑全完成但
    # 任务在办）限年度反而放宽，8 → 22。两个数都对，口径必须自带量词说明，否则
    # 「进行中但里程碑都完成的任务有多少」答 8 还是 22 没法判对错。
    mm1y = await _call(registry, "weekly_milestone_stats", scope="mismatch", year=2026, top=20)
    check(
        "G-C06 存在量词限 2026 只剩 3 条，且口径写明限年度会漏掉跨年度矛盾",
        mm1y.get("row_count") == 3
        and "存在量词" in str(mm1y.get("caliber", ""))
        and "漏掉" in str(mm1y.get("caliber", "")),
        f"rows={mm1y.get('row_count')} caliber={str(mm1y.get('caliber'))[-90:]}",
    )
    mm2y = await _call(
        registry, "weekly_milestone_stats", scope="mismatch", kind="milestones_done_task_open", year=2026, top=40
    )
    check(
        "G-C09 全称量词限 2026 反而涨到 22 条，且口径写明限年度是放宽不是收紧",
        mm2y.get("row_count") == 22
        and "全称量词" in str(mm2y.get("caliber", ""))
        and "放宽" in str(mm2y.get("caliber", "")),
        f"rows={mm2y.get('row_count')} caliber={str(mm2y.get('caliber'))[-90:]}",
    )
    check(
        "G-C06/C09 不限年度时口径明说比对 2025 与 2026 全部年度",
        "2025 与 2026" in str(mm1.get("caliber", "")) and "2025 与 2026" in str(mm2.get("caliber", "")),
        str(mm1.get("caliber"))[-90:],
    )

    # G-C07/C08. 项目组挂在任务上（11 个），里程碑行自己的 group_name 是六个短名，
    # 名字像但取值集合都不一样。拿 group_name 顶上去答「哪些项目组完成比例较高」
    # 会报成 安全组 62.8%，而正解首行是 关键技术攻关组 81.8%。
    pg = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="project_group", year=2026, top=20)
    pg_rows = pg.get("rows") or []
    pg_map = {str(r.get("bucket")): str(r.get("finish_rate_pct")) for r in pg_rows}
    check(
        "G-C07 项目组轴按完成率降序，前三为 关键技术攻关组 81.8/市场化改革组 78.9/国家工程办 72.4",
        [str(r.get("bucket")) for r in pg_rows[:3]] == ["关键技术攻关组", "市场化改革组", "国家工程办"]
        and [str(r.get("finish_rate_pct")) for r in pg_rows[:3]] == ["81.8", "78.9", "72.4"],
        str(pg_rows[:3])[:200],
    )
    check(
        "G-C08 同一次调用的末三行即需重点核实的 算力网络组 34.9/区域协同组 35.7/标准安全组 38.6",
        [str(r.get("bucket")) for r in pg_rows[-3:]] == ["标准安全组", "区域协同组", "算力网络组"]
        and pg_map.get("算力网络组") == "34.9"
        and pg_map.get("区域协同组") == "35.7"
        and pg_map.get("标准安全组") == "38.6",
        str(pg_rows[-3:])[:200],
    )
    check(
        "G-C07/C08 项目组共 11 个桶，与 group_name 的 6 个短名不是同一个轴",
        len(pg_rows) == 11
        and "不是一个轴" in str(pg.get("caliber", ""))
        and "不能当项目组绩效" in str(pg.get("caliber", "")),
        f"buckets={len(pg_rows)}",
    )
    gname = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="group_name", top=20)
    check(
        "G-C07 对照：group_name 轴只有 6 个桶且取值与项目组不重叠，误用会答错",
        len(gname.get("rows") or []) == 6
        and not ({str(r.get("bucket")) for r in gname.get("rows") or []} & set(pg_map)),
        str([r.get("bucket") for r in gname.get("rows") or []]),
    )

    # --- A5: JOIN 放大与去重 -------------------------------------------------
    # K 类。三张子表一起 JOIN 时里程碑会被附件行数乘一遍：技术组真实 294，不去重
    # 会报 1363（这也是 K6-01 标准答案本身错的地方，它对任务/目标用了 DISTINCT
    # 却对里程碑用了裸 COUNT）。两个看板相加必须等于全库里程碑总数 474。
    sc_board = await _call(registry, "weekly_scale", by="board")
    sb = {str(r.get("bucket")): r for r in sc_board.get("rows") or []}
    tech = sb.get("技术组重点任务进展") or {}
    grp = sb.get("集团重点任务调度") or {}
    check(
        "K6-01 看板规模去重后 技术组 82/77/294/402、集团 46/40/180/52",
        int(tech.get("tasks") or 0) == 82
        and int(tech.get("with_year_goal") or 0) == 77
        and int(tech.get("milestones") or 0) == 294
        and int(tech.get("attachments") or 0) == 402
        and int(grp.get("tasks") or 0) == 46
        and int(grp.get("milestones") or 0) == 180
        and int(grp.get("attachments") or 0) == 52,
        f"tech={tech} group={grp}",
    )
    check(
        "K6-01 各看板里程碑相加 474 等于全库总数（没被 JOIN 放大）",
        sum(int(r.get("milestones") or 0) for r in sc_board.get("rows") or []) == 474,
        f"sum={sum(int(r.get('milestones') or 0) for r in sc_board.get('rows') or [])}",
    )
    check(
        "K6-01 口径给出「相加等于全库总数」这条自检法",
        "COUNT(DISTINCT" in str(sc_board.get("caliber", ""))
        and "相加等于全库里程碑总数" in str(sc_board.get("caliber", "")),
        str(sc_board.get("caliber"))[:160],
    )
    # 一次 JOIN 出四个维度，才能避免「分四次查再拼」这条放大路径。
    sc_grp = await _call(registry, "weekly_scale", by="project_group")
    check(
        "K6-02 按专项组 11 行，首组 标准安全组 19/79/50",
        sc_grp.get("row_count") == 11
        and str(_first(sc_grp, "bucket")) == "标准安全组"
        and int(_first(sc_grp, "tasks") or 0) == 19
        and int(_first(sc_grp, "milestones") or 0) == 79
        and int(_first(sc_grp, "attachments") or 0) == 50,
        f"rows={sc_grp.get('row_count')} 首组={_first(sc_grp, 'bucket')}",
    )
    # totals 与 completeness 是两个问题：「多少个里程碑」问子表行数（294），
    # 「多少任务有里程碑」问任务数（80）。拿一个答另一个必错。
    sc_full = await _call(registry, "weekly_scale", by="board", mode="completeness")
    cb = {str(r.get("bucket")): r for r in sc_full.get("rows") or []}
    ctech = cb.get("技术组重点任务进展") or {}
    check(
        "K6-03 技术组完备度 82 个任务中 77 有目标 / 80 有里程碑 / 73 有进展",
        int(ctech.get("tasks") or 0) == 82
        and int(ctech.get("has_goal") or 0) == 77
        and int(ctech.get("has_milestone") or 0) == 80
        and int(ctech.get("has_progress") or 0) == 73,
        str(ctech),
    )
    check(
        "K6-03 completeness 口径点明 has_* 是任务数不是子表条数",
        "不是子表条数" in str(sc_full.get("caliber", "")),
        str(sc_full.get("caliber"))[:160],
    )
    # 集团看板一条已发布进展都没有；intensity 的分母必须留住这批零期任务，
    # 换 INNER JOIN 会让这一行整行消失，均值也就被抬高了。
    sc_int = await _call(registry, "weekly_scale", by="project_group", mode="intensity")
    check(
        "K2-04 进展密度首位 关键技术攻关组 10 任务 / 99 行 / 9.90",
        str(_first(sc_int, "bucket")) == "关键技术攻关组"
        and int(_first(sc_int, "tasks") or 0) == 10
        and int(_first(sc_int, "progress_rows") or 0) == 99
        and str(_first(sc_int, "rows_per_task")) == "9.90",
        f"首行={_first(sc_int, 'bucket')} {_first(sc_int, 'rows_per_task')}",
    )
    sc_int_b = await _call(registry, "weekly_scale", by="board", mode="intensity")
    zero_row = next(
        (r for r in sc_int_b.get("rows") or [] if str(r.get("bucket")) == "集团重点任务调度"),
        {},
    )
    check(
        "K2-04 零进展看板仍在分母里（LEFT JOIN 保留，46 任务 / 0 行）",
        int(zero_row.get("tasks") or 0) == 46 and int(zero_row.get("progress_rows", -1)) == 0,
        str(zero_row),
    )
    bad_axis = await _call(registry, "weekly_scale", by="owner")
    check(
        "weekly_scale 未知分组轴报错并列出支持值",
        bad_axis.get("ok") is False
        and bad_axis.get("error", {}).get("code") == "unsupported_by"
        and "project_group" in str(bad_axis.get("error", {}).get("message", "")),
        f"err={bad_axis.get('error')}",
    )
    bad_smode = await _call(registry, "weekly_scale", by="board", mode="coverage")
    check(
        "weekly_scale 未知口径报错并列出三档",
        bad_smode.get("ok") is False and bad_smode.get("error", {}).get("code") == "unsupported_mode",
        f"err={bad_smode.get('error')}",
    )

    # I 类。O2OA 三个外部标识填充率互不相同：process_id/work_id 各 460，
    # task_id 只有 60。拿一列代答另一列会把缺失率答反。
    ext = await _call(registry, "weekly_submission_query", scope="external_ids")
    erow = (ext.get("rows") or [{}])[0]
    check(
        "I9-01 提交单外部标识 462 总 / 460 / 460 / 60，缺 task_id 402 占 87.0%",
        int(erow.get("total") or 0) == 462
        and int(erow.get("has_process_id") or 0) == 460
        and int(erow.get("has_work_id") or 0) == 460
        and int(erow.get("has_task_id") or 0) == 60
        and int(erow.get("missing_task_id") or 0) == 402
        and str(erow.get("missing_task_id_pct")) == "87.0",
        str(erow),
    )
    check(
        "I9-01 口径点明三列填充率不同，不可互相代答",
        "不要用其中一列代答另一列" in str(ext.get("caliber", "")),
        str(ext.get("caliber"))[:160],
    )
    # 「在途」必须枚举成员：status <> 'published' 会把 cancelled 那 1 张算进来，
    # 答成 60 而不是 59（negation_includes_cancelled）。
    infl = await _call(registry, "weekly_submission_query", scope="inflight_external")
    irow = (infl.get("rows") or [{}])[0]
    check(
        "I9-02 在途且有流程号 59 张（取反会算成 60）",
        int(irow.get("inflight_with_process_id") or 0) == 59,
        str(irow),
    )
    check(
        "I9-02 口径说明在途按成员枚举、cancelled 不算在途",
        "cancelled" in str(infl.get("caliber", ""))
        and "不用 status <> 'published' 取反" in str(infl.get("caliber", "")),
        str(infl.get("caliber"))[:180],
    )
    bad_sub = await _call(registry, "weekly_submission_query", scope="payload")
    check(
        "weekly_submission_query 未知口径报错而非退回默认列表",
        bad_sub.get("ok") is False and bad_sub.get("error", {}).get("code") == "unsupported_scope",
        f"err={bad_sub.get('error')}",
    )

    # I8-01. 提交单已发布、进展行仍未发布：两套码值各判一次。期数按 version_no
    # 去重，否则「几期」会答成「几行」。
    unpub = await _call(registry, "weekly_progress_coverage", scope="unpublished_by_task", limit=10)
    check(
        "I8-01 按任务列未发布期数 共 72 个任务，首两位 4 期",
        unpub.get("total_count") == 72
        and unpub.get("row_count") == 10
        and unpub.get("has_more") is True
        and int(_first(unpub, "unpublished_rounds") or 0) == 4,
        f"total={unpub.get('total_count')} rows={unpub.get('row_count')}",
    )
    check(
        "I8-01 口径说明按 version_no 去重、是期数不是行数",
        "是「期数」不是「行数」" in str(unpub.get("caliber", "")),
        str(unpub.get("caliber"))[:180],
    )

    # F4-04. 问的是「标识」不是「任务」：同一个标识挂 3 个任务只算一个标识。
    # 不去重会返回 128 行、同一个 id 重复三次，也就数不出有几个并列最长。
    longest = await _call(registry, "weekly_person_stats", scope="id_longest", top=6)
    lrows = longest.get("rows") or []
    top_ids = [str(r.get("owner_user_id")) for r in lrows if int(r.get("id_length") or 0) == 11]
    check(
        "F4-04 最长标识去重后 4 个 11 位 NDG 账号并列，且带 task_count",
        len(top_ids) == 4 and len(set(top_ids)) == 4 and all("task_count" in r for r in lrows),
        f"ids={top_ids}",
    )
    check(
        "F4-04 口径说明一行一个去重标识、并列要一起陈述",
        "一行一个去重后的标识" in str(longest.get("caliber", "")),
        str(longest.get("caliber"))[:160],
    )

    # R7-01. changed_tasks 是批次自己声明的数字。要判断「对不上」必须反查实际
    # 落库；LEFT JOIN 不可换 INNER，第 20 批声明 43、实落 0，正是最极端那条。
    rec = await _call(registry, "weekly_import_audit", reconcile_rows=True)
    b20 = next((r for r in rec.get("rows") or [] if int(r.get("id") or 0) == 20), {})
    check(
        "R7-01 第 20 批声明 43 实落 0（INNER JOIN 会丢掉这行）",
        int(b20.get("declared_tasks") or 0) == 43
        and int(b20.get("actual_tasks", -1)) == 0
        and int(b20.get("task_diff") or 0) == -43,
        str(b20),
    )
    check(
        "Q5-02 全部 20 个批次声明与实落都不等，由服务端给出 mismatched_batches",
        rec.get("mismatched_batches") == 20 and rec.get("row_count") == 20,
        f"mismatched={rec.get('mismatched_batches')} rows={rec.get('row_count')}",
    )
    plain = await _call(registry, "weekly_import_audit")
    check(
        "不核对时口径主动声明 changed_tasks 只是声明值",
        "未与实际落库行核对" in str(plain.get("caliber", "")),
        str(plain.get("caliber"))[:180],
    )

    # Q4-02. 「某看板哪些任务没设目标」要在服务端按看板过滤，否则拿全库行自己
    # 筛会把 total_count 一起丢掉。
    gmiss = await _call(registry, "weekly_year_goal_stats", scope="missing", year=2026, board="group", top=20)
    check(
        "Q4-02 集团看板 2026 无目标 6 个任务，口径回显看板过滤",
        gmiss.get("row_count") == 6 and gmiss.get("total_count") == 6 and "仅看板" in str(gmiss.get("caliber", "")),
        f"rows={gmiss.get('row_count')} total={gmiss.get('total_count')}",
    )
    bad_board = await _call(registry, "weekly_year_goal_stats", scope="missing", year=2026, board="nope")
    check(
        "年度目标未匹配看板显式报错",
        bad_board.get("ok") is False and bad_board.get("error", {}).get("code") == "board_not_found",
        f"err={bad_board.get('error')}",
    )

    # Q2-03. 「集团看板每个任务各有几个附件」问的是整整 46 行；不带 board 时
    # 全库任务在抢名次，集团的任务一个都排不上来。total_count 与 row_count
    # 相等才说明列全了。
    q23 = await _call(registry, "weekly_rank", metric="attachments", board="group", ascending=True, top=60)
    check(
        "Q2-03 集团看板逐任务附件 46 行，total_count 与 row_count 相等",
        q23.get("row_count") == 46 and q23.get("total_count") == 46 and q23.get("has_more") is False,
        f"rows={q23.get('row_count')} total={q23.get('total_count')}",
    )
    check(
        "Q2-03 口径回显看板过滤，并说明 total_count 应与 row_count 相等",
        "仅看板" in str(q23.get("caliber", "")) and "不等即未列全" in str(q23.get("caliber", "")),
        str(q23.get("caliber"))[:200],
    )
    short = await _call(registry, "weekly_rank", metric="attachments", board="group", ascending=True, top=5)
    check(
        "Q2-03 top 给小了时 total_count 仍报 46，据此可判断没列全",
        short.get("row_count") == 5 and short.get("total_count") == 46,
        f"rows={short.get('row_count')} total={short.get('total_count')}",
    )
    bad_rboard = await _call(registry, "weekly_rank", metric="attachments", board="nope")
    check(
        "weekly_rank 未匹配看板显式报错而非静默全库排名",
        bad_rboard.get("ok") is False and bad_rboard.get("error", {}).get("code") == "board_not_found",
        f"err={bad_rboard.get('error')}",
    )

    # ---- A6：审批流转状态 / 自然月窗 / 滞报榜 / 写法归档 / 最新一期 / 孤儿引用 ----

    # B6-01. 审批流转状态是全库口径，唯一不加发布闸门的分组。加了闸门只会剩
    # published 一档 128，问题问的那 22 条全部消失。
    wf = await _call(registry, "weekly_aggregate", group_by="workflow_status")
    wf_map = {str(r.get("group_name")): int(r.get("cnt", -1)) for r in wf.get("rows") or []}
    check(
        "B6-01 审批流转状态 7 档，published 128 / pending_audit 7 / cancelled 1",
        wf_map
        == {
            "published": 128,
            "pending_audit": 7,
            "pending_leader": 5,
            "pending_fill": 3,
            "rejected": 3,
            "signing": 3,
            "cancelled": 1,
        },
        str(wf_map),
    )
    wf_totals = wf.get("totals") or {}
    check(
        "B6-02/04 未发布 22 条、已发布占比 85.3 由服务端算好",
        int(wf_totals.get("total_tasks", -1)) == 150
        and int(wf_totals.get("unpublished_tasks", -1)) == 22
        and str(wf_totals.get("published_pct")) == "85.3",
        str(wf_totals),
    )
    # G-B08/F01. 「还有多少流程要继续推动」只数四档在途 = 18，退回（已回到填报方）
    # 与已取消（不再推进）都不算。基线把退回加进去答成 21，正是因为这个加法留给了
    # 模型自己做；22 那个数也顶不上，它含退回和已取消。
    check(
        "G-B08 活跃待办 18 由服务端算好，且与未发布 22、退回 3、已取消 1 各是各的数",
        int(wf_totals.get("active_pending_tasks", -1)) == 18
        and int(wf_totals.get("rejected_tasks", -1)) == 3
        and int(wf_totals.get("cancelled_tasks", -1)) == 1
        and int(wf_totals.get("active_pending_tasks", 0))
        + int(wf_totals.get("rejected_tasks", 0))
        + int(wf_totals.get("cancelled_tasks", 0))
        == int(wf_totals.get("unpublished_tasks", -1)),
        str(wf_totals),
    )
    check(
        "G-B08 口径明说活跃待办不含退回与已取消，也不许用未发布数顶替",
        "退回 rejected 与已取消 cancelled 不计入" in str(wf.get("caliber", ""))
        and "不要用 unpublished_tasks 顶替" in str(wf.get("caliber", "")),
        str(wf.get("caliber"))[-160:],
    )
    # G-F02/F04. 分档只给条数时，「等领导审批的是哪几个任务」无路可走：正式任务
    # 清单按 R-01 一条未发布的都不返回，模型只能退回填报表，把「审批中的填报单」
    # 当任务答成 15 条和 9 条。清单随分档一起返回后，问数与问名字同一次调用解决。
    wf_list = wf.get("unpublished_task_list") or []
    by_state: dict[str, list[int]] = {}
    for row in wf_list:
        by_state.setdefault(str(row.get("workflow_status")), []).append(int(row.get("id", 0)))
    check(
        "G-F02 待领导审批 5 条按名字可取（任务 14/53/58/112/129）",
        by_state.get("pending_leader") == [14, 53, 58, 112, 129],
        str(by_state.get("pending_leader")),
    )
    check(
        "G-F04 会签 3 条按名字可取（任务 70/141/147）",
        by_state.get("signing") == [70, 141, 147],
        str(by_state.get("signing")),
    )
    check(
        "G-F02 清单是未发布全量 22 条、无一条已发布、且每条都带任务名",
        len(wf_list) == 22
        and int(wf_totals.get("unpublished_tasks", -1)) == len(wf_list)
        and all(str(r.get("workflow_status")) != "published" for r in wf_list)
        and all(str(r.get("task_name") or "").strip() for r in wf_list),
        f"{len(wf_list)} 条 / 状态 {sorted(by_state)}",
    )
    check(
        "G-F02 四档在途条数与 active_pending_tasks 对得上（7+5+3+3=18）",
        [len(by_state.get(code, [])) for code in ("pending_audit", "pending_leader", "pending_fill", "signing")]
        == [7, 5, 3, 3],
        str({k: len(v) for k, v in by_state.items()}),
    )
    check(
        "G-F02 口径指路按 workflow_status 筛清单，并挡住拿填报单当任务清单",
        "unpublished_task_list" in str(wf.get("caliber", ""))
        and "填报单与任务不是一回事" in str(wf.get("caliber", "")),
        str(wf.get("caliber"))[-220:],
    )
    # 对照组：正式任务清单一侧的闸门没动，未发布任务在那边仍然取不到。
    formal_ids = {
        int(r.get("id", 0)) for r in ((await _call(registry, "weekly_task_query", limit=200)).get("rows") or [])
    }
    check(
        "G-F02 未放宽 R-01：22 条未发布任务在正式任务清单里一条都不出现",
        formal_ids.isdisjoint({int(r.get("id", 0)) for r in wf_list}) and len(formal_ids) == 128,
        f"正式 {len(formal_ids)} 条，交集 {sorted(formal_ids & {int(r.get('id', 0)) for r in wf_list})}",
    )
    check(
        "B6 口径点明与业务状态不是一套词汇，且未发布不能按在途各档相加",
        "不是一套词汇" in str(wf.get("caliber", "")) and "cancelled" in str(wf.get("caliber", "")),
        str(wf.get("caliber"))[:200],
    )
    # 对照组：业务状态分组仍带发布闸门，两个口径不能互相顶替。
    biz = await _call(registry, "weekly_aggregate", group_by="status")
    biz_map = {str(r.get("group_name")): int(r.get("cnt", -1)) for r in biz.get("rows") or []}
    check(
        "B6 对照 业务状态仍在发布闸门内（14/78/31/5，合计 128）",
        biz_map == {"未开始": 14, "进行中": 78, "已完成": 31, "已停用": 5},
        str(biz_map),
    )
    # B3-02. 完成率由服务端 ROUND 到一位小数：31 / 128 手算是 24.21875，
    # 报成 24.22% 就与口径的 24.2% 不一致。
    biz_totals = biz.get("totals") or {}
    check(
        "B3-02 完成率由服务端算好（128 / 31 / 24.2）",
        int(biz_totals.get("total_tasks", -1)) == 128
        and int(biz_totals.get("finished_tasks", -1)) == 31
        and str(biz_totals.get("finish_rate_pct")) == "24.2",
        str(biz_totals),
    )
    check(
        "B3-02 caliber 点明勿自行相除（24.22% 是手算出来的）",
        "totals.finish_rate_pct" in str(biz.get("caliber", "")) and "24.2" in str(biz.get("caliber", "")),
        str(biz.get("caliber", ""))[-200:],
    )

    # R6-03. 自然月与 90 天是两个窗口：三个月前是 2026-05-15，90 天前是
    # 2026-05-17，中间夹着 3 行，5 月桶因此 16 vs 13。
    m3 = await _call(registry, "weekly_group_history", by="month", last_months=3)
    m3_map = {str(r.get("bucket")): int(r.get("progress_count", -1)) for r in m3.get("rows") or []}
    check(
        "R6-03 最近三个月按自然月回溯 16/30/12/45",
        m3_map == {"2026-05": 16, "2026-06": 30, "2026-07": 12, "2026-08": 45},
        str(m3_map),
    )
    d90 = await _call(registry, "weekly_group_history", by="month", last_days=90)
    check(
        "R6-03 反例 90 天窗 5 月只有 13，两个窗口不可互替",
        int({str(r.get("bucket")): r.get("progress_count") for r in d90.get("rows") or []}.get("2026-05", -1)) == 13,
        str({str(r.get("bucket")): r.get("progress_count") for r in d90.get("rows") or []}),
    )
    check(
        "R6-03 口径写明自然月回溯而非 90 天",
        "自然月回溯" in str(m3.get("caliber", "")) and "2026-05-15" in str(m3.get("caliber", "")),
        str(m3.get("caliber"))[:220],
    )
    both = await _call(registry, "weekly_group_history", by="month", last_days=90, last_months=3)
    check(
        "R6-03 两个窗口同时给显式报错，不静默合成第三个窗口",
        both.get("ok") is False and both.get("error", {}).get("code") == "invalid_argument",
        str(both.get("error")),
    )

    # R6-04. 滞报榜取 MAX(report_time)，用最早一期会把老任务全排到榜首。
    lag = await _call(registry, "weekly_group_history", by="lag", limit=5)
    check(
        "R6-04 滞报最久前 5 名 105/110/137/124/99，天数 16/14/14/13/12",
        [(int(r.get("task_id", -1)), int(r.get("lag_days", -1))) for r in lag.get("rows") or []]
        == [(105, 16), (110, 14), (137, 14), (124, 13), (99, 12)],
        str([(r.get("task_id"), r.get("lag_days")) for r in lag.get("rows") or []]),
    )
    check(
        "R6-04 上榜任务 46 个，口径声明从未报过的不在榜上",
        int(lag.get("total_tasks", -1)) == 46 and "从未报过的不在榜上" in str(lag.get("caliber", "")),
        f"total={lag.get('total_tasks')} caliber={str(lag.get('caliber'))[:120]}",
    )

    # E4-03. 「各种写法各有多少条」问的是 6 个格式档，不是 28 个去重取值；
    # 两者差一个量级，档位判别的优先级也必须固定（'2026年6月底' 归含「底」）。
    fmt = await _call(registry, "weekly_group_stats", scope="completion_time_formats")
    fmt_map = {str(r.get("fmt")): int(r.get("cnt", -1)) for r in fmt.get("rows") or []}
    check(
        "E4-03 完成时间 6 档 其他 12 / 含底 11 / 标准日期 6 / 季度 6 / 中文年月 6 / 中文年月日 5",
        fmt_map
        == {
            "其他": 12,
            "模糊表述（含“底”）": 11,
            "标准日期 YYYY-MM-DD": 6,
            "季度 YYYYQn": 6,
            "中文年月": 6,
            "中文年月日": 5,
        },
        str(fmt_map),
    )
    check(
        "E4-03 各档相加 46 等于 total_count（一条只进一档）",
        sum(fmt_map.values()) == 46 and int(fmt.get("total_count", -1)) == 46,
        f"sum={sum(fmt_map.values())} total={fmt.get('total_count')}",
    )
    # 去重取值是另一个口径，28 个，两者不能互答。
    vals = await _call(registry, "weekly_group_stats", scope="completion_time_values", top=60)
    check(
        "E4-03 对照 去重取值 28 个，与 6 档不是同一个问题",
        int(vals.get("total_count", -1)) == 28,
        f"total={vals.get('total_count')}",
    )

    # K4-04. 状态在 task 上、成效在集团明细表里，矛盾判定必须两侧同时给条件；
    # 缺 non_empty 会把「未开始且成效为空」也收进来，那并不矛盾。
    k44 = await _call(
        registry,
        "weekly_group_detail_query",
        status="0",
        non_empty="progress_effect",
        fields="progress_effect",
    )
    check(
        "K4-04 未开始却写了成效的 6 条（97/108/130/137/140/142，按最新进展时间序）",
        sorted(int(r.get("task_id", -1)) for r in k44.get("rows") or []) == [97, 108, 130, 137, 140, 142]
        and int(k44.get("total_count", -1)) == 6,
        str([r.get("task_id") for r in k44.get("rows") or []]),
    )
    check(
        "K4-04 口径区分业务状态与审批流转状态",
        "与审批流转状态" in str(k44.get("caliber", "")) and "progress_effect 非空" in str(k44.get("caliber", "")),
        str(k44.get("caliber"))[:200],
    )
    k44_open = await _call(registry, "weekly_group_detail_query", status="0", fields="progress_effect")
    check(
        "K4-04 不加 non_empty 时行数会变（矛盾判定必须带非空条件）",
        int(k44_open.get("total_count", -1)) >= 6,
        f"total={k44_open.get('total_count')}",
    )
    bad_status = await _call(registry, "weekly_group_detail_query", status="9")
    check(
        "weekly_group_detail_query 非法 status 报错而非静默忽略",
        bad_status.get("ok") is False and bad_status.get("error", {}).get("code") == "invalid_status",
        str(bad_status.get("error")),
    )
    bad_ne = await _call(registry, "weekly_group_detail_query", non_empty="nope")
    check(
        "weekly_group_detail_query 非法 non_empty 列名报错并给出值域",
        bad_ne.get("ok") is False and bad_ne.get("error", {}).get("code") == "unsupported_field",
        str(bad_ne.get("error"))[:160],
    )

    # F5-01. 牵头人/项目负责人两列都在集团明细表里，不在共享的 task 行上。
    f51 = await _call(
        registry,
        "weekly_group_detail_query",
        fields="lead_owner_names,project_owner_names,project_group",
        limit=8,
    )
    check(
        "F5-01 前 8 条带牵头人与项目负责人（首行 高志强 / 吴晓东）",
        f51.get("row_count") == 8
        and _first(f51, "lead_owner_names") == "高志强"
        and _first(f51, "project_owner_names") == "吴晓东",
        f"lead={_first(f51, 'lead_owner_names')} proj={_first(f51, 'project_owner_names')}",
    )
    check(
        "F5-01 total_count 46 说明只是截断到 8 条，不是全部",
        int(f51.get("total_count", -1)) == 46,
        f"total={f51.get('total_count')}",
    )

    # C4-01 / C4-02. 一任务一行是这个 scope 的全部意义：任务 13 有 16 期，
    # 不按 version_no 收敛就会出 16 行，且最老那期的下一步会被当成现在的安排。
    c41 = await _call(registry, "weekly_progress_coverage", scope="latest_round", project_group="算力网络组")
    check(
        "C4-01 算力网络组最新一期下一步 8 条，一任务一行",
        [int(r.get("task_id", -1)) for r in c41.get("rows") or []] == [8, 9, 13, 17, 31, 44, 45, 94]
        and int(c41.get("total_count", -1)) == 8,
        str([r.get("task_id") for r in c41.get("rows") or []]),
    )
    check(
        "C4-01 任务 13 取到 version_no 16 而非更早期号",
        {int(r.get("task_id", -1)): int(r.get("version_no", -1)) for r in c41.get("rows") or []}.get(13) == 16,
        str([(r.get("task_id"), r.get("version_no")) for r in c41.get("rows") or []]),
    )
    check(
        "C4-01 口径写明不按 progress_date 取最新",
        "不是按 progress_date 取最新" in str(c41.get("caliber", "")),
        str(c41.get("caliber"))[:200],
    )
    c42 = await _call(registry, "weekly_progress_coverage", scope="latest_round", project_group="标准安全组")
    check(
        "C4-02 标准安全组 10 条（首行任务 1，末行任务 86）",
        [int(r.get("task_id", -1)) for r in c42.get("rows") or []] == [1, 11, 12, 18, 24, 30, 36, 46, 52, 86]
        and int(c42.get("total_count", -1)) == 10,
        str([r.get("task_id") for r in c42.get("rows") or []]),
    )
    # C4-04. 0 是结论：最新一期都写了下一步。别把它当空结果去换口径重查。
    c44 = await _call(registry, "weekly_progress_coverage", scope="missing_next")
    check(
        # 用字符串比而不是 int(x or -1)：0 本身是假值，会被 or 兑成 -1 而误判。
        "C4-04 最新一期缺下一步的任务数 = 0",
        str(_first(c44, "tasks_missing_next")) == "0",
        str(c44.get("rows")),
    )
    check(
        "C4-04 口径写明中间某期空着不算",
        "中间某期空着不算" in str(c44.get("caliber", "")),
        str(c44.get("caliber"))[-80:],
    )

    # R7-04. 孤儿与「未走导入」是两回事：120 条手工填报不能算成引用不完整。
    orph = await _call(registry, "weekly_import_audit", orphans=True)
    check(
        # SUM() 回字符串、COUNT() 回整数，一律按字符串比，顺带避开 0 被 or 兑掉。
        "R7-04 孤儿进展 0 条 / 孤儿批次 0 个，未走导入的 120 条单列",
        str(_first(orph, "orphan_rows")) == "0"
        and str(_first(orph, "orphan_batch_ids")) == "0"
        and str(_first(orph, "rows_without_import")) == "120",
        str(orph.get("rows")),
    )
    check(
        "R7-04 附带 20 个批次的对账数，孤儿检查与对账同时给",
        int(orph.get("reconciliation", {}).get("batch_count") or 0) == 20,
        str(orph.get("reconciliation")),
    )
    check(
        "R7-04 口径声明 0 即引用完整、不要换口径重算",
        "不要换口径重算" in str(orph.get("caliber", "")),
        str(orph.get("caliber"))[-60:],
    )

    # A7 治理循环调用：以下三处是基线里重复调用最凶的题，缺的不是数据而是
    # 「一次调用答完」的路径。基线里 weekly_progress_history 被单题调 74 次、
    # weekly_attachment_query 51 次，6 轮题通过率只有 12.1%——慢与错同源。

    # I4-03. 提交单只加软删闸门，不加任务发布闸门。
    kind = await _call(registry, "weekly_submission_query", scope="by_kind")
    kind_map = {str(r.get("submission_kind")): int(r.get("submission_count", -1)) for r in kind.get("rows") or []}
    check(
        "I4-03 提交单类型 progress 312 / initial 150，相加 462",
        kind_map == {"progress": 312, "initial": 150} and sum(kind_map.values()) == 462,
        str(kind_map),
    )
    check(
        "I4-03 口径写明不加任务发布闸门",
        "不加任务发布闸门" in str(kind.get("caliber", "")),
        str(kind.get("caliber"))[:80],
    )

    # O3-04. NOT EXISTS 判存在性，分母另给，别拿 22 当分母。
    zero = await _call(registry, "weekly_attachment_stats", scope="zero_attachment")
    check(
        "O3-04 零附件正式任务 22 条，首行任务 9，分母 128",
        zero.get("row_count") == 22
        and int(_first(zero, "task_id") or -1) == 9
        and int(zero.get("total_formal_tasks") or -1) == 128,
        f"rows={zero.get('row_count')} 分母={zero.get('total_formal_tasks')}",
    )
    check(
        "O3-04 口径点明 NOT EXISTS 判定与分母区分",
        "NOT EXISTS" in str(zero.get("caliber", "")) and "total_formal_tasks 才是分母" in str(zero.get("caliber", "")),
        str(zero.get("caliber"))[-90:],
    )

    # R5-01. 看板在 task 上，附件表没有 board_id，按看板筛必须 JOIN 回任务。
    gatt = await _call(registry, "weekly_attachment_query", board="group", limit=10)
    check(
        "R5-01 集团组附件前 10 按 task_id 定序（97/97/97/101/102/103/104...）",
        [int(r.get("task_id", -1)) for r in gatt.get("rows") or []] == [97, 97, 97, 101, 102, 103, 104, 104, 104, 104],
        str([r.get("task_id") for r in gatt.get("rows") or []]),
    )
    check(
        "R5-01 带 board 时同时给出 task_name，否则答不了「哪些任务」",
        all(str(r.get("task_name") or "") for r in gatt.get("rows") or []),
        str(_first(gatt, "task_name")),
    )
    gall = await _call(registry, "weekly_attachment_query", board="group")
    check(
        "R5-01 集团组共 52 个有效附件，一次调用列全",
        gall.get("row_count") == 52 and gall.get("has_more") is False,
        f"rows={gall.get('row_count')} has_more={gall.get('has_more')}",
    )
    gname = await _call(registry, "weekly_attachment_query", board="集团重点任务调度", limit=3)
    check(
        "R5-01 看板 code 与看板名都能认",
        gname.get("ok") is not False and int(_first(gname, "task_id") or -1) == 97,
        str(_first(gname, "task_id")),
    )
    gbad = await _call(registry, "weekly_attachment_query", board="不存在的组")
    check(
        "R5-01 错看板名报 board_not_found 而非静默返回全库",
        gbad.get("ok") is False and gbad.get("error", {}).get("code") == "board_not_found",
        str(gbad.get("error")),
    )
    plain = await _call(registry, "weekly_attachment_query", limit=3)
    check(
        "R5-01 对照 不带 board 时仍不带 task_name（旧行为未变）",
        plain.get("row_count") == 3 and "task_name" not in (plain.get("rows") or [{}])[0],
        str(list((plain.get("rows") or [{}])[0])),
    )
    check(
        "storage_path 在按看板筛时同样不外泄",
        all("storage_path" not in r for r in gatt.get("rows") or []),
        str(list((gatt.get("rows") or [{}])[0])),
    )

    # J2-03. 金标就是取最大的那一条，largest + top=1 一次即答。
    big = await _call(registry, "weekly_attachment_stats", scope="largest", top=1)
    check(
        "J2-03 最大附件一次即答（行业数据标注基地能力建设-会议纪要.pdf / 8379724 字节）",
        big.get("row_count") == 1
        and int(_first(big, "file_size") or -1) == 8379724
        and str(_first(big, "file_name")) == "行业数据标注基地能力建设-会议纪要.pdf",
        f"{_first(big, 'file_name')} {_first(big, 'file_size')}",
    )

    # A8. 病灶是「从被截断的 200 行清单里手数」——模型自己都写过「无法精确求出
    # 全库总数」。以下各档一律服务端聚合完再回，断言盯的是数字本身与分母口径。
    ifc = await _call(registry, "weekly_submission_query", scope="inflight_count")
    check(
        "I3-01 在途提交单 61 张、分布在 55 个任务上",
        str(_first(ifc, "inflight_submissions")) == "61" and str(_first(ifc, "tasks")) == "55",
        str(ifc.get("rows")),
    )
    ifb = await _call(registry, "weekly_submission_query", scope="inflight_by_board")
    ifb_rows = {(r.get("board_code"), r.get("status")): r.get("submission_count") for r in ifb.get("rows") or []}
    check(
        "I3-02 在途按看板 + 状态两维分 9 档，rejected 同属在途（group 4 / tech 9）",
        ifb.get("row_count") == 9
        and str(ifb_rows.get(("group", "rejected"))) == "4"
        and str(ifb_rows.get(("tech", "rejected"))) == "9"
        and str(ifb_rows.get(("group", "pending_audit"))) == "14"
        and str(ifb_rows.get(("tech", "pending_leader"))) == "10",
        str(ifb.get("rows")),
    )
    check(
        "I3-02 各档相加等于在途总数 61（漏掉任一档即少算）",
        sum(int(v) for v in ifb_rows.values()) == 61,
        str(sorted(ifb_rows.items())),
    )
    ifm = await _call(registry, "weekly_submission_query", scope="inflight_multi")
    check(
        "I3-02 同时挂 2 张在途单的任务 6 个，服务端 HAVING 判定",
        ifm.get("row_count") == 6 and all(str(r.get("pending_submissions")) == "2" for r in ifm.get("rows") or []),
        str([r.get("task_id") for r in ifm.get("rows") or []]),
    )
    sgs = await _call(registry, "weekly_submission_query", scope="sign_summary")
    check(
        "I4-01 需会签 155 / 不需 307 / 合计 462",
        str(_first(sgs, "need_sign")) == "155"
        and str(_first(sgs, "no_sign")) == "307"
        and str(_first(sgs, "total")) == "462",
        str(sgs.get("rows")),
    )
    check(
        # need_sign 是标记、signing 是当前节点，两者答的不是一个问题。
        "I4-01 need_sign 不等于在途 signing 的 9 张（两套口径已在 caliber 里点明）",
        "signing" in str(sgs.get("caliber")),
        str(sgs.get("caliber")),
    )
    bys = await _call(registry, "weekly_submission_query", scope="by_signer")
    check(
        "I4-02 会签人 9 位，罗小川 29 单居首、郑亚楠 1 单垫底",
        bys.get("row_count") == 9
        and str(_first(bys, "signer_name")) == "罗小川"
        and str(_first(bys, "signed_count")) == "29"
        and all(str(r.get("signer_name") or "") for r in bys.get("rows") or []),
        str([(r.get("signer_name"), r.get("signed_count")) for r in bys.get("rows") or []]),
    )
    sgt = await _call(registry, "weekly_submission_query", scope="sign_turnaround")
    sgt_rows = {str(r.get("need_sign")): (r.get("n"), r.get("avg_days")) for r in sgt.get("rows") or []}
    check(
        "I4-03 会签耗时 128 单 14.7 天 vs 不会签 274 单 14.5 天，未完结的不进分母",
        sgt.get("row_count") == 2
        and str(sgt_rows.get("1")) == "(128, '14.7')"
        and str(sgt_rows.get("0")) == "(274, '14.5')",
        str(sgt.get("rows")),
    )
    check(
        "I4-03 两档相加 402 < 462，caliber 已写明未完结不计",
        sum(int(v[0]) for v in sgt_rows.values()) == 402 and "402" in str(sgt.get("caliber")),
        str(sgt.get("caliber")),
    )
    rpt = await _call(registry, "weekly_submission_query", scope="rounds_per_task")
    check(
        "I5-01 人均提交轮次 3.08 = 462 / 150，分子分母一并回",
        str(_first(rpt, "avg_rounds")) == "3.08"
        and str(_first(rpt, "total_submissions")) == "462"
        and str(_first(rpt, "tasks")) == "150",
        str(rpt.get("rows")),
    )
    pvp = await _call(registry, "weekly_submission_query", scope="published_vs_progress")
    check(
        "I5-02 已发布进展提交单 272 vs 已发布进展行 943（两表两闸门）",
        str(_first(pvp, "published_progress_submissions")) == "272"
        and str(_first(pvp, "published_progress_rows")) == "943",
        str(pvp.get("rows")),
    )
    ls = await _call(registry, "weekly_submission_query", scope="latest_status")
    ls_map = {r.get("status"): str(r.get("tasks")) for r in ls.get("rows") or []}
    check(
        "G-F05 最新一版提交单状态分布 112/6/5/3/2（一任务一行取 round_no 最大，各档相加 128）",
        ls_map.get("published") == "112"
        and ls_map.get("pending_leader") == "6"
        and ls_map.get("pending_audit") == "5"
        and ls_map.get("rejected") == "3"
        and ls_map.get("signing") == "2"
        and str(ls.get("row_count")) == "5",
        str(ls.get("rows")),
    )
    bna = await _call(registry, "weekly_workflow_query", scope="by_node_action")
    bna_rows = {(r.get("node_type"), r.get("action")): r.get("action_count") for r in bna.get("rows") or []}
    check(
        "I3-03 动作按 node_type + action 分 6 档，approved 在三个节点各自计数",
        bna.get("row_count") == 6
        and str(bna_rows.get(("fill", "submitted"))) == "460"
        and str(bna_rows.get(("audit", "approved"))) == "400"
        and str(bna_rows.get(("leader", "approved"))) == "400"
        and str(bna_rows.get(("sign", "approved"))) == "155"
        and str(bna_rows.get(("admin", "created"))) == "150"
        and str(bna_rows.get(("audit", "rejected"))) == "13",
        str(bna.get("rows")),
    )
    check(
        "I3-03 各档相加等于动作总数 1578",
        sum(int(v) for v in bna_rows.values()) == 1578,
        str(sorted(bna_rows.items())),
    )
    apt = await _call(registry, "weekly_workflow_query", scope="actions_per_task")
    check(
        "I3-04 人均动作 10.52 = 1578 / 150，分母是有动作的任务数而非 128",
        str(_first(apt, "avg_actions")) == "10.52"
        and str(_first(apt, "total_actions")) == "1578"
        and str(_first(apt, "tasks")) == "150",
        str(apt.get("rows")),
    )
    lnk = await _call(registry, "weekly_group_history", by="linkage")
    check(
        "I8-03 集团成效历史 404 行全部未挂提交单（linked 0），分母不是过闸的 362",
        str(_first(lnk, "total_rows")) == "404"
        and str(_first(lnk, "linked_rows")) == "0"
        and str(_first(lnk, "unlinked_rows")) == "404"
        and str(_first(lnk, "published_rows")) == "362",
        str(lnk.get("rows")),
    )
    check(
        # 0 必须被读成「确实没有挂接」，不能被读成「查不到」。
        "I8-03 caliber 明说 0 即没有挂接，不是查不到",
        "不是查不到" in str(lnk.get("caliber")),
        str(lnk.get("caliber")),
    )
    badflow = await _call(registry, "weekly_workflow_query", scope="by_action")
    check(
        "A8 动作侧错 scope 报 unsupported_scope 并列出值域，而非静默返回全量日志",
        badflow.get("ok") is False and badflow.get("error", {}).get("code") == "unsupported_scope",
        str(badflow.get("error")),
    )

    # A9 F 类：完整率百分比、按组点名、集团看板负责人两列分歧。
    pct = await _call(registry, "weekly_field_completeness", field="project_owner_id")
    check(
        # 模型在基线里答「完整率 100%」，与它自己引用的 119/128 自相矛盾。
        "F3-04 项目负责人 ID 完整率由服务端给出 128/119/93.0",
        str(_first(pct, "total")) == "128"
        and str(_first(pct, "filled")) == "119"
        and str(_first(pct, "filled_pct")) == "93.0",
        str(pct.get("rows")),
    )
    check(
        "F3-04 caliber 点明 filled_pct 已算好、不要自己重算",
        "不要自己拿 filled / total 重算" in str(pct.get("caliber")),
        str(pct.get("caliber")),
    )
    full = await _call(registry, "weekly_field_completeness", field="project_owner_name")
    check(
        # 姓名列确实是 100%，两列口径不同这件事必须两边都能验出来。
        "F3-01 姓名列 128/128/100.0，与 ID 列的 93.0 不是同一个问题",
        str(_first(full, "filled")) == "128" and str(_first(full, "filled_pct")) == "100.0",
        str(full.get("rows")),
    )

    # G-E04. 「集团组实施举措是否可信」的判据是区分度，不是填报率：裸表 55 行
    # 全部非空、填写率 100%，但只有 1 个不同值（同一句话复制下来）。此前没有
    # 任何一档报得出不同值个数，模型只能拿填写率作答，推出的结论与 gold 相反。
    measure = await _call(registry, "weekly_field_completeness", field="implementation_measure")
    check(
        "E04 裸表口径 55 行全非空，但只有 1 个不同值",
        measure.get("raw_row_count") == 55
        and measure.get("raw_filled") == 55
        and measure.get("raw_distinct_values") == 1,
        f"raw={measure.get('raw_row_count')}/{measure.get('raw_filled')}/{measure.get('raw_distinct_values')}",
    )
    check(
        "E04 过闸口径同样只有 1 个不同值，最高频值占满 46 行",
        measure.get("distinct_values") == 1 and measure.get("top_value_rows") == 46,
        f"distinct={measure.get('distinct_values')} top={measure.get('top_value_rows')}",
    )
    measure_caliber = str(measure.get("caliber"))
    check(
        "E04 caliber 点明「填写率再高也不具备区分度」，并给出规则信号定性",
        "不具备区分度" in measure_caliber
        and "应回查生成逻辑或源数据" in measure_caliber
        and "不构成对项目或人员的绩效判断" in measure_caliber,
        measure_caliber[-160:],
    )
    check(
        "E04 caliber 指路两档分母：字段质量看裸表 55，任务填报看过闸 128",
        "两档不要混着引用" in measure_caliber and "55 行" in measure_caliber and str(_first(measure, "total")) == "128",
        f"total={_first(measure, 'total')} caliber={measure_caliber[-120:]}",
    )
    # 反向：正常字段不能被误报成「无区分度」，否则信号一响就没有信息量。
    goal_measure = await _call(registry, "weekly_field_completeness", field="overall_goal")
    check(
        "E04 反向 overall_goal 区分度正常，不触发单值警报，且 task 字段无裸表档",
        int(goal_measure.get("distinct_values") or 0) > 1
        and "不具备区分度" not in str(goal_measure.get("caliber"))
        and "raw_row_count" not in goal_measure,
        f"distinct={goal_measure.get('distinct_values')}",
    )

    roster = await _call(registry, "weekly_person_stats", scope="group_roster", project_group="标准安全组")
    names = [r.get("person") for r in roster.get("rows") or []]
    check(
        "F7-03 标准安全组牵头人 9 人，行数即人数（该组 19 条任务）",
        roster.get("row_count") == 9 and len(names) == 9,
        str(names),
    )
    check(
        "F7-03 九人姓名与金标逐名一致",
        set(names)
        == {
            "吴晓东",
            "李建华",
            "周文斌",
            "孙立群",
            "张国栋",
            "王振国",
            "赵明辉",
            "陈志远",
            "project_lead_b",
        },
        str(sorted(names)),
    )
    check(
        "F7-03 caliber 点明不要拿任务条数当人数",
        "不要拿任务条数当人数" in str(roster.get("caliber")),
        str(roster.get("caliber")),
    )
    noroster = await _call(registry, "weekly_person_stats", scope="group_roster")
    check(
        # 缺组名时必须报错并指路，不能默默退化成全库点名。
        "F7-03 group_roster 缺 project_group 时报 missing_project_group 并指路",
        noroster.get("ok") is False and noroster.get("error", {}).get("code") == "missing_project_group",
        str(noroster.get("error")),
    )
    pg = await _call(registry, "weekly_task_query", project_group="标准安全组", limit=200)
    check(
        "F7-03 weekly_task_query 支持 project_group 精确筛，19 条",
        pg.get("ok") is True and str(pg.get("total_count")) == "19",
        str(pg.get("total_count")),
    )

    det = await _call(
        registry,
        "weekly_group_detail_query",
        task="数据资产入表试点推进",
        fields="project_owner_names,project_owner_ids",
    )
    check(
        # 金标是明细表的三人，模型答的「秦怀瑾」来自 task 行的单值列。
        "F5-03 集团明细表负责人为三人且人数由服务端算出",
        str(_first(det, "project_owner_names")) == "胡建国,方永康,邓少华"
        and str(_first(det, "project_owner_count")) == "3",
        str(det.get("rows")),
    )
    check(
        "F5-03 caliber 点明该列与 task 上同名单值列并非同一数据",
        "并非同一个数据" in str(det.get("caliber")),
        str(det.get("caliber")),
    )
    task_row = await _call(registry, "weekly_task_query", keyword="数据资产入表试点推进", limit=5)
    single = [r.get("project_owner_name") for r in task_row.get("rows") or [] if r.get("id") == 97]
    check(
        # 两列确实不一致这件事本身要锁住：断言只写一边，改坏另一边不会被发现。
        "F5-03 task 行上的单值列确为「秦怀瑾」，两列不一致是数据事实",
        single == ["秦怀瑾"],
        str(single),
    )

    recent_rej = await _call(registry, "weekly_workflow_query", scope="recent", action="rejected", limit=8)
    rej_rows = recent_rej.get("rows") or []
    check(
        # 金标要「最近」被驳回的 8 条；默认流水按 task_id 排，最近那条埋在中间。
        "I2-01 最近驳回首行为 111 号任务 2026-07-18 那条，按动作时间倒序",
        len(rej_rows) == 8
        and rej_rows[0].get("task_id") == 111
        and str(rej_rows[0].get("acted_at")) == "2026-07-18 17:10:00"
        and rej_rows[0].get("operator_name") == "高志强",
        str(rej_rows[:1]),
    )
    check(
        "I2-01 时间严格倒序，且带任务名与填报人两列",
        [str(r.get("acted_at")) for r in rej_rows] == sorted((str(r.get("acted_at")) for r in rej_rows), reverse=True)
        and all(r.get("task_name") and r.get("reporter_name") for r in rej_rows),
        str([(r.get("task_name"), r.get("reporter_name"), str(r.get("acted_at"))) for r in rej_rows]),
    )
    check(
        "I2-01 caliber 点明「最近」看动作时间而非任务 id 或轮次",
        "不是任务 id 也不是轮次号" in str(recent_rej.get("caliber")),
        str(recent_rej.get("caliber")),
    )
    all_rej = await _call(registry, "weekly_workflow_query", scope="recent", action="rejected", limit=200)
    check(
        # 全库驳回动作 13 条：只要 8 条是金标的取数口径，不是数据只有 8 条。
        "I2-01 驳回动作全量 13 条，前 8 条是时间序取头不是全集",
        (all_rej.get("row_count") or 0) == 13,
        str(all_rej.get("row_count")),
    )
    recent_any = await _call(registry, "weekly_workflow_query", scope="recent", limit=3)
    check(
        "I2-01 recent 不带 action 时同样倒序，首行为 2026-08-15 那条",
        str(_first(recent_any, "acted_at")) == "2026-08-15 10:05:00",
        str(recent_any.get("rows")),
    )
    bad_scope = await _call(registry, "weekly_workflow_query", scope="recently", limit=3)
    check(
        "I2-01 错口径名报错并列出 recent，不静默退成全量流水",
        bad_scope.get("ok") is False and "recent" in str(bad_scope.get("error")),
        str(bad_scope.get("error")),
    )

    never = await _call(registry, "weekly_progress_coverage", scope="never_reported", limit=200)
    never_rows = never.get("rows") or []
    check(
        # 基线答的 9 来自 freshness 的「4 从未报进展」档（按 latest_progress_time
        # 判空）；金标 55 按 NOT EXISTS 判，两者差的 46 条全在集团看板。
        "B7-03 从未报进展 55 条 = 正式任务 128 - 有进展 73",
        str(never.get("total_count")) == "55" and len(never_rows) == 55,
        f"total={never.get('total_count')} rows={len(never_rows)}",
    )
    check(
        "B7-03 其中 46 条在集团历史表报过，真正两张表都没报的是 9 条",
        sum(1 for r in never_rows if r.get("has_group_history")) == 46
        and sum(1 for r in never_rows if not r.get("has_group_history")) == 9,
        str([r.get("has_group_history") for r in never_rows]),
    )
    check(
        "B7-03 caliber 点明不能用 latest_progress_time 判空（那样只得 9）",
        "latest_progress_time" in str(never.get("caliber")) and "9" in str(never.get("caliber")),
        str(never.get("caliber")),
    )
    covered = await _call(registry, "weekly_progress_coverage", scope="summary")
    check(
        # 两档必须同一套「进展」定义，否则 55 + 73 对不上 128。
        "B7-03 与 summary 同源：tasks_covered 73 且 943 期进展",
        str(_first(covered, "tasks_covered")) == "73" and str(_first(covered, "progress_rows")) == "943",
        str(covered.get("rows")),
    )

    split = await _call(registry, "weekly_progress_coverage", scope="publish_split")
    check(
        # 基线答的 945/1068 是不加任务闸门的数，两处分别取再相加就会落到那组。
        "C6-01 已发布/未发布/合计一行给全：943 + 123 = 1066",
        str(_first(split, "published")) == "943"
        and str(_first(split, "unpublished")) == "123"
        and str(_first(split, "total")) == "1066",
        str(split.get("rows")),
    )
    check(
        "C6-01 caliber 点明去掉任务闸门得 945/1068",
        "945" in str(split.get("caliber")) and "1068" in str(split.get("caliber")),
        str(split.get("caliber")),
    )
    check(
        # 943 必须与 summary 的 progress_rows 同源，否则同一个数两处不一致。
        "C6-01 published 与 summary 的 progress_rows 一致",
        str(_first(split, "published")) == str(_first(covered, "progress_rows")),
        f"split={_first(split, 'published')} summary={_first(covered, 'progress_rows')}",
    )

    unpub = await _call(registry, "weekly_progress_coverage", scope="unpublished", limit=50)
    unpub_rows = unpub.get("rows") or []
    rejected = next((r for r in unpub_rows if r.get("status") == 2), {})
    check(
        # 「多少条、涉及多少任务」一句话两个数；只给 cnt 的话 33 无处可取。
        "C6-04 驳回 39 行涉及 33 条任务，两个数同一行",
        str(rejected.get("cnt")) == "39" and str(rejected.get("task_count")) == "33",
        str(rejected),
    )
    check(
        "C6-04 三档 cnt 相加等于 total_count 123",
        sum(int(r.get("cnt") or 0) for r in unpub_rows) == 123 and str(unpub.get("total_count")) == "123",
        str([r.get("cnt") for r in unpub_rows]),
    )
    check(
        # task_count 不可相加：22 + 47 + 33 = 102，而去重后只有 72 条任务。
        "C6-04 caliber 点明各档 task_count 不可相加（去重后 72 条）",
        "不可相加" in str(unpub.get("caliber")) and "72" in str(unpub.get("caliber")),
        str(unpub.get("caliber")),
    )

    pending = await _call(registry, "weekly_progress_coverage", scope="pending_review", limit=8)
    pending_rows = pending.get("rows") or []
    check(
        # 金标按 report_time 倒序取 8；unpublished 那档只给「待审核 58 条」一个数。
        "C6-03 待审核清单按 report_time 倒序，首行为 48 号任务 08-01 18:10",
        len(pending_rows) == 8
        and pending_rows[0].get("task_id") == 48
        and str(pending_rows[0].get("report_time")) == "2026-08-01 18:10:00"
        and str(pending.get("total_count")) == "58",
        str(pending_rows[:1]),
    )
    check(
        # 「对外还是上一期」这半句得有 public_version 才对得上。
        "C6-03 带对外可见期号：4 号任务压着 19 期、对外 18 期",
        any(
            r.get("task_id") == 4 and r.get("pending_version") == 19 and r.get("public_version") == 18
            for r in pending_rows
        ),
        str([(r.get("task_id"), r.get("pending_version"), r.get("public_version")) for r in pending_rows]),
    )
    check(
        # 48 号首期就卡在审核，对外一期都没有，不能当成「上一期」。
        "C6-03 48 号 public_version 为空（首期即卡审核，对外一期都没有）",
        pending_rows[0].get("public_version") is None and "为空" in str(pending.get("caliber")),
        str(pending_rows[0]),
    )

    flight = await _call(registry, "weekly_freshness_distribution", in_flight=True)
    flight_rows = flight.get("rows") or []
    never_bucket = next((r for r in flight_rows if "从未" in str(r.get("freshness_bucket"))), {})
    check(
        # 金标问的是在办：全量分档那档是 9，多算了已完成的任务 88。
        "C3-03 在办从未报进展 8 条，各档相加等于在办总数 92",
        str(never_bucket.get("task_count")) == "8"
        and sum(int(r.get("task_count") or 0) for r in flight_rows) == 92
        and str(flight.get("task_total")) == "92",
        str(flight_rows),
    )
    all_flight = await _call(registry, "weekly_freshness_distribution")
    all_never = next((r for r in (all_flight.get("rows") or []) if "从未" in str(r.get("freshness_bucket"))), {})
    check(
        "C3-03 不加 in_flight 仍是 9，且 caliber 指路 in_flight=true",
        str(all_never.get("task_count")) == "9"
        and "in_flight" in str(all_flight.get("caliber"))
        and str(all_flight.get("task_total")) == "128",
        f"never={all_never.get('task_count')} total={all_flight.get('task_total')}",
    )
    check(
        # 这一档的 9/8 与 never_reported 的 55 是两套判据，口径里必须互相点明。
        "C3-03 caliber 点明与 never_reported 的 55 不是同一判据",
        "never_reported" in str(flight.get("caliber")) and "55" in str(flight.get("caliber")),
        str(flight.get("caliber")),
    )

    cur_effect = await _call(
        registry,
        "weekly_group_detail_query",
        fields="target_result,progress_effect,completion_time",
        order_by="progress_time",
        limit=5,
    )
    cur_rows = cur_effect.get("rows") or []
    check(
        # 默认按 task_id 排，前 5 条是 97-101（看板最早那批），答的不是「当期」。
        "C2-04 当期进度成效前 5 条按最新进展倒序：103/128/113/149/116",
        [r.get("task_id") for r in cur_rows] == [103, 128, 113, 149, 116]
        and str(cur_rows[0].get("latest_progress_time")) == "2026-08-14 14:00:00",
        str([(r.get("task_id"), r.get("latest_progress_time")) for r in cur_rows]),
    )
    bad_order = await _call(registry, "weekly_group_detail_query", order_by="__bad__", limit=1)
    check(
        "C2-04 不支持的排序键明确报错，不静默退回 task_id 序",
        bad_order.get("ok") is False
        and (bad_order.get("error") or {}).get("code") == "unsupported_order_by"
        and "progress_time" in str((bad_order.get("error") or {}).get("message")),
        str(bad_order),
    )

    depth = await _call(registry, "weekly_progress_coverage", scope="summary")
    check(
        # 分母是报过进展的 73 条，拿正式任务 128 去除得 7.37，正是基线答错的那个数。
        "D3-04 平均期数 12.92 = 943 / 73，服务端直接给到",
        str(_first(depth, "avg_rounds_per_task")) == "12.92"
        and str(_first(depth, "progress_rows")) == "943"
        and str(_first(depth, "tasks_covered")) == "73",
        str(depth.get("rows")),
    )
    check(
        "D3-04 caliber 点明分母是 73 而非 128",
        "73" in str(depth.get("caliber")) and "7.37" in str(depth.get("caliber")),
        str(depth.get("caliber")),
    )

    by_task = await _call(registry, "weekly_group_history", by="task", limit=5)
    task_rows = by_task.get("rows") or []
    check(
        # 11 期的有 8 条并列，按任务名排会把 133 顶进前 5、把 115 挤出去。
        "D5-04 看板按任务计期前 5 条并列按 id：104/105/115/120/127",
        [r.get("task_id") for r in task_rows] == [104, 105, 115, 120, 127]
        and {str(r.get("progress_count")) for r in task_rows} == {"11"},
        str([(r.get("task_id"), r.get("bucket"), r.get("progress_count")) for r in task_rows]),
    )
    check(
        "D5-04 分组回 task_id 且 caliber 说明定序键",
        "task_id" in (by_task.get("columns") or []) and "task id" in str(by_task.get("caliber")),
        f"columns={by_task.get('columns')}",
    )

    series = await _call(registry, "weekly_progress_history", task="数据资源登记体系建设")
    check(
        # 同名系列是四条独立任务，只被告知「这里有 14 期」的调用方无从知道兄弟存在。
        "D1-02 同名系列显式回报：41（2期）/60（3期）/79（4期）",
        [s.get("id") for s in (series.get("same_name_series") or [])] == [41, 60, 79]
        and "不要合并进本任务的历史" in str(series.get("caliber")),
        str(series.get("same_name_series")),
    )
    unique_name = await _call(registry, "weekly_progress_history", task="多方安全计算性能优化")
    check(
        "D1-02 无同名系列时不挂 same_name_series 字段",
        unique_name.get("ok") is not False and "same_name_series" not in unique_name,
        str(sorted(unique_name.keys())),
    )

    slowest = await _call(registry, "weekly_approval_turnaround", scope="slowest", top=3)
    slow_rows = slowest.get("rows") or []
    check(
        # 榜首 59 天是两轮并列，只回任务名时模型手上没有定序键，两条都列就成了多余条目。
        "E8-04 审批最慢首行为任务 76、59 天，并列按 id 升序",
        [r.get("task_id") for r in slow_rows] == [76, 143, 18]
        and str(slow_rows[0].get("days")) == "59"
        and str(slowest.get("top_tie_count")) == "2",
        str([(r.get("task_id"), r.get("days")) for r in slow_rows]),
    )
    top_one = await _call(registry, "weekly_approval_turnaround", scope="slowest", top=1)
    check(
        "E8-04 取 top=1 仍报出并列数，不把并列藏起来",
        [r.get("task_id") for r in (top_one.get("rows") or [])] == [76]
        and str(top_one.get("top_tie_count")) == "2"
        and "并列" in str(top_one.get("caliber")),
        str(top_one.get("rows")),
    )

    drift = await _call(registry, "weekly_freshness_distribution", drift=True)
    check(
        # 金标 LIMIT 8 是截断，全集 73 条；口径要写清是双向漂移而不是漏报。
        "E6-04 漂移清单 73 条，按 task id 升序，首行任务 1",
        str(drift.get("row_count")) == "73"
        and (drift.get("rows") or [{}])[0].get("task_id") == 1
        and drift.get("has_more") is False,
        f"row_count={drift.get('row_count')} first={(drift.get('rows') or [{}])[0].get('task_id')}",
    )
    check(
        "E6-04 caliber 点明双向不一致且给出 73 这个规模",
        "偏早" in str(drift.get("caliber")) and "73" in str(drift.get("caliber")),
        str(drift.get("caliber")),
    )

    wiped = await _call(registry, "weekly_milestone_stats", scope="fully_deleted")
    wiped_rows = wiped.get("rows") or []
    check(
        # 各清单口径都带 m.is_deleted = 0，被删的行在别处根本不出现，缺这一档只能答「无法确认」。
        "H4-03 里程碑被全删的 3 条任务：63(5)/78(3)/110(2)",
        [(r.get("task_id"), r.get("deleted_milestones")) for r in wiped_rows] == [(63, 5), (78, 3), (110, 2)],
        str([(r.get("task_id"), r.get("deleted_milestones")) for r in wiped_rows]),
    )
    check(
        # 「删过」是 23 条、「删干净」是 3 条，口径必须把这两个数摆在一起。
        "H4-03 caliber 区分「全删 3 条」与「删过 23 条」",
        "23" in str(wiped.get("caliber")) and "NOT EXISTS" in str(wiped.get("caliber")),
        str(wiped.get("caliber")),
    )
    del_totals = await _call(registry, "weekly_milestone_stats", scope="deleted")
    check(
        "H4-03 全表软删档指路 fully_deleted，不让模型拿 566/36/602 硬答",
        "fully_deleted" in str(del_totals.get("caliber")),
        str(del_totals.get("caliber")),
    )

    per_task = await _call(registry, "weekly_milestone_stats", scope="per_task", top=3)
    check(
        # 前几行同为 6 个，模型看不出这是并列就把 23 条全铺开。
        "H5-04 里程碑最多首行任务 8、6 个，并列 23 条随返回",
        [r.get("task_id") for r in (per_task.get("rows") or [])] == [8, 24, 36]
        and str((per_task.get("rows") or [{}])[0].get("milestones")) == "6"
        and str(per_task.get("top_tie_count")) == "23",
        str([(r.get("task_id"), r.get("milestones")) for r in (per_task.get("rows") or [])]),
    )

    ms_series = await _call(registry, "weekly_milestone_query", task="数据资源登记体系建设")
    check(
        # 本体 5 条，2/3/4 期另有 5/3/2 条，合起来 15 条答的是另一个问题。
        "H1-02 单任务里程碑 5 条并回报同系列 41/60/79",
        str(ms_series.get("total_count")) == "5"
        and [s.get("id") for s in (ms_series.get("same_name_series") or [])] == [41, 60, 79]
        and "不要合并进本任务的安排" in str(ms_series.get("caliber")),
        str(ms_series.get("same_name_series")),
    )
    ms_year = await _call(registry, "weekly_milestone_query", task="全国一体化算力网调度平台建设", year="2026")
    check(
        "H1-04 该任务 2026 年 4 条，同系列 46/65/84 一并点明",
        str(ms_year.get("total_count")) == "4"
        and [s.get("id") for s in (ms_year.get("same_name_series") or [])] == [46, 65, 84],
        f"total={ms_year.get('total_count')} series={ms_year.get('same_name_series')}",
    )
    ms_unique = await _call(registry, "weekly_milestone_query", task="多方安全计算性能优化")
    check(
        "H1-02 无同名系列时里程碑清单不挂该字段",
        "same_name_series" not in ms_unique and str(ms_unique.get("total_count")) == "3",
        str(sorted(ms_unique.keys())),
    )

    # ---- 六处「跨轮稳定失败」的取数缺陷，期望值全部取自题库 gold_answer ----

    # K2-03：分组轴此前在默认档被静默丢掉，回的是全库 5 个桶而不是各组占比。
    bare_axis = await _call(registry, "weekly_freshness_distribution", by="project_group")
    check(
        "K2-03 单传 by 不再静默退化成全量分档，而是指名该带哪个天数",
        bare_axis.get("ok") is False
        and "stale_days" in str(bare_axis.get("error"))
        and "recent_days" in str(bare_axis.get("error")),
        json.dumps(bare_axis, ensure_ascii=False)[:200],
    )
    stale_axis = await _call(registry, "weekly_freshness_distribution", by="project_group", stale_days=90)
    check(
        # gold 首行国家工程办 4/15 = 26.7%；标准安全组 5 条更多但占比更低，
        # 只给 stale_count 答不了这题。
        "K2-03 各组滞后占比首行国家工程办 26.7%（条数最多的标准安全组 26.3% 在后）",
        [(r.get("bucket"), str(r.get("stale_pct"))) for r in (stale_axis.get("rows") or [])][:2]
        == [("国家工程办", "26.7"), ("标准安全组", "26.3")],
        str([(r.get("bucket"), r.get("stale_pct")) for r in (stale_axis.get("rows") or [])][:3]),
    )
    active_axis = await _call(registry, "weekly_freshness_distribution", by="board", recent_days=90)
    check(
        # gold：技术组 61/82 = 74.4% 活跃。问活跃度就得按 active_pct 定序，
        # 否则首行给的是滞后最多的那端，等于把答案报反。
        "K1-03 各看板近 90 天活跃度：技术组 61 条 / 74.4%，按活跃端定序",
        [
            (r.get("bucket"), str(r.get("active_count")), str(r.get("active_pct")))
            for r in (active_axis.get("rows") or [])
        ]
        == [("集团重点任务调度", "46", "100.0"), ("技术组重点任务进展", "61", "74.4")],
        str([(r.get("bucket"), r.get("active_count"), r.get("active_pct")) for r in (active_axis.get("rows") or [])]),
    )

    # C3-04：调用对、表格逐行也对，模型却把合计写成 19（真值 18）。跨行算术一律服务端做完。
    inflight_axis = await _call(
        registry,
        "weekly_freshness_distribution",
        by="project_group",
        stale_days=90,
        in_flight=True,
    )
    inflight_rows = inflight_axis.get("rows") or []
    check(
        "C3-04 在办口径下国家工程办 3 条（不限状态是 4 条），标准安全组 5 条",
        {r.get("bucket"): str(r.get("stale_count")) for r in inflight_rows}.get("国家工程办") == "3"
        and {r.get("bucket"): str(r.get("stale_count")) for r in inflight_rows}.get("标准安全组") == "5",
        str([(r.get("bucket"), r.get("stale_count")) for r in inflight_rows][:4]),
    )
    check(
        "C3-04 totals 给出合计，且与逐行相加一致（18），免得模型自己求和写成 19",
        str((inflight_axis.get("totals") or {}).get("stale_total")) == "18"
        and sum(int(r.get("stale_count")) for r in inflight_rows) == 18
        and str((inflight_axis.get("totals") or {}).get("task_total")) == "92",
        json.dumps(inflight_axis.get("totals"), ensure_ascii=False),
    )
    check(
        "C3-04 caliber 指明合计取 totals，不要自己把一列加一遍",
        "stale_total" in str(inflight_axis.get("caliber")),
        str(inflight_axis.get("caliber"))[-170:],
    )

    # K5-01/K5-02：一级分类此前只回 cnt，率要模型自己除，两题都算错。
    cat_rate = await _call(registry, "weekly_aggregate", group_by="primary_category", order_by="finish_rate")
    check(
        "K5-01 一级分类完成率逐档等于 gold，首行国家数据基础设施 45.5%",
        [str(r.get("finish_rate_pct")) for r in (cat_rate.get("rows") or [])]
        == ["45.5", "44.4", "30.0", "27.8", "27.3", "21.4", "21.4", "16.7", "15.4", "10.0", "8.3"],
        str([(r.get("group_name"), r.get("finish_rate_pct")) for r in (cat_rate.get("rows") or [])][:3]),
    )
    check(
        # K5-02 问「关键技术攻关和平台研发比」，gold 27.8% vs 8.3%，
        # 基线曾答成 40.0% / 37.5% 且高低颠倒。
        "K5-02 关键技术攻关 18/5/27.8 高于平台研发 12/1/8.3",
        [
            (r.get("cnt"), str(r.get("finished")), str(r.get("finish_rate_pct")))
            for r in (cat_rate.get("rows") or [])
            if r.get("group_name") in ("关键技术攻关", "平台研发")
        ]
        == [(18, "5", "27.8"), (12, "1", "8.3")],
        str([(r.get("group_name"), r.get("cnt"), r.get("finished")) for r in (cat_rate.get("rows") or [])]),
    )

    # F2-02/F4-04：并列此前由 caliber 让模型「按并列陈述」，单数问句被答成三人。
    lead_top = await _call(registry, "weekly_person_stats", scope="workload", top=1)
    check(
        "F2-02 任务量最大的牵头人单行吴晓东 14，并列个数作为 tied_at_top 回出",
        [(r.get("person"), r.get("task_count")) for r in (lead_top.get("rows") or [])] == [("吴晓东", 14)]
        and str(lead_top.get("tied_at_top")) == "3",
        f"rows={lead_top.get('rows')} tied={lead_top.get('tied_at_top')}",
    )
    lead_ten = await _call(registry, "weekly_person_stats", scope="workload", top=10)
    check(
        # F2-01 要前 10 位且并列都在内：修 F2-02 不能把这题打翻。
        "F2-01 前 10 位仍是 10 行且三个并列 14 都在",
        str(lead_ten.get("row_count")) == "10"
        and [r.get("task_count") for r in (lead_ten.get("rows") or [])][:3] == [14, 14, 14],
        str([(r.get("person"), r.get("task_count")) for r in (lead_ten.get("rows") or [])][:4]),
    )
    id_top = await _call(registry, "weekly_person_stats", scope="id_longest", top=1)
    id_first = (id_top.get("rows") or [{}])[0]
    check(
        # 库里存的是双反斜杠（NDG\\emp519），gold 写的是单反斜杠，字面数不可靠：
        # 断言只钉「首行是 emp519 那个标识、长度 11、并列 4 个」这三件事。
        "F4-04 最长标识单行 emp519（长 11），另有 3 个等长走 tied_at_top",
        str(id_top.get("row_count")) == "1"
        and "emp519" in str(id_first.get("owner_user_id"))
        and id_first.get("id_length") == 11
        and str(id_top.get("tied_at_top")) == "4",
        f"rows={id_top.get('rows')} tied={id_top.get('tied_at_top')}",
    )

    # D4-04：「补报」此前没有任何出口，模型把 6 轮工具耗尽仍答不出。
    backfill = await _call(registry, "weekly_progress_coverage", scope="backfill")
    check(
        "D4-04 补报 5 对相邻期，与 gold 的期号/上报时间逐条一致",
        [(r.get("task_name"), r.get("late_filed_version"), r.get("next_version")) for r in (backfill.get("rows") or [])]
        == [
            ("行业可信数据空间建设", 10, 11),
            ("数据资源登记体系建设", 13, 14),
            ("隐私计算平台自主可控攻关", 17, 18),
            ("全国一体化算力网调度平台建设", 10, 11),
            ("金融行业高质量数据集建设", 10, 11),
        ],
        str([(r.get("task_name"), r.get("late_filed_version")) for r in (backfill.get("rows") or [])]),
    )
    check(
        # lag_days 是同一行内「上报日 - 周期日」，跟相邻两期谁先谁后是两件事。
        "D4-04 caliber 点明与 progress_range 的 lag_days 不是一个口径",
        "lag_days" in str(backfill.get("caliber")),
        str(backfill.get("caliber"))[:160],
    )

    # Q1-01/O7-05：默认档就是最新进展时间序（当期在前）；order_by=progress_time
    # 只是把它写显式，返回同一批（103/128/113/149...）。
    group_default = await _call(registry, "weekly_group_detail_query", limit=8)
    check(
        "Q1-01 集团明细默认按最新进展时间定序，前 8 条即当期",
        [r.get("task_id") for r in (group_default.get("rows") or [])] == [103, 128, 113, 149, 116, 150, 138, 101],
        str([r.get("task_id") for r in (group_default.get("rows") or [])]),
    )
    group_recent = await _call(registry, "weekly_group_detail_query", order_by="progress_time", limit=8)
    check(
        "Q1-01 order_by=progress_time 与默认档同一批任务（口径写显式，不是另一问）",
        [r.get("task_id") for r in (group_recent.get("rows") or [])][:3] == [103, 128, 113],
        str([r.get("task_id") for r in (group_recent.get("rows") or [])][:5]),
    )

    # R2-01：「最新一期成效」在历史表，不是明细表那个去规范化的当前值。
    group_latest = await _call(registry, "weekly_group_history", latest_only=True, limit=8)
    check(
        "R2-01 集团最新一期成效走 group_history latest_only，前 8 条为 97-104",
        [r.get("task_id") for r in (group_latest.get("rows") or [])] == [97, 98, 99, 100, 101, 102, 103, 104],
        str([r.get("task_id") for r in (group_latest.get("rows") or [])]),
    )

    # ---- 全量两轮后新暴露的四处，期望值同样取自 gold_answer ----

    # L5-01/L5-02：占比与累计占比服务端此前根本没有，模型自己算且取一位小数。
    shares = await _call(registry, "weekly_aggregate", group_by="project_group")
    check(
        "L5-01 各组占比与累计占比逐行等于 gold（两位小数）",
        [(str(r.get("cnt")), str(r.get("share_pct")), str(r.get("cum_pct"))) for r in (shares.get("rows") or [])][:7]
        == [
            ("19", "14.84", "14.84"),
            ("15", "11.72", "26.56"),
            ("15", "11.72", "38.28"),
            ("14", "10.94", "49.22"),
            ("12", "9.38", "58.59"),
            ("11", "8.59", "67.19"),
            ("10", "7.81", "75.00"),
        ],
        str([(r.get("group_name"), r.get("share_pct"), r.get("cum_pct")) for r in (shares.get("rows") or [])][:5]),
    )
    check(
        # 49.22 舍成 49.2 不影响「是否过半」，但 58.59 舍成 58.6 再跟阈值比就串档，
        # L5-02 正是因此把「否」答成了「是」。两位小数是这一族的下限。
        "L5-02 前 4 组累计 49.22% 未过半，第 5 组才 58.59%，caliber 写明按 cum_pct 答",
        str((shares.get("rows") or [{}])[3].get("cum_pct")) == "49.22"
        and str((shares.get("rows") or [{}, {}, {}, {}, {}])[4].get("cum_pct")) == "58.59"
        and "未过半" in str(shares.get("caliber")),
        str(shares.get("caliber"))[-190:],
    )
    by_rate = await _call(registry, "weekly_aggregate", group_by="project_group", order_by="finish_rate")
    check(
        # 累计是沿「任务数倒序」累加的，换成按完成率排之后它不再单调，留着就是假信号。
        "L5-01 按完成率定序时不返回 cum_pct（换序后累计不再单调）",
        "cum_pct" not in (by_rate.get("columns") or []),
        str(by_rate.get("columns")),
    )

    # E6-01：weekly_freshness 此前不回落后天数，模型只能答出时间点。
    fresh = await _call(registry, "weekly_freshness")
    check(
        "E6-01 freshness 随 overall 给出 newest 2026-08-14 14:00:00 与 days_behind 1",
        str((fresh.get("overall") or {}).get("newest")) == "2026-08-14 14:00:00"
        and str((fresh.get("overall") or {}).get("days_behind")) == "1"
        and str(fresh.get("as_of")) == "2026-08-15",
        json.dumps(fresh.get("overall"), ensure_ascii=False),
    )
    check(
        "E6-01 各看板行也各带自己的 days_behind",
        all("days_behind" in r for r in (fresh.get("rows") or [])),
        str([(r.get("board_name"), r.get("days_behind")) for r in (fresh.get("rows") or [])]),
    )

    # Q2-03：清单档早有「把 top 提到 46」的提示，但模型选了分布档就看不到，故反向指路。
    dist = await _call(registry, "weekly_group_stats", scope="attachment_distribution")
    check(
        "Q2-03 分布档反向指路到 scope=attachments 且点明要把 top 提到 46",
        "scope=attachments" in str(dist.get("caliber")) and "top 提到 46" in str(dist.get("caliber")),
        str(dist.get("caliber"))[-170:],
    )
    per_task = await _call(registry, "weekly_group_stats", scope="attachments", top=46)
    check(
        "Q2-03 清单档 top=46 给全 46 行，零附件任务在最前（gold 首三条 98/99/100）",
        str(per_task.get("row_count")) == "46"
        and [r.get("task_id") for r in (per_task.get("rows") or [])][:3] == [98, 99, 100]
        and str((per_task.get("rows") or [{}])[0].get("attachments")) == "0",
        str([(r.get("task_id"), r.get("attachments")) for r in (per_task.get("rows") or [])][:4]),
    )

    # R8-02：task 行的单值负责人列与集团明细的多值列在 46 条上全部不一致。
    group_hit = await _call(registry, "weekly_task_query", board="group", keyword="一体化算力网体系建设")
    check(
        "R8-02 命中集团看板任务时挂出「别用单值负责人列」的提示",
        "group_board_owner_note" in group_hit and "lead_owner_names" in str(group_hit.get("group_board_owner_note")),
        str(group_hit.get("group_board_owner_note"))[:150],
    )
    tech_hit = await _call(registry, "weekly_task_query", board="tech", limit=3)
    check(
        "R8-02 技术看板不挂该提示（免得给无关问答添噪声）",
        "group_board_owner_note" not in tech_hit,
        str(sorted(tech_hit.keys())),
    )

    # ---- oa_biz 题库暴露的缺口：主责人一列此前没有计数出口 ----
    # 三个角色是三个不同人群：技术组 owner_user_id 45 人、project_owner_name 45 人，
    # 而 lead_owner_name 只有 12 人。oa_biz 的「负责人」指 owner_user_id，
    # 此前只能落到 lead_owner 那一档，把 45 答成 12（六道题同一个根因）。
    owner_sum = await _call(registry, "weekly_person_stats", scope="workload_summary", role="owner", board="tech")
    owner_first = (owner_sum.get("rows") or [{}])[0]
    check(
        "OA-B7-03 技术组主责人 45 人（不是分管领导那 12 人）",
        str(owner_first.get("people")) == "45" and str(owner_first.get("tasks")) == "82",
        json.dumps(owner_first, ensure_ascii=False),
    )
    check(
        # gold 1.82 = 82/45。此前落到 lead_owner 档得 6.83，率本身没错、分母错了。
        "OA-B6-02 技术组平均每主责人 1.82 个任务，服务端算完",
        str(owner_first.get("avg_tasks_per_person")) == "1.82",
        json.dumps(owner_first, ensure_ascii=False),
    )
    owner_list = await _call(registry, "weekly_person_stats", scope="workload", role="owner", board="tech")
    check(
        "OA-V1-01 技术组每主责人各几个任务：45 行，一人一行",
        str(owner_list.get("row_count")) == "45",
        f"row_count={owner_list.get('row_count')} has_more={owner_list.get('has_more')}",
    )
    owner_top = await _call(registry, "weekly_person_stats", scope="workload", role="owner", board="tech", top=1)
    check(
        "OA-V1-04 「谁任务最多」单数问句取首行，并列个数走 tied_at_top",
        str(owner_top.get("row_count")) == "1" and owner_top.get("tied_at_top") is not None,
        f"rows={owner_top.get('rows')} tied={owner_top.get('tied_at_top')}",
    )
    check(
        "OA-B7-03 三个角色的人群规模写进 caliber，避免再串列",
        "主责人" in str(owner_sum.get("caliber")) and "tech" in str(owner_sum.get("caliber")),
        str(owner_sum.get("caliber"))[:160],
    )
    lead_all = await _call(registry, "weekly_person_stats", scope="workload_summary", role="lead_owner")
    check(
        # 回归：新增 owner 档与 board 参数不能动到原有两档的全库口径。
        "回归 lead_owner 全库仍是 128 任务 / 16 人 / 8.00",
        str((lead_all.get("rows") or [{}])[0].get("people")) == "16"
        and str((lead_all.get("rows") or [{}])[0].get("avg_tasks_per_person")) == "8.00",
        json.dumps((lead_all.get("rows") or [{}])[0], ensure_ascii=False),
    )
    bad_board = await _call(registry, "weekly_person_stats", scope="workload", role="owner", board="nope")
    check(
        "board 传错时明确报 board_not_found，不静默退回全库",
        bad_board.get("ok") is False and "board_not_found" in str(bad_board.get("error")),
        json.dumps(bad_board.get("error"), ensure_ascii=False)[:120],
    )

    # 「谁任务最多」的并列由服务端裁决：同一问法在 oa_biz 里有两套 gold，
    # 全库那两题用 HAVING = MAX 保并列（2 行），技术组那题用 LIMIT 1 硬切（1 行）。
    # 模型在明细上判不出该用哪套，所以分成两档、各自把口径写进 caliber。
    top_all = await _call(registry, "weekly_person_stats", scope="workload_top", role="owner")
    check(
        "OA-F1-01/L2-02 全库任务最多保并列：10515 与 u3208 各 7 条，2 行",
        [(r.get("person"), r.get("task_count")) for r in (top_all.get("rows") or [])] == [("10515", 7), ("u3208", 7)]
        and str(top_all.get("tied_at_top")) == "2",
        str([(r.get("person"), r.get("task_count")) for r in (top_all.get("rows") or [])]),
    )
    top_tech = await _call(registry, "weekly_person_stats", scope="workload_top", role="owner", board="tech")
    check(
        "技术组 keep_ties 档四人并列各 4 条（10445/10515/u3208/u3214）",
        [r.get("person") for r in (top_tech.get("rows") or [])] == ["10445", "10515", "u3208", "u3214"],
        str([(r.get("person"), r.get("task_count")) for r in (top_tech.get("rows") or [])]),
    )
    cut_tech = await _call(registry, "weekly_person_stats", scope="workload", role="owner", board="tech", top=1)
    check(
        # OA-V1-04 的 gold 是 LIMIT 1，硬切档必须仍只回一行，且用 tied_at_top 说明并列。
        "OA-V1-04 硬切档 top=1 仍是单行 10445，并列数走 tied_at_top=4",
        [r.get("person") for r in (cut_tech.get("rows") or [])] == ["10445"]
        and str(cut_tech.get("tied_at_top")) == "4",
        f"rows={cut_tech.get('rows')} tied={cut_tech.get('tied_at_top')}",
    )
    check(
        "两档在 caliber 里互相指路，免得模型选错并列规则",
        "top=1" in str(top_tech.get("caliber")) and "并列" in str(top_tech.get("caliber")),
        str(top_tech.get("caliber"))[:170],
    )
    # G-D01. owner 档分组列是工号，只回 person 时模型只能照抄「10515」，数字全对
    # 也判错（判定器：未提供姓名，无法确认）。person_name 与工号 1:1，故随行给出。
    check(
        "G-D01 owner 档带姓名：10515 是姚立诚、u3208 是余承志",
        [(r.get("person"), r.get("person_name")) for r in (top_all.get("rows") or [])]
        == [("10515", "姚立诚"), ("u3208", "余承志")],
        str([(r.get("person"), r.get("person_name")) for r in (top_all.get("rows") or [])]),
    )
    check(
        "G-D01 口径点明答「是谁」要报姓名而不是工号",
        "person_name" in str(top_all.get("caliber", "")) and "不要报工号" in str(top_all.get("caliber", "")),
        str(top_all.get("caliber"))[:200],
    )
    # 硬切档与非并列档同样要带姓名，否则同一问法换个档又退回工号。
    check(
        "G-D01 硬切档也带姓名（10445 是阎立新）",
        [(r.get("person"), r.get("person_name")) for r in (cut_tech.get("rows") or [])] == [("10445", "阎立新")],
        str(cut_tech.get("rows")),
    )
    # 姓名档本身就是姓名，不该多出一个 person_name 列来（否则等于同一个值给两遍）。
    lead_top = await _call(registry, "weekly_person_stats", scope="workload_top", role="lead_owner")
    check(
        "G-D01 姓名档不多挂 person_name，也不在口径里提工号",
        all("person_name" not in r for r in (lead_top.get("rows") or []))
        and "不要报工号" not in str(lead_top.get("caliber", "")),
        str((lead_top.get("rows") or [])[:2]),
    )
    # 映射是「取 MAX 也只有一个值」的前提：47 个工号全部 1:1 对应姓名。这条一旦
    # 不成立，person_name 就静默变成任取一个，故把前提本身也钉住。
    owner_all = await _call(registry, "weekly_person_stats", scope="workload", role="owner")
    owner_rows = owner_all.get("rows") or []
    pairs = {(str(r.get("person")), str(r.get("person_name"))) for r in owner_rows}
    check(
        "G-D01 工号与姓名 1:1（47 个工号、47 个姓名、无一为空）",
        len(owner_rows) == 47
        and len({p for p, _ in pairs}) == 47
        and len({n for _, n in pairs}) == 47
        and all(n.strip() and n != "None" for _, n in pairs),
        f"{len(owner_rows)} 行 / {len({p for p, _ in pairs})} 工号 / {len({n for _, n in pairs})} 姓名",
    )

    # ---- 审批表「裸表口径」四档 + 提交单两档 ----
    # 这批 oa_biz 题问的是审批表本身有多大（走过哪些环节、谁经办得最多、留了几条
    # 意见），跟任务还在不在办无关——与里程碑 deleted 那一档同一个道理。工具此前
    # 只有带闸门的档，差额稳定：动作 1613 vs 1578，提交单 470 vs 462。
    by_node = await _call(registry, "weekly_workflow_query", scope="by_node")
    check(
        "OA-I1-02/U12-02 审批环节逐档等于 gold（fill 468 起，五档）",
        [(r.get("node_type"), r.get("cnt")) for r in (by_node.get("rows") or [])]
        == [("fill", 468), ("audit", 422), ("leader", 409), ("admin", 158), ("sign", 156)],
        str([(r.get("node_type"), r.get("cnt")) for r in (by_node.get("rows") or [])]),
    )
    check(
        # 三档口径一起回出，口径才可核对而不是靠猜。
        "审批动作三档口径随 caliber_tiers 返回：裸表 1613 / 软删 1578 / 双闸门 1519",
        by_node.get("caliber_tiers") == {"raw_table": 1613, "soft_deleted_gate": 1578, "formal_task_gate": 1519},
        json.dumps(by_node.get("caliber_tiers"), ensure_ascii=False),
    )
    by_op = await _call(registry, "weekly_workflow_query", scope="by_operator")
    check(
        "OA-I2-02/U12-05 经办人首行孙立群 244，共 57 人（此前无出口）",
        [(r.get("operator_name"), r.get("cnt")) for r in (by_op.get("rows") or [])][:2]
        == [("孙立群", 244), ("吴晓东", 93)]
        and str(by_op.get("total_count")) == "57",
        f"first={(by_op.get('rows') or [{}])[0]} total={by_op.get('total_count')}",
    )
    span = await _call(registry, "weekly_workflow_query", scope="log_span")
    span_first = (span.get("rows") or [{}])[0]
    check(
        "OA-I1-03 日志跨度 2025-01-07 ~ 2026-08-15，共 1613 条",
        str(span_first.get("actions")) == "1613"
        and str(span_first.get("first_at")) == "2025-01-07 11:00:00"
        and str(span_first.get("last_at")) == "2026-08-15 10:05:00",
        json.dumps(span_first, ensure_ascii=False),
    )
    op_cnt = await _call(registry, "weekly_workflow_query", scope="opinion_count")
    check(
        # 只数存在性、不回正文，所以不受 R-04/R-14 遮蔽影响。
        "OA-M4-02 有意见的动作 1455 条，且只计数不外泄正文",
        str((op_cnt.get("rows") or [{}])[0].get("cnt")) == "1455"
        and "opinion" not in str((op_cnt.get("rows") or [{}])[0].keys()),
        json.dumps((op_cnt.get("rows") or [{}])[0], ensure_ascii=False),
    )
    sub_total = await _call(registry, "weekly_submission_query", scope="table_total")
    check(
        "OA-U12-01 提交单表 470 张（加软删是 462，加双闸门 438）",
        str((sub_total.get("rows") or [{}])[0].get("cnt")) == "470"
        and sub_total.get("caliber_tiers") == {"raw_table": 470, "soft_deleted_gate": 462, "formal_task_gate": 438},
        json.dumps(sub_total.get("caliber_tiers"), ensure_ascii=False),
    )
    sub_status = await _call(registry, "weekly_submission_query", scope="by_status")
    check(
        "OA-I4-01/U12-04 提交单状态七档等于 gold（published 408）",
        [(r.get("status"), r.get("cnt")) for r in (sub_status.get("rows") or [])]
        == [
            ("cancelled", 1),
            ("pending_audit", 21),
            ("pending_fill", 3),
            ("pending_leader", 15),
            ("published", 408),
            ("rejected", 13),
            ("signing", 9),
        ],
        str([(r.get("status"), r.get("cnt")) for r in (sub_status.get("rows") or [])]),
    )
    check(
        "状态档点明 approved 不在值域内（用它过滤筛不掉任何行）",
        "approved" in str(sub_status.get("caliber")),
        str(sub_status.get("caliber"))[:150],
    )
    # 回归：新增裸表档不能动到原有带闸门的两档。
    by_na = await _call(registry, "weekly_workflow_query", scope="by_node_action")
    check(
        "回归 by_node_action 仍是软删档，各档相加 1578",
        sum(int(r.get("action_count")) for r in (by_na.get("rows") or [])) == 1578,
        f"sum={sum(int(r.get('action_count')) for r in (by_na.get('rows') or []))}",
    )
    by_kind = await _call(registry, "weekly_submission_query", scope="by_kind")
    check(
        "回归 by_kind 仍是软删档，两档相加 462",
        sum(int(r.get("submission_count")) for r in (by_kind.get("rows") or [])) == 462,
        str([(r.get("submission_kind"), r.get("submission_count")) for r in (by_kind.get("rows") or [])]),
    )

    # ---- 里程碑填报人维度 + 最新版未发布口径 ----
    # OA-H5-03「里程碑都是谁报的」此前没有这一维，模型只能答不可答。
    ms_reporter = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="reporter_id", top=200)
    check(
        "OA-H5-03 里程碑按填报人分 47 行，首行 u3208 报 31 条",
        str(ms_reporter.get("row_count")) == "47"
        and [(r.get("bucket"), r.get("total")) for r in (ms_reporter.get("rows") or [])][:2]
        == [("u3208", 31), ("10515", 25)],
        str([(r.get("bucket"), r.get("total")) for r in (ms_reporter.get("rows") or [])][:3]),
    )
    ms_cat = await _call(registry, "weekly_milestone_stats", scope="by_dimension", by="category")
    check(
        "回归里程碑 category 轴仍是 6 档（新增维度不影响原有轴）",
        str(ms_cat.get("row_count")) == "6",
        f"row_count={ms_cat.get('row_count')}",
    )

    # OA-C6-01「最新写的那版进展还没对外发布」= 每任务最大 version_no 那版未发布。
    # 三档口径挨得很近，差额都是真实的语义差别，不能互相代答。
    latest_unpub = await _call(registry, "weekly_progress_coverage", scope="latest_unpublished", limit=200)
    check(
        "OA-C6-01 最新版未发布 72 条（含草稿与驳回，都没对外）",
        str(latest_unpub.get("total_count")) == "72" and str(latest_unpub.get("row_count")) == "72",
        f"total={latest_unpub.get('total_count')} rows={latest_unpub.get('row_count')}",
    )
    check(
        "OA-C6-01 每行带 progress_status，说明卡在哪一档",
        all("progress_status" in r for r in (latest_unpub.get("rows") or [])),
        str([(r.get("latest_version"), r.get("progress_status")) for r in (latest_unpub.get("rows") or [])][:3]),
    )
    check(
        "OA-C6-01 caliber 把 58 / 72 / 123 三档差别写明，防止互相代答",
        "58" in str(latest_unpub.get("caliber")) and "123" in str(latest_unpub.get("caliber")),
        str(latest_unpub.get("caliber"))[:180],
    )
    pending = await _call(registry, "weekly_progress_coverage", scope="pending_review", limit=200)
    check(
        "回归 pending_review 仍是 58 条（只取待审核那一档）",
        str(pending.get("total_count")) == "58",
        f"total={pending.get('total_count')}",
    )

    # ---- 工作流诊断出的四处：列追加 / 团队人数 / 同文本 / 明细裸表 ----

    # OA-C1-02：填报人与上报时间此前不同源，得两次调用再自己配对。
    hist_cols = await _call(registry, "weekly_progress_history", task="8")
    check(
        "OA-C1-02 进展历史同排返回 reporter_id，人和时间一次读全",
        "reporter_id" in (hist_cols.get("columns") or []) and str(hist_cols.get("row_count")) == "11",
        str(hist_cols.get("columns")),
    )
    latest_round = await _call(registry, "weekly_progress_coverage", scope="latest_round", limit=200)
    check(
        # 纯加列：行数必须仍是 73，否则就不是加列而是改了口径。
        "latest_round 加 latest_progress 列后仍是 73 行（纯加列，不动行集）",
        "latest_progress" in (latest_round.get("columns") or []) and str(latest_round.get("row_count")) == "73",
        str(latest_round.get("columns")),
    )

    # 规则信号题的文本检查出口（G-E01/E02/D05）：正则与判定固化在服务端，
    # 数字与扫描口径（每任务最新一期已发布进展，含集团看板任务）必须钉死，
    # 模型不能自己翻正文数。
    conflict = await _call(registry, "weekly_progress_coverage", scope="text_check", rule="number_conflict")
    check(
        "text_check number_conflict 15 项硬冲突、10 项阶段和超 100",
        conflict.get("ok") is True
        and str(conflict.get("hard_conflict_count")) == "15"
        and str(conflict.get("sum_anomaly_count")) == "10",
        f"hard={conflict.get('hard_conflict_count')} sum={conflict.get('sum_anomaly_count')}",
    )
    avail = await _call(registry, "weekly_progress_coverage", scope="text_check", rule="availability")
    check(
        "text_check availability 低于 90% 共 19 项",
        avail.get("ok") is True and str(avail.get("row_count")) == "19",
        f"rows={avail.get('row_count')}",
    )
    kw = await _call(registry, "weekly_progress_coverage", scope="text_check", rule="keyword")
    check(
        "text_check keyword 默认检索协调/协同等 19 条",
        kw.get("ok") is True and str(kw.get("row_count")) == "19",
        f"rows={kw.get('row_count')}",
    )
    kw2 = await _call(registry, "weekly_progress_coverage", scope="text_check", rule="keyword", keyword="跨域互认")
    check(
        "text_check keyword 自定义词生效（跨域互认 10 条）",
        kw2.get("ok") is True and str(kw2.get("row_count")) == "10",
        f"rows={kw2.get('row_count')}",
    )
    bad = await _call(registry, "weekly_progress_coverage", scope="text_check", rule="bogus")
    check(
        "text_check 不支持的规则报 unsupported_rule",
        bad.get("ok") is False and bad.get("error", {}).get("code") == "unsupported_rule",
        str(bad.get("error")),
    )
    allv = await _call(
        registry, "weekly_progress_coverage", scope="text_check", rule="number_conflict", all_versions=True
    )
    check(
        "text_check all_versions 扫全部版本（任务 103 的 V8 冲突出现，> 最新期 15 条）",
        allv.get("ok") is True and int(allv.get("row_count", 0)) > 15,
        f"rows={allv.get('row_count')}",
    )
    t103 = await _call(
        registry, "weekly_progress_coverage", scope="text_check", rule="number_conflict", task="103", all_versions=True
    )
    t103_hits = {(r.get("version_no")) for r in t103.get("rows") or []}
    check(
        "text_check task=103 all_versions 命中 V8（G-P04）",
        t103.get("ok") is True and 8 in t103_hits,
        f"versions={sorted(t103_hits)}",
    )
    orphan = await _call(registry, "weekly_progress_coverage", scope="orphan_records")
    check(
        "孤儿记录 2 条无任务进展 + 2 条无提交单动作（G-E03）",
        str(_first(orphan, "orphan_progress_rows")) == "2" and str(_first(orphan, "orphan_actions")) == "2",
        str(orphan.get("rows")),
    )
    fc = await _call(registry, "weekly_progress_coverage", scope="formal_coverage")
    check(
        "formal_coverage 128 正式任务 / 119 有正式进展 / 93.0%（G-B01）",
        str(_first(fc, "formal_task_count")) == "128"
        and str(_first(fc, "tasks_with_progress")) == "119"
        and str(_first(fc, "coverage_pct")) == "93.0",
        str(fc.get("rows")),
    )
    series = await _call(registry, "weekly_aggregate", group_by="name_series")
    check(
        "name_series 33 个多期家族、97 条任务、总家族 64（G-D04）",
        str(series.get("multi_member_families")) == "33"
        and str(series.get("tasks_in_families")) == "97"
        and str(series.get("families_total")) == "64",
        f"multi={series.get('multi_member_families')} "
        f"tasks={series.get('tasks_in_families')} total={series.get('families_total')}",
    )

    # OA-F6-02：项目团队人数在 task 行上，此前无排名度量。
    team = await _call(registry, "weekly_rank", metric="project_team_size", mode="keep_ties", top=1)
    check(
        "OA-F6-02 项目团队人数 keep_ties 回 128 行，全为 1 人（与 gold 一致）",
        str(team.get("row_count")) == "128" and {int(r.get("metric_value")) for r in (team.get("rows") or [])} == {1},
        f"row_count={team.get('row_count')} values={sorted({r.get('metric_value') for r in (team.get('rows') or [])})}",
    )
    rounds_cut = await _call(registry, "weekly_rank", metric="progress_rounds", mode="cut", top=3)
    check(
        "回归 progress_rounds 名次档未受新度量影响（首行 18 期）",
        str((rounds_cut.get("rows") or [{}])[0].get("metric_value")) == "18",
        str([(r.get("task_name"), r.get("metric_value")) for r in (rounds_cut.get("rows") or [])][:2]),
    )

    # OA-C4-03：0 行是答案，但必须带扫描分母，否则读不出「查遍了都没有」。
    same_text = await _call(registry, "weekly_progress_coverage", scope="same_text", limit=200)
    check(
        "OA-C4-03 同文本比对 0 行，且带扫描分母 943 行 / 73 任务",
        str(same_text.get("total_count")) == "0"
        and str(same_text.get("scanned_rows")) == "943"
        and str(same_text.get("scanned_tasks")) == "73",
        f"total={same_text.get('total_count')} "
        f"scanned={same_text.get('scanned_rows')}/{same_text.get('scanned_tasks')}",
    )
    check(
        "OA-C4-03 caliber 说明是整字段比对且全期扫描（不是只看最新一期）",
        "TRIM" in str(same_text.get("caliber")) and "最新一期" in str(same_text.get("caliber")),
        str(same_text.get("caliber"))[:170],
    )

    # OA-T1-03：集团明细裸表 55 行，与加闸门的 46 是两个口径。
    raw_grp = await _call(registry, "weekly_group_stats", scope="project_group_raw", top=50)
    check(
        "OA-T1-03 集团明细按专项组裸表分档逐值等于 gold（10/6/6/6/5/5/5/4/4/2/2）",
        [int(r.get("rows_")) for r in (raw_grp.get("rows") or [])] == [10, 6, 6, 6, 5, 5, 5, 4, 4, 2, 2],
        str([(r.get("grp"), r.get("rows_")) for r in (raw_grp.get("rows") or [])][:4]),
    )
    check(
        "OA-T1-03 两档口径随 caliber_tiers 返回：裸表 55 / 加闸门 46",
        raw_grp.get("caliber_tiers") == {"raw_table": 55, "formal_task_gate": 46}
        and sum(int(r.get("formal_rows")) for r in (raw_grp.get("rows") or [])) == 46,
        json.dumps(raw_grp.get("caliber_tiers"), ensure_ascii=False),
    )
    check(
        # 这一档是 opt-in，必须指回任务口径那条路，否则会抢走「各组有多少任务」的问题。
        "OA-T1-03 caliber 指回 weekly_aggregate（免得抢走任务口径的问题）",
        "weekly_aggregate" in str(raw_grp.get("caliber")),
        str(raw_grp.get("caliber"))[-140:],
    )
    gated_grp = await _call(registry, "weekly_aggregate", group_by="project_group", board="group")
    check(
        "回归任务口径仍是 46（新增裸表档没污染 weekly_aggregate）",
        sum(int(r.get("cnt")) for r in (gated_grp.get("rows") or [])) == 46,
        f"sum={sum(int(r.get('cnt')) for r in (gated_grp.get('rows') or []))}",
    )

    return report()


def report() -> int:
    width = max(len(name) for _, name, _ in _results) if _results else 10
    failed = 0
    lines = []
    for status, name, detail in _results:
        if status == FAIL:
            failed += 1
            lines.append(f"[{status}] {name:<{width}}  {detail}")
        else:
            lines.append(f"[{status}] {name}")
    print("\n".join(lines))
    print(f"\n{len(_results) - failed}/{len(_results)} passed")
    return 1 if failed else 0


def main() -> int:
    if not os.environ.get("GUOSHU_WEEKLY_MCP_URL"):
        print("GUOSHU_WEEKLY_MCP_URL is not set", file=sys.stderr)
        print("start the mock service first: python mock-mcp/server.py", file=sys.stderr)
        return 2
    return anyio.run(run)


if __name__ == "__main__":
    raise SystemExit(main())
