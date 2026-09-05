"""运行时接线: bitable 适配器、base/表的一次性创建、状态文件、卡片编排。

刻意为之: app_token 与两个 table_id 是运行时才有的值, 不能写进 yaml, 所以存
workspace 的 .psi/rookie_sop/base.json (与 feishu_auth 把 token 放
.psi/feishu/uat.json 同一惯例)。
"""

from __future__ import annotations

# ruff: noqa: RUF002, E402, RUF001, PLC0415
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_card as _card
import _rookie_sop_config as _cfg
import _rookie_sop_progress as _p
import _rookie_sop_store as _store
import _runtime_paths as _paths

_STATE_REL = ".psi/rookie_sop/base.json"
_DEV_MODULE = "开发环境"
# 公开别名: rookie_sop_tick 重绘整卡时要判断是不是开发环境那张
DEV_MODULE = _DEV_MODULE
_MAX_ROWS_PER_CARD = 40


def bitable_adapter() -> Any:
    """把真实 feishu_bitable_* 工具包成 store 期望的适配器。"""
    import feishu_bitable as _bt

    class _Adapter:
        search_records = staticmethod(_bt.feishu_bitable_search_records)
        create_records = staticmethod(_bt.feishu_bitable_create_records)
        update_records = staticmethod(_bt.feishu_bitable_update_records)

    return _Adapter()


async def load_state(workspace: str = "") -> dict[str, Any]:
    """读这个新人的 state 文件。

    workspace 可显式传入 —— 定时任务(fire=tool)执行工具时框架不建立路径上下文
    (schedule_registry._fire_tool 直接 await func(**args), 没有 runtime_scope),
    于是 resolve_workspace() 会回落到 agent 包目录、读不到 state。定时那条路径
    因此必须自带 workspace, 不能依赖 ContextVar。
    """
    path = _paths.resolve_workspace(workspace) / _STATE_REL
    try:
        text = await path.read_text(encoding="utf-8")
    except FileNotFoundError, OSError:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def save_state(state: dict[str, Any], workspace: str = "") -> None:
    """写这个新人的 state。

    workspace 必须显式传 **新人自己的** 目录, 不能靠 resolve_workspace() ——
    HR 说「给某人发入职卡」时工具跑在 HR 的 session 里, 默认解析出来的是 HR 的
    workspace, state 会落到 HR 名下(实测踩过: 罗霖发卡, state 写进了
    users/ou_f330a7e0.../ 而不是王炜博的目录)。后果是新人那侧的定时同步与催办
    都找不到数据, 而且每个 HR 各攒一份、同一个新人被不同 HR 发卡就分裂成多份表。
    """
    path = _paths.resolve_workspace(workspace) / _STATE_REL
    await path.parent.mkdir(parents=True, exist_ok=True)
    await path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _module_window(items: list[_cfg.SopItem], module: str) -> int:
    return next((i.window_days for i in items if i.module == module), 1)


def due_text_for(onboard: date, window_days: int) -> str:
    """公开别名 —— rookie_sop_tick 重绘时要复算同样的截止文案。"""
    return _due_text(onboard, window_days)


def _due_text(onboard: date, window_days: int) -> str:
    if window_days <= 1:
        return "Day 1 截止"
    return f"Day 1-{window_days} 截止（{_cfg.due_date(onboard, window_days)}）"


