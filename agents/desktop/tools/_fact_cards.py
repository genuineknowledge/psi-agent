"""资料卡加载器 —— 政策参数的唯一数据源(policy_query / subsidy_calc 共用)。

资料卡是 YAML, 住在能力包根 ``<agent>/fact-cards/``。两个工具都经本模块读它,
工具里不得再留第二份参数 —— 那正是《省钱场景交接》§7 坑 1「参数两处重复, 改一处
必改另一处」的成因。

缓存按 ``(mtime_ns, size)`` 失效: 改完卡无需重启进程即可生效。工具注册表按文件
hash 热重载是同一套期望, 只是资料卡不在 ``tools/`` 下, 内核不会替我们盯着它,
所以这道失效判据必须自己写。

合并(档位默认 + 品类覆盖)也只在本模块做一次。两个工具各写一遍就等于把刚消掉的
分叉换个地方重建。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import yaml
from loguru import logger

_CARDS_DIRNAME = "fact-cards"

# path -> ((mtime_ns, size), card)
_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}

# 档位级字段, 品类可逐项覆盖。机器值在前, 展示措辞在后。
_TIER_FIELDS = (
    "rate",
    "cap",
    "price_gate",
    "energy_required",
    "rate_label",
    "cap_label",
    "energy_label",
    "gate_label",
    "quota_label",
    "category_label",
    "source",
)


def _roots() -> list[Path]:
    """能力包根: 就是本文件所在的那个包(``.../<pack>/tools/_fact_cards.py`` -> ``<pack>``)。

    **刻意不走 ``_runtime_paths.agent_dir()``** —— 那个模块名在两个能力包里同名
    (desktop / feishu 各有 ``tools/_runtime_paths.py``), 靠裸名 import 时谁先落在
    ``sys.path`` 上谁赢: 全量跑测试时两个包的 tools 目录同时在场, ``agent_dir()``
    会指到 feishu 包, 于是资料卡跑到别人家里去找(实测就是这么红的)。

    ``__file__`` 按构造就是「定义这两个工具的那个包」, 与内核解析私有模块时
    「提问层优先」同源: 内容分层下用户层覆盖 ``_fact_cards`` 时, 赢的是覆盖层那份,
    它的 ``parents[1]`` 也正是覆盖层根 —— 该读的就是那张卡。
    """
    return [Path(__file__).resolve().parents[1]]


async def load_card(name: str) -> dict[str, Any]:
    """读 ``<agent>/fact-cards/<name>.yaml``; 找不到抛 ``FileNotFoundError``。"""
    tried: list[str] = []
    for root in _roots():
        path_str = str(root / _CARDS_DIRNAME / f"{name}.yaml")
        try:
            stat = await anyio.Path(path_str).stat()
        except OSError:
            tried.append(path_str)
            continue
        signature = (stat.st_mtime_ns, stat.st_size)
        hit = _cache.get(path_str)
        if hit is not None and hit[0] == signature:
            return hit[1]
        text = await anyio.Path(path_str).read_text(encoding="utf-8")
        card = yaml.safe_load(text)
        if not isinstance(card, dict):
            raise ValueError(f"资料卡 {path_str} 顶层必须是 mapping, 实际是 {type(card).__name__}")
        _cache[path_str] = (signature, card)
        logger.debug(f"Fact card loaded: {path_str} ({len(text)} chars)")
        return card
    raise FileNotFoundError(f"找不到资料卡 {name}.yaml; 已试: {tried}")


def category_names(card: dict[str, Any]) -> tuple[str, ...]:
    """资料卡登记的品类名 —— 工具侧唯一认得的取值集合。"""
    categories = card.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("资料卡的 categories 必须是 mapping")
    return tuple(categories)


def _tier_params(card: dict[str, Any], kind: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """返回 ``(档位名, 档位定义, 品类条目)``; 品类未登记抛 ``KeyError``。"""
    categories = card.get("categories")
    tiers = card.get("tiers")
    if not isinstance(categories, dict) or not isinstance(tiers, dict):
        raise ValueError("资料卡缺少 categories / tiers")
    entry = categories.get(kind)
    if not isinstance(entry, dict):
        raise KeyError(kind)
    tier_name = entry.get("tier")
    tier = tiers.get(tier_name)
    if not isinstance(tier, dict):
        raise ValueError(f"资料卡里品类 {kind!r} 指向了未定义的档位 {tier_name!r}")
    return str(tier_name), tier, entry


def params_of(card: dict[str, Any], kind: str) -> dict[str, Any]:
    """品类生效参数 = 档位默认 + 品类覆盖。

    机器值(``rate`` / ``cap`` / ``price_gate`` / ``energy_required``)与展示措辞
    一并合并, 使调用方只读一个 dict。**合并只此一处。**
    """
    tier_name, tier, entry = _tier_params(card, kind)
    params: dict[str, Any] = {
        "tier": tier_name,
        "tier_label": tier.get("label"),
        # display 刻意不参与上面的档位合并: 档位自己的展示名在 display_name,
        # 两者同名会在合并里串(品类没写 display 时会拿到档位名「家电」而不是品类名)。
        "display": entry.get("display") or kind,
    }
    for field in _TIER_FIELDS:
        params[field] = entry[field] if field in entry else tier.get(field)
    return params


def _supported_layout(card: dict[str, Any]) -> dict[str, Any]:
    """拼装规则全部由卡提供 —— 代码里不留默认值, 否则又是一个第二数据源。

    这些模板渲染出来是要给用户看的中文(全角冒号、分号), 写在代码里既会被
    RUF001/002 拦, 也会与卡里的品类表脱节。
    """
    layout = card.get("supported_text")
    if not isinstance(layout, dict):
        raise ValueError("资料卡缺少 supported_text")
    missing = [key for key in ("prefix", "tier_tpl", "tier_join", "item_join") if key not in layout]
    if missing:
        raise ValueError(f"资料卡 supported_text 缺少: {missing}")
    return layout


def supported_text(card: dict[str, Any]) -> str:
    """「支持哪些品类」那句人话 —— 由品类表拼装, 不手写, 免得与表漂移。"""
    layout = _supported_layout(card)
    tiers = card.get("tiers")
    categories = card.get("categories")
    if not isinstance(tiers, dict) or not isinstance(categories, dict):
        raise ValueError("资料卡缺少 tiers / categories")
    item_join = str(layout["item_join"])
    parts: list[str] = []
    for tier_name, tier in tiers.items():
        if not isinstance(tier, dict):
            continue
        items: list[str] = []
        for key in tier.get("display_order") or []:
            entry = categories.get(key)
            display = entry.get("display") if isinstance(entry, dict) else None
            items.append(str(display if display else key))
        parts.append(
            str(layout["tier_tpl"]).format(
                tier=tier.get("display_name", tier_name),
                n=len(items),
                items=item_join.join(items),
            )
        )
    return str(layout["prefix"]) + str(layout["tier_join"]).join(parts)
