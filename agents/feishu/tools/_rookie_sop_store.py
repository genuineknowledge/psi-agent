"""明细表与总览表的读写 —— 唯一碰飞书表格的模块。

刻意为之: bitable 操作通过注入的适配器对象调用(具备 search_records /
create_records / update_records 三个 async 方法), 这样单测传 fake 就能跑,
不需要飞书凭据。日期列(type 5)收发的是毫秒时间戳, 转换只发生在本模块,
上层只见 datetime.date。
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _rookie_sop_config as _cfg
import _rookie_sop_progress as _p
import _runtime_paths as _paths

_CONFIG_PATH = "config/rookie_sop.yaml"

# 飞书字段类型: 1 文本, 2 数字, 3 单选, 5 日期。19(查找引用) API 建不出来。
# 第一列是索引列, 必须是 1/2/5/13/15/20/22 之一 —— 两张表都用文本键列打头。
DETAIL_FIELDS: list[dict[str, Any]] = [
    {"field_name": "记录键", "type": 1},
    {"field_name": "姓名", "type": 1},
    {"field_name": "open_id", "type": 1},
    {"field_name": "模块", "type": 1},
    {"field_name": "项", "type": 1},
    {"field_name": "验收标准", "type": 1},
    # 必读材料的链接。填了它的行在详情页渲染成「链接 + 我已阅读并理解」,
    # 而不是笼统的「完成」勾选框。
    {"field_name": "必读链接", "type": 1},
    {
        "field_name": "状态",
        "type": 3,
        "property": {
            "options": [
                {"name": _p.STATUS_TODO, "color": 1},
                {"name": _p.STATUS_DONE, "color": 0},
                {"name": _p.STATUS_NA, "color": 2},
            ]
        },
    },
    {"field_name": "完成时间", "type": 5},
    {"field_name": "入职日", "type": 5},
    {"field_name": "截止日", "type": 5},
    {"field_name": "Mentor", "type": 1},
    {"field_name": "适用角色", "type": 1},
]

OVERVIEW_FIELDS: list[dict[str, Any]] = [
    # 姓名放第一列: 它是多维表格的主字段(首列即主字段, 表格视图里最显眼、
    # 分享出去第一眼就能认人)。open_id 是机器可读的路由键、对 HR 无意义,
    # 挪到最后一列, 只为出问题时能对上人。
    {"field_name": "姓名", "type": 1},
    {"field_name": "入职日", "type": 5},
    {"field_name": "入职第N天", "type": 2},
    {"field_name": "角色", "type": 1},
    {"field_name": "进度", "type": 1},
    {"field_name": "完成率", "type": 2},
    {"field_name": "状态", "type": 1},
    # 分模块完成情况与未完成清单 —— HR 要的是「谁卡在哪」, 光看总数看不出来。
    {"field_name": "各部分完成情况", "type": 1},
    {"field_name": "未完成内容", "type": 1},
    {"field_name": "逾期项数", "type": 2},
    {"field_name": "逾期项", "type": 1},
    {"field_name": "最后更新", "type": 5},
    {"field_name": "open_id", "type": 1},
]

_DATE_KEYS = ("完成时间", "入职日", "截止日", "最后更新")


async def load_config() -> dict[str, Any]:
    path = _paths.resolve_agent() / _CONFIG_PATH
    try:
        text = await path.read_text(encoding="utf-8")
    except FileNotFoundError, OSError:
        return {}
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def to_millis(value: date | None) -> int | None:
    if value is None:
        return None
    return int(datetime.combine(value, time()).timestamp() * 1000)


def from_millis(value: Any) -> date | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000).date()


def detail_row_fields(
    item: _cfg.SopItem,
    *,
    open_id: str,
    name: str,
    onboard: date,
    role_label: str = "",
) -> dict[str, Any]:
    """种下一行明细 —— 所有项一律种成 未完成, 包括开发环境项。

    刻意为之: 开发环境的 5 项与角色选择同在一张卡上、Day 1 就可点(见
    _rookie_sop_card.role_card), 所以它们从落地起就计入分母 —— Day 1 是 33。
    选「非研发人员」后 mark_module_na 把这 5 项标成 不适用, 分母降到 28;
    选「研发人员」则保持 33。
    (早先的做法是未答角色时先种成 不适用 让 Day 1 显示 28, 但那与「一张卡上
    5 项立即可点」矛盾 —— 可点却不计分母会让进度看起来对不上。)
    """
    status = _p.STATUS_TODO
    return {
        "记录键": f"{open_id}:{item.item_id}",
        "姓名": name,
        "open_id": open_id,
        "模块": item.module,
        "项": item.title,
        "验收标准": item.acceptance,
        "必读链接": item.url,
        "状态": status,
        "入职日": to_millis(onboard),
        "截止日": to_millis(_cfg.due_date(onboard, item.window_days)),
        "Mentor": "",
        "适用角色": role_label or ("仅研发" if item.dev_only else "全员"),
    }


def _parse_result(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError, TypeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _items_of(raw: str | dict[str, Any]) -> list[dict[str, Any]]:
    """飞书搜索类工具返回的是扁平结构 {ok, records, count, has_more, page_token,
    total} —— 没有 result 包装。"""
    payload = _parse_result(raw) if isinstance(raw, str) else raw
    for key in ("records", "items", "tables", "metas"):
        values = payload.get(key)
        if isinstance(values, list):
            return [i for i in values if isinstance(i, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        return _items_of(data)
    return []


def _page_info(raw: str) -> tuple[bool, str]:
    payload = _parse_result(raw)
    has_more = bool(payload.get("has_more"))
    page_token = str(payload.get("page_token") or "")
    return has_more, page_token


def _write_ok(raw: str) -> tuple[bool, str]:
    """create_records/update_records 的返回同样是扁平 {ok, created/updated, count}。

    ok 为假时把 message/error 带出来, 让调用方能报出真实原因而不是一句空的失败。
    """
    payload = _parse_result(raw)
    if payload.get("ok") is True:
        return True, ""
    return False, str(payload.get("message") or payload.get("error") or "write failed")


def _unwrap_text(value: Any) -> Any:
    """文本字段(type 1)在飞书 search_records 里回来的是富文本片段数组
    ``[{"text": ..., "type": "text"}, ...]``, 不是字符串 —— 按顺序拼接每段的
    ``text`` 才是人看的值。单选(type 3)、日期(type 5)等标量字段不受影响,
    原样返回, 免得把 状态/完成率 之类的整数也错拆一遍。

    防御性地处理: 空列表 → 空串; 列表里混着裸字符串(没有 dict 包装的分段)
    也按原样接上; 元素既不是 dict 也不是 str 的直接跳过(不拼进去, 不让一个
    脏元素污染整段文本); None 或非列表标量原样放回, 不当成文本字段处理。
    """
    if not isinstance(value, list):
        return value
    parts: list[str] = []
    for segment in value:
        if isinstance(segment, dict):
            parts.append(str(segment.get("text") or ""))
        elif isinstance(segment, str):
            parts.append(segment)
        # 既不是 dict 也不是 str 的分段(比如 None 或数字)悄悄跳过, 不拼进去。
    return "".join(parts)


def _row_of(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields")
    row: dict[str, Any] = dict(fields) if isinstance(fields, dict) else {}
    for key, value in row.items():
        row[key] = _unwrap_text(value)
    row["record_id"] = str(item.get("record_id") or "")
    for key in _DATE_KEYS:
        if key in row:
            row[key] = from_millis(row[key])
    return row


def _eq_filter(field_name: str, value: str) -> str:
    return json.dumps(
        {"conjunction": "and", "conditions": [{"field_name": field_name, "operator": "is", "value": [value]}]},
        ensure_ascii=False,
    )


MAX_PAGES = 50


async def fetch_detail(
    bitable: Any, app_token: str, detail_table_id: str, open_id: str
) -> tuple[list[dict[str, Any]], bool]:
    """拉取一个人的全部明细行 —— 翻页直到 has_more 为假, 绝不只读第一页。

    单页缺失会让 recompute_overview 用不完整的行数算进度, 所以这里循环翻页而
    不是信任 page_size 上限。防御性退出有两层: 响应说还有更多但没给新 token(空
    或与刚发出的相同)视为服务端异常, 停止而不是死循环; 服务端若持续给「有更多」
    且每次都换新 token(已见过的 token 集合挡不住这种), 循环页数上限
    MAX_PAGES(与 _feishu_api_impl.py 的翻页上限同一防线)兜底, 与其死循环挂死
    整个 fire=tool 回合, 不如报出「读到一半就停了」。

    返回 (rows, truncated) —— truncated 为真时调用方拿到的是不完整读取, 不能
    当成全量对待。
    """
    filter_json = _eq_filter("open_id", open_id)
    rows: list[dict[str, Any]] = []
    page_token = ""
    seen_tokens = {""}
    for _ in range(MAX_PAGES):
        raw = await bitable.search_records(
            app_token, detail_table_id, filter_json, page_size=500, page_token=page_token
        )
        rows.extend(_row_of(i) for i in _items_of(raw))
        has_more, next_token = _page_info(raw)
        if not has_more or not next_token or next_token in seen_tokens:
            return rows, False
        seen_tokens.add(next_token)
        page_token = next_token
    return rows, True


async def mark_done(
    bitable: Any,
    app_token: str,
    detail_table_id: str,
    *,
    open_id: str,
    item_id: str,
    today: date,
) -> dict[str, Any]:
    key = f"{open_id}:{item_id}"
    raw = await bitable.search_records(app_token, detail_table_id, _eq_filter("记录键", key), page_size=2)
    rows = [_row_of(i) for i in _items_of(raw)]
    if not rows:
        return {"ok": False, "error": f"detail row not found for {key}"}
    # page_size=2 是特意选的: 只需要区分「恰好一行」和「不止一行」, 不需要拉全部
    # 重复行。记录键理应唯一, 但重试等原因可能双写 —— 只标第一行会让重复行
    # 永远卡在未完成, 悄悄破坏一人一项一行的前提, 所以这里必须报出重复数。
    duplicates = len(rows) - 1
    row = rows[0]
    if str(row.get("状态") or "") == _p.STATUS_DONE:
        return {"ok": True, "already_done": True, "record_id": row["record_id"], "duplicates": duplicates}
    # 不查 ok 就返回成功是致命的: 框架此时已经消耗了这一行的墓碑并重绘掉了按钮,
    # 一旦这次写入被飞书拒绝, 这一项就再也点不了了, 却还停在未完成 —— 必须让
    # 调用方能看见写失败, 而不是替它掩盖。
    raw = await bitable.update_records(
        app_token,
        detail_table_id,
        json.dumps(
            [{"record_id": row["record_id"], "fields": {"状态": _p.STATUS_DONE, "完成时间": to_millis(today)}}],
            ensure_ascii=False,
        ),
    )
    ok, error = _write_ok(raw)
    if not ok:
        return {"ok": False, "error": f"update_records rejected: {error}", "record_id": row["record_id"]}
    return {"ok": True, "already_done": False, "record_id": row["record_id"], "duplicates": duplicates}


def _item_id_of(row: dict[str, Any]) -> str:
    """明细行的 item_id 藏在 记录键 = "{open_id}:{item_id}" 的后半段。"""
    key = str(row.get("记录键") or "")
    return key.rsplit(":", 1)[-1] if ":" in key else key


async def reset_progress(
    bitable: Any,
    app_token: str,
    detail_table_id: str,
    *,
    open_id: str,
) -> dict[str, Any]:
    """把这个人的全部明细行退回「未完成」, 并清掉完成时间与角色标记。

    用于 HR 点名发卡 —— 那意味着「重新开始」, 而不是接着上次的进度。
    刻意不删行重建: 行里还有入职日/截止日等播种时算好的字段, 原地改状态比
    删了重播种少一半往返, 也不会在中途失败时留下半套数据。
    """
    rows, _ = await fetch_detail(bitable, app_token, detail_table_id, open_id)
    stale = [r for r in rows if str(r.get("状态") or "") != _p.STATUS_TODO]
    if not stale:
        return {"ok": True, "reset": 0}

    records = [
        {
            "record_id": r["record_id"],
            "fields": {"状态": _p.STATUS_TODO, "完成时间": None, "适用角色": ""},
        }
        for r in stale
        if r.get("record_id")
    ]
    if not records:
        return {"ok": True, "reset": 0}

    raw = await bitable.update_records(app_token, detail_table_id, json.dumps(records, ensure_ascii=False))
    ok, error = _write_ok(raw)
    if not ok:
        return {"ok": False, "error": f"update_records rejected: {error}"}
    return {"ok": True, "reset": len(records)}


async def mark_module_na(
    bitable: Any,
    app_token: str,
    detail_table_id: str,
    *,
    open_id: str,
    module: str,
    today: date,
    exclude_item_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """把一个模块里除 exclude_item_ids 外、尚未完成的行标 不适用。

    刻意为之: exclude_item_ids 用于 role_confirmed 这类「同模块但全员适用」的项 ——
    它不该跟着 dev_only 的五项一起被非研发分支标不适用(需求 4)。
    """
    rows, truncated = await fetch_detail(bitable, app_token, detail_table_id, open_id)
    targets = [
        r
        for r in rows
        if str(r.get("模块") or "") == module
        and str(r.get("状态") or "") != _p.STATUS_DONE
        and _item_id_of(r) not in exclude_item_ids
    ]
    if not targets:
        result: dict[str, Any] = {"ok": True, "marked": 0}
        if truncated:
            result["truncated"] = True
        return result
    raw = await bitable.update_records(
        app_token,
        detail_table_id,
        json.dumps(
            [{"record_id": r["record_id"], "fields": {"状态": _p.STATUS_NA}} for r in targets],
            ensure_ascii=False,
        ),
    )
    ok, error = _write_ok(raw)
    if not ok:
        return {"ok": False, "error": f"update_records rejected: {error}"}
    result = {"ok": True, "marked": len(targets)}
    if truncated:
        result["truncated"] = True
    return result


async def recompute_overview(
    bitable: Any,
    app_token: str,
    overview_table_id: str,
    *,
    open_id: str,
    name: str,
    role: str,
    rows: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    """从明细整体重算总览行 —— 不做增量, 所以任何漏写都会在下一次调用时自愈。"""
    fields = _p.overview_fields(rows, today, name, open_id, role)
    for key in _DATE_KEYS:
        if key in fields:
            fields[key] = to_millis(fields[key])

    raw = await bitable.search_records(app_token, overview_table_id, _eq_filter("open_id", open_id), page_size=2)
    existing = _items_of(raw)
    if existing:
        record_id = str(existing[0].get("record_id") or "")
        write_raw = await bitable.update_records(
            app_token,
            overview_table_id,
            json.dumps([{"record_id": record_id, "fields": fields}], ensure_ascii=False),
        )
        ok, error = _write_ok(write_raw)
        if not ok:
            return {"ok": False, "error": f"update_records rejected: {error}", "record_id": record_id}
        return {"ok": True, "created": False, "record_id": record_id, "fields": fields}

    write_raw = await bitable.create_records(
        app_token, overview_table_id, json.dumps([{"fields": fields}], ensure_ascii=False)
    )
    ok, error = _write_ok(write_raw)
    if not ok:
        return {"ok": False, "error": f"create_records rejected: {error}"}
    return {"ok": True, "created": True, "fields": fields}
