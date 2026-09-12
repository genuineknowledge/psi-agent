"""Load the user-editable company TODO SOP config (``config/todo-sop.yaml``).

The company-specific TODO judgment requirements (three-level schema, priority quadrants,
quota, mentor-check deadline, leave approval codes, closure elements, ledger field schema,
completion / truthfulness verdict words) are the one thing that changes when this product
is sold to another company. They live in ``config/todo-sop.yaml`` and are read on demand;
a missing or malformed file returns ``{}`` so every caller falls back to its own built-in
default, keeping behaviour unchanged rather than silently misjudging.
"""

from __future__ import annotations

import json
from typing import Any

import _feishu_impl as _core
import _runtime_paths as _paths
import yaml
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest
from loguru import logger

from _feishu.sheet import _col_letter

_CONFIG_REL = "config/todo-sop.yaml"


async def load_todo_sop() -> dict[str, Any]:
    """Return the parsed ``config/todo-sop.yaml``, or ``{}`` when unreadable / invalid.

    Callers must fall back to their built-in default on ``{}``; ``mentor_ledger.py`` keeps
    ``_LEDGER_SCHEMA_FIELDS`` as that fallback, so a broken config never changes the ledger
    schema silently.
    """
    path = _paths.resolve_agent() / _CONFIG_REL
    try:
        text = await path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        logger.warning(f"todo-sop config unreadable ({exc}); callers fall back to defaults")
        return {}
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning(f"todo-sop config is not valid YAML ({exc}); callers fall back to defaults")
        return {}
    if not isinstance(loaded, dict) or not _valid_ledger(loaded):
        logger.warning("todo-sop config lacks a valid ledger_schema; callers fall back to defaults")
        return {}
    return loaded


def _valid_ledger(cfg: dict[str, Any]) -> bool:
    """The ledger schema is the tool-critical part; require it to be present and well-typed."""
    ledger = cfg.get("ledger_schema")
    if not isinstance(ledger, dict):
        return False
    fields = ledger.get("fields")
    if not isinstance(fields, list) or not fields:
        return False
    return all(isinstance(f, dict) and f.get("field_name") and "type" in f for f in fields)


