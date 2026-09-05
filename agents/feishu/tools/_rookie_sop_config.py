"""SOP 清单解析 —— 纯逻辑，不碰飞书，便于单测。

刻意为之: 清单本身在 agent 包 config/rookie_sop.yaml 里, 改 SOP 不用改代码
(与 config/handbook_onboarding.yaml 同一模式)。
"""

from __future__ import annotations

# ruff: noqa: RUF002
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ROLE_DEV = "dev"
ROLE_NONDEV = "nondev"


@dataclass(frozen=True)
class SopItem:
    item_id: str
    module: str
    title: str
    acceptance: str
    window_days: int
    dev_only: bool
    # 填了 url 的是必读材料: 详情页渲染成链接 + 单个「我已阅读并理解」勾选框,
    # 而不是笼统的「完成」—— 阅读类的验收就是「读过并理解」。
    url: str = ""


def load_sop(cfg: dict[str, Any]) -> list[SopItem]:
    """把 yaml 的 modules → items 两层结构展开成扁平条目列表, 保持声明顺序。"""
    items: list[SopItem] = []
    modules = cfg.get("modules")
    if not isinstance(modules, list):
        return items
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_name = str(module.get("name") or "").strip()
        window_days = module.get("window_days", 1)
        window = window_days if isinstance(window_days, int) and window_days > 0 else 1
        raw_items = module.get("items")
        if not isinstance(raw_items, list):
            continue
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get("id") or "").strip()
            if not item_id:
                continue
            items.append(
                SopItem(
                    item_id=item_id,
                    module=module_name,
                    title=str(raw.get("title") or "").strip() or item_id,
                    acceptance=str(raw.get("acceptance") or "").strip(),
                    window_days=window,
                    dev_only=bool(raw.get("dev_only")),
                    url=str(raw.get("url") or "").strip(),
                )
            )
    return items


def due_date(onboard_date: date, window_days: int) -> date:
    """SOP 的「Day 1-3」表示第 1 到第 3 天, 所以窗口 3 天的截止日是入职日 +2。"""
    window = window_days if window_days > 0 else 1
    return onboard_date + timedelta(days=window - 1)


def day_index(onboard_date: date, today: date) -> int:
    """入职第 N 天, 自然日计数, 入职日为第 1 天。

    刻意为之: 不跳过周末 —— SOP 的 Day 1-7 本身就是自然日窗口。
    """
    return (today - onboard_date).days + 1


def applicable_items(items: list[SopItem], role: str) -> list[SopItem]:
    """按角色过滤。角色未确认("")时也排除 dev_only, 免得进度分母虚高。"""
    if role.strip().casefold() == ROLE_DEV:
        return list(items)
    return [i for i in items if not i.dev_only]