def plan_module_cards(
    items: list[_cfg.SopItem],
    rows: list[dict[str, Any]],
    onboard: date,
    today: date,
    sop_url: str,
) -> list[dict[str, Any]]:
    """按模块编排要发的卡。开发环境先问角色, 其余直接列勾选行。"""
    modules: list[str] = []
    for item in items:
        if item.module not in modules:
            modules.append(item.module)

    plans: list[dict[str, Any]] = []
    for module in modules:
        window = _module_window(items, module)
        due_text = _due_text(onboard, window)
        module_rows = [r for r in rows if str(r.get("模块") or "") == module][:_MAX_ROWS_PER_CARD]
        done = sum(1 for r in module_rows if str(r.get("状态") or "") == _p.STATUS_DONE)
        if module == _DEV_MODULE:
            # 一张卡装完角色选择与开发项 —— multi_use 按 action 逐个消费, 点掉角色
            # 按钮不影响其余行, 所以不必先发角色卡再发第二张。role_confirmed 也在
            # 这个模块里, 它有自己的勾选按钮, 但点角色按钮时会被工具一并标完成。
            card, handlers = _card.role_card(
                due_text,
                dev_rows=module_rows,
                sop_url=sop_url,
                progress_text=f"{done}/{len(module_rows)}",
                role_answered=False,
                today=today,
            )
            plans.append({"module": module, "card": card, "handlers": handlers, "is_role_card": True})
            continue
        card, handlers = _card.module_card(
            module, module_rows, f"{done}/{len(module_rows)}", due_text, sop_url, today=today
        )
        plans.append({"module": module, "card": card, "handlers": handlers, "is_role_card": False})
    return plans


def should_send_cards(*, is_first_send: bool, force_resend: bool) -> bool:
    """卡片/催办是否要发 —— 首次发, 或调用方显式要求强发。"""
    return is_first_send or force_resend


async def _base_url(app_token: str) -> str:
    """多维表格的正式链接(带租户域名); 查不到时回退通用域名。

    刻意不硬编码 https://feishu.cn/base/... —— 每个租户有自己的域名(本租户是
    genuineknowledge.feishu.cn), 通用域名拼出来的链接 HR 点开可能进不去。
    实测: 同一个 app_token, 飞书 metas/batch_query 返回的才是能打开的那个。
    (文档链接早先踩过同一个坑, 见 _rookie_sop_docapi.fetch_doc_url。)
    """
    import feishu_api as _api

    try:
        res = _store._parse_result(
            await _api.feishu_api(
                "POST",
                "/open-apis/drive/v1/metas/batch_query",
                body_json=json.dumps(
                    {"request_docs": [{"doc_token": app_token, "doc_type": "bitable"}], "with_url": True},
                    ensure_ascii=False,
                ),
            )
        )
        for meta in _store._items_of(res) or ((res.get("data") or {}).get("metas") or []):
            url = str((meta or {}).get("url") or "").strip()
            if url:
                return url
    except Exception:  # 查链接失败不该让建库/发卡失败
        pass
    return f"https://feishu.cn/base/{app_token}"