def _build_wiki_get_node_request(token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces/get_node"
    req.add_query("token", token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def todo_fill_status_impl(
    board_link: str, cycle_date: str, mentor_name: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Deterministic 缺写 pipeline: read board → group by mentor → classify → check leave.

    Returns buckets 缺写/请假免填/解析失败/已填. 判定口径全在代码里:
    - 已离职/冻结(通讯录已移除或 status 标记不活跃)在工具内部剔除:
      不查假、不进缺写、**不返回**——调用方拿不到名字,输出天然不体现;
    - 解析失败(重名等)不进缺写,归「解析失败」;
    - 在职且当期格空白 → 查假,该日命中已通过请假 = 请假免填,否则 = 缺写;
    - 在职且当期格非空 = 已填。
    """
    from _feishu.contact import member_status_check_impl  # noqa: PLC0415
    from _feishu.leave import query_leave_impl  # noqa: PLC0415
    from _feishu.sheet import read_sheet_grid_impl  # noqa: PLC0415

    # 1. wiki 链接换 obj_token
    wiki_token = board_link.rstrip("/").split("/")[-1]
    res = await _core._invoke(_build_wiki_get_node_request(wiki_token), user_key=user_key)
    if not res.get("ok"):
        return res
    node = res.get("data", {}).get("node", {}) if isinstance(res.get("data"), dict) else {}
    obj_token = node.get("obj_token", "")
    if not obj_token:
        return _core._error(f"wiki get_node 没拿到 obj_token: {board_link}")

    # 2. 表头限列单行读(全宽整表读会被行边界字符预算截成两三行,名单全丢)
    header_grid = await read_sheet_grid_impl(obj_token, range_="!A1:AZ1", max_rows=1, user_key=user_key)
    if not header_grid.get("ok"):
        return header_grid
    header_rows = header_grid.get("rows", []) or header_grid.get("values", []) or []
    if not header_rows:
        return _core._error("看板表读不到内容。")

    # 3. 表结构解析:第 1 行表头;人名列/mentor 列/cycle_date 列按表头文字找
    header = [str(c).strip() for c in header_rows[0]]
    person_col = _find_col(header, ("负责人", "人", "姓名"))
    mentor_col = _find_col(header, ("mentor", "上级", "导师"))
    if person_col < 0:
        return _core._error(f"人名列认不出来,表头: {header[:8]}")
    date_col = _find_col(header, (cycle_date,))

    # 4. 名单:人员行 = 人名列非空;mentor 过滤。
    #    宽列(A:AZ)整行读会被行边界字符预算截成每页 1 行(一行 20+ 个历史日期
    #    单元格,文本总和轻易超预算),当期列整列读也会被截成十来行 ——
    #    拆三段读:人名列+上级列(3 列,轻)分页拿名单;组内行号区间单独读当期列。
    roster: dict[int, tuple[str, str]] = {}  # sheet 行号 -> (name, mentor)
    start_row = 2  # 表头占第 1 行
    for _ in range(8):  # 最多翻 8 页(400 行)
        grid = await read_sheet_grid_impl(
            obj_token, range_="!A1:C400", max_rows=50, start_row=start_row, user_key=user_key
        )
        if not grid.get("ok"):
            return grid
        for i, row in enumerate(grid.get("rows", []) or []):
            if len(row) <= person_col:
                continue
            name = _norm_name(str(row[person_col]))
            if not name:
                continue
            m = _norm_name(str(row[mentor_col])) if mentor_col >= 0 and len(row) > mentor_col else ""
            roster[start_row + i] = (name, m)
        if not grid.get("has_more"):
            break
        start_row = grid.get("next_start_row") or start_row + 1
    group_rows = [r for r, (_, m) in roster.items() if not mentor_name or m == _norm_name(mentor_name)]
    fill_map: dict[int, bool] = {}
    if group_rows and date_col >= 0:
        col_letter = _col_letter(date_col)
        lo, hi = min(group_rows), max(group_rows)
        fg = await read_sheet_grid_impl(
            obj_token,
            range_=f"!{col_letter}{lo}:{col_letter}{hi}",
            max_rows=hi - lo + 1,
            user_key=user_key,
        )
        if fg.get("ok"):
            for i, row in enumerate(fg.get("rows", []) or []):
                if row:
                    fill_map[lo + i] = bool(str(row[0]).strip())
    people: list[dict[str, Any]] = [{"name": roster[r][0], "filled": fill_map.get(r, False)} for r in group_rows]

    # 5. 离职/在职分类(确定性)
    classified = await member_status_check_impl([p["name"] for p in people], user_key)
    if not classified.get("ok"):
        return classified
    resigned = set(classified.get("resigned", []))
    unresolved = set(classified.get("unresolved", []))
    active_names = [a["name"] for a in classified.get("active", [])]

    # 6. 在职且空白的查假(一次窗口查询全部名单)
    blanks = [p["name"] for p in people if not p["filled"] and p["name"] in active_names]
    on_leave: set[str] = set()
    needs_fix: set[str] = set()
    if blanks:
        leave = await query_leave_impl(
            await _leave_code(),
            date_from=cycle_date,
            date_to=cycle_date,
            names_json=json.dumps(blanks, ensure_ascii=False),
            user_key=user_key,
        )
        if leave.get("ok"):
            for item in leave.get("on_leave", []) if isinstance(leave.get("on_leave"), list) else []:
                if item.get("hit_dates"):
                    on_leave.add(str(item.get("name", "")))
            if leave.get("name_lookup_error"):
                needs_fix.update(blanks)  # 名字对不上请假记录,宁可不判缺写

    # 7. 组装五类
    return _build_buckets(cycle_date, mentor_name, people, resigned, unresolved, on_leave, needs_fix)


def _build_buckets(
    cycle_date: str,
    mentor_name: str,
    people: list[dict[str, Any]],
    resigned: set[str],
    unresolved: set[str],
    on_leave: set[str],
    needs_fix: set[str],
) -> dict[str, Any]:
    """Pure bucket assembly — every person lands in exactly one bucket."""
    bucket = {
        "ok": True,
        "cycle_date": cycle_date,
        "mentor_name": mentor_name or "(全部)",
        "缺写": [],
        "请假免填": [],
        "解析失败": sorted(unresolved),
        "已填": [],
    }
    for p in people:
        n = p["name"]
        if n in resigned or n in unresolved:
            continue
        if p["filled"]:
            bucket["已填"].append(n)
        elif n in on_leave:
            bucket["请假免填"].append(n)
        elif n in needs_fix:
            bucket["解析失败"].append(n)
        else:
            bucket["缺写"].append(n)
    return bucket


async def _leave_code() -> str:
    """请假审批定义码:读 config,读不到退回内置默认(与既有口径一致)。"""
    sop = await load_todo_sop()
    leave = sop.get("leave", {}) if isinstance(sop.get("leave"), dict) else {}
    code = str(leave.get("leave_approval_code", "")).strip()
    return code or _LEAVE_CODE_FALLBACK


_LEAVE_CODE_FALLBACK = "99EEC396-536A-4C7A-8B2D-412584E35CE3"


def _norm_name(cell: str) -> str:
    """看板人名列/上级列常带 ``@`` 前缀(如 ``@孙逊``),匹配前两边都剥掉。"""
    return cell.strip().lstrip("@").strip()


def _find_col(header: list[str], candidates: tuple[str, ...]) -> int:
    """Locate a column by header text; exact match first, then substring."""
    for i, cell in enumerate(header):
        if cell in candidates:
            return i
    for i, cell in enumerate(header):
        for cand in candidates:
            if cand and cand in cell:
                return i
    return -1
