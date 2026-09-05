"""从明细行算进度、逾期、今日到期, 以及总览表的一行投影 —— 纯逻辑, 不碰飞书。

刻意为之: 总览行永远由本模块从明细整体重算, 不做增量加减。飞书 bitable 的
「查找引用」字段(type 19) API 建不出来、公式列也写不进去, 所以总览只能双写维护;
「整体重算」让任何一次漏写都会在下一次勾选时自愈, 不累积漂移。
"""

from __future__ import annotations

# ruff: noqa: RUF001
from dataclasses import dataclass, field
from datetime import date
from typing import Any

STATUS_TODO = "未完成"
STATUS_DONE = "已完成"
STATUS_NA = "不适用"

ROLE_LABELS = {"dev": "研发", "nondev": "非研发", "": "待确认"}


@dataclass
class Progress:
    done: int = 0
    total: int = 0
    percent: int = 0
    overdue: list[dict[str, Any]] = field(default_factory=list)
    due_today: list[dict[str, Any]] = field(default_factory=list)
    next_due: dict[str, Any] | None = None
    all_done: bool = False


def _due(row: dict[str, Any]) -> date | None:
    value = row.get("截止日")
    return value if isinstance(value, date) else None


def summarize(rows: list[dict[str, Any]], today: date) -> Progress:
    """分母只算「适用」的行 —— 不适用的既不进分子也不进分母。"""
    applicable = [r for r in rows if str(r.get("状态") or "") != STATUS_NA]
    done_rows = [r for r in applicable if str(r.get("状态") or "") == STATUS_DONE]
    todo_rows = [r for r in applicable if str(r.get("状态") or "") != STATUS_DONE]

    total = len(applicable)
    done = len(done_rows)
    percent = round(done * 100 / total) if total else 0

    overdue: list[dict[str, Any]] = []
    due_today: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    for row in todo_rows:
        due = _due(row)
        if due is None:
            future.append(row)
        elif due < today:
            overdue.append(row)
        elif due == today:
            due_today.append(row)
        else:
            future.append(row)

    future.sort(key=lambda r: _due(r) or date.max)
    return Progress(
        done=done,
        total=total,
        percent=percent,
        overdue=overdue,
        due_today=due_today,
        next_due=future[0] if future else None,
        # 刻意为之: total==0 不算「全部完成」, 否则空清单会误发出新手村卡
        all_done=bool(total) and done == total,
    )


def overview_fields(
    rows: list[dict[str, Any]],
    today: date,
    name: str,
    open_id: str,
    role: str,
) -> dict[str, Any]:
    """总览表的一行 —— 纯投影, 删掉重建也不丢信息。"""
    progress = summarize(rows, today)
    onboard = next((r["入职日"] for r in rows if isinstance(r.get("入职日"), date)), None)
    return {
        "open_id": open_id,
        "姓名": name,
        "入职日": onboard,
        "入职第N天": (today - onboard).days + 1 if onboard else 0,
        "角色": ROLE_LABELS.get(role.strip().casefold(), "待确认"),
        "进度": f"{progress.done}/{progress.total}",
        "完成率": progress.percent,
        "逾期项数": len(progress.overdue),
        "逾期项": "、".join(str(r.get("项") or "") for r in progress.overdue),
        "状态": "已出新手村" if progress.all_done else "进行中",
        # HR 要的是「谁卡在哪」, 光看 11/28 看不出来 —— 所以把分模块小计和未完成
        # 条目名直接投影进总览, 免得为了看一个人卡在哪还要去翻几百行明细。
        "各部分完成情况": module_tally_text(rows),
        "未完成内容": unfinished_text(rows),
        "最后更新": today,
    }


def module_tally_text(rows: list[dict[str, Any]]) -> str:
    """各模块「x/y」一行文字, 按模块在明细里出现的顺序。"""
    modules: list[str] = []
    for row in rows:
        module = str(row.get("模块") or "")
        if module and module not in modules:
            modules.append(module)
    parts: list[str] = []
    for module in modules:
        module_rows = [r for r in rows if str(r.get("模块") or "") == module]
        # 不适用的项不进分母 —— 非研发的开发环境项不该让他看起来永远差几项
        applicable = [r for r in module_rows if str(r.get("状态") or "") != STATUS_NA]
        if not applicable:
            continue
        done_n = sum(1 for r in applicable if str(r.get("状态") or "") == STATUS_DONE)
        parts.append(f"{module} {done_n}/{len(applicable)}")
    return " · ".join(parts)


def unfinished_text(rows: list[dict[str, Any]], limit: int = 12) -> str:
    """未完成条目名, 按模块分组。超过 limit 条就截断并标注剩余数量 ——
    单元格塞几百字没人看, 而 HR 真正要的是「还差哪几件」。
    """
    pending = [r for r in rows if str(r.get("状态") or "") == STATUS_TODO]
    if not pending:
        return ""
    names = [str(r.get("项") or "") for r in pending]
    if len(names) <= limit:
        return "、".join(names)
    return "、".join(names[:limit]) + f"…（另 {len(names) - limit} 项）"