async def ensure_base(cfg: dict[str, Any], workspace: str = "") -> dict[str, Any]:
    """全组织共用一个 base（总览表一人一行、明细表一人 N 行）。

    坐标优先取配置里的 ``shared_base`` —— 刻意不按人存: state 在每个人自己的
    workspace 里, 于是每个新人首次发卡都发现「我这儿没有 app_token」而各建一个新库。
    实测踩过: 16 个人建出 16 个互不相干的库, HR 打开只看得到最早那两个人, 19:00 的
    异常提醒也只扫得到一个库。库坐标是全组织共享的事实, 不该跟着人走。

    配置留空时才建新库, 并把 id 回填进 state ——首次上线用得到; 拿到 id 后填进
    ``config/rookie_sop.yaml`` 的 ``shared_base`` 就固定下来了。
    """
    shared = cfg.get("shared_base")
    if isinstance(shared, dict):
        app_token = str(shared.get("app_token") or "").strip()
        overview_id = str(shared.get("overview_table_id") or "").strip()
        detail_id = str(shared.get("detail_table_id") or "").strip()
        if app_token and overview_id and detail_id:
            # 私有数据(文档/卡片映射)仍留在各人 state 里, 只把库坐标换成共享的
            state = await load_state(workspace)
            state.update(
                {
                    "app_token": app_token,
                    "overview_table_id": overview_id,
                    "detail_table_id": detail_id,
                    "table_url": await _base_url(app_token),
                }
            )
            await save_state(state, workspace)
            return state

    state = await load_state(workspace)
    if state.get("app_token") and state.get("detail_table_id") and state.get("overview_table_id"):
        return state

    import feishu_api as _api
    import feishu_bitable as _bt

    company = str(cfg.get("company_name") or "").strip() or "团队"
    created = _store._parse_result(
        await _api.feishu_api(
            "POST",
            "/open-apis/bitable/v1/apps",
            body_json=json.dumps({"name": f"{company}新人入职进度"}, ensure_ascii=False),
        )
    )
    # feishu_api 走的是通用 _resp_to_result 信封: {ok, code, msg, data} —— 没有
    # "result" 这一层, app_token 在 data.app.app_token 里(飞书原始文档的结构)。
    app_token = str(((created.get("data") or {}).get("app") or {}).get("app_token") or "")
    if not app_token:
        return {"ok": False, "error": f"cannot create bitable base: {created}"}

    # 总览表先建 —— 多维表格里表的排列顺序就是创建顺序, HR 打开时第一眼看到的
    # 应该是「谁完成到什么程度」的总览, 而不是几百行的逐项明细。
    overview = _store._parse_result(
        await _bt.feishu_bitable_create_table(
            app_token, "入职总览", json.dumps(_store.OVERVIEW_FIELDS, ensure_ascii=False)
        )
    )
    detail = _store._parse_result(
        await _bt.feishu_bitable_create_table(
            app_token, "入职明细", json.dumps(_store.DETAIL_FIELDS, ensure_ascii=False)
        )
    )
    # 飞书建 base 时会自带一张名为「数据表」的空表, 而且它排在最前面 —— 会把
    # 总览表挤到第二位, HR 打开先看到一张空表。删掉它。
    # 失败不阻断建库(顶多多一张空表), 但记进返回值, 不静默。
    stray_note = ""
    try:
        listed = _store._parse_result(
            await _api.feishu_api(
                "GET",
                f"/open-apis/bitable/v1/apps/{app_token}/tables",
                query_json=json.dumps({"page_size": 20}, ensure_ascii=False),
            )
        )
        for table in _store._items_of(listed):
            if str(table.get("name") or "") == "数据表":
                await _api.feishu_api(
                    "DELETE", f"/open-apis/bitable/v1/apps/{app_token}/tables/{table.get('table_id')}"
                )
    except Exception as exc:
        stray_note = f"stray default table not removed: {exc!r}"

    # feishu_bitable_create_table 是扁平结构 {ok, table_id, name, default_view_id,
    # field_ids} —— table_id 直接在顶层, 同样没有 "result" 包装。
    detail_table_id = str(detail.get("table_id") or "")
    overview_table_id = str(overview.get("table_id") or "")
    # 不落半成品状态: 少了任何一个 table_id, 下次运行会拿着空 id 去调 fetch_detail /
    # create_records, 换回一个不明所以的飞书原始错误, 不如现在就报清楚缺了什么。
    if not detail_table_id or not overview_table_id:
        missing = [
            n for n, v in (("detail_table_id", detail_table_id), ("overview_table_id", overview_table_id)) if not v
        ]
        return {
            "ok": False,
            "error": f"table creation incomplete, missing {missing}: detail={detail}, overview={overview}",
        }
    state = {
        "app_token": app_token,
        "detail_table_id": detail_table_id,
        "overview_table_id": overview_table_id,
        "table_url": await _base_url(app_token),
    }
    await save_state(state, workspace)
    # 自带空表没删掉不影响功能, 但要让人知道 —— 否则 HR 打开会先看到一张空表,
    # 而没人清楚它是哪来的。刻意不写进 state: 那是持久化的库坐标, 不该混入一次性的告警。
    if stray_note:
        return {**state, "warning": stray_note}
    return state
