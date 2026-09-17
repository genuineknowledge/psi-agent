"""subsidy_calc v1.4: 确定性补贴计算(精确计算, 模型不手算)。

输入: 结算价(扣平台优惠后的成交价) / 品类 / 能效等级(家电类必传) / region(可选)
输出: 资格 / 补贴金额 / 到手价 / 公式 / 口径标签 / 额度假设

参数: v1.4 起**全部来自资料卡** ``fact-cards/guobu-2026.yaml``(与 ``policy_query``
同一张卡) —— 本文件不再持有任何比例 / 上限 / 门槛的字面量。档位不再是 if/elif 的
家电/数码分支, 而是卡里的三个字段:

- ``rate`` / ``cap``: 比例与单件上限;
- ``price_gate``: 结算价门槛, ``null`` 表示不设门槛;
- ``energy_required``: 是否要求 1 级能效/水效。

这样加档位只改卡, 不加代码分支(对齐《省钱决策 Workspace 方案》§4「不要为每个场景
各写一个计算器」)。

版本:
- v1.1: 品类与 policy_query 对齐; v1.2: 能效必传 + 超门槛地方补贴提示;
- v1.3(2026-08-26, review #1/#2/#3/#4): 品类匹配改共享 _guobu_categories(电视柜/空调扇
  不误判); 能效白名单精确匹配(「不是1级」「1.5匹」不放行); region 可选并返回口径声明;
  返回额度假设 assumption;
- v1.4(本次): 参数移出代码改读资料卡(交接文档 §7 坑 1「两处各写一份」)。
"""

# RUF001: 下面两处是**数据**, 不是散文 —— 白名单要逐字匹配用户/页面上的全角写法
# (「一级(能效)」), 归一化要剥掉用户打的全角「:」。改成半角就匹配不上了。
# ruff: noqa: RUF001

import json
from typing import Any

from _fact_cards import category_names, load_card, params_of, supported_text
from _guobu_categories import match_category

# 资料卡名(``<agent>/fact-cards/<name>.yaml``)。改政策改那张卡, 不改本文件。
_CARD = "guobu-2026"

# 能效白名单: 把用户/页面上的写法归一后**精确**匹配。
# 刻意留在代码里而不是资料卡 —— 它是输入归一化(「一级」「国标一级」是同一个意思的
# 不同说法), 不是政策参数; 政策参数只有「要求 1 级」这一个事实(档位 energy_required)。
_ENERGY_LEVEL_1 = {
    "1",
    "1级",
    "一级",
    "1级能效",
    "一级能效",
    "1级水效",
    "一级水效",
    "1级能耗",
    "一级能耗",
    "1级标准",
    "一级标准",
    "国标一级",
    "一级（能效）",
}


def _norm_energy(energy_level: str) -> str:
    s = (energy_level or "").strip().replace(" ", "").replace("：", "").replace(":", "").replace("=", "")
    for p in ("能效等级", "能效标准", "能耗等级", "国标能效", "能效"):
        if s.startswith(p):
            s = s[len(p) :].lstrip(":：= ")
            break
    return s


async def subsidy_calc(
    price: float,
    category: str = "手机",
    energy_level: str = "",
    region: str = "",
    return_json: bool = True,
) -> str:
    """确定性计算补贴与到手价。price=结算价(扣优惠后); category=品类; region=省份(可选)。"""
    card = await load_card(_CARD)
    notes = card["notes"]
    labels = card["labels"]
    cat = (category or "").strip()
    price = float(price)
    kind = match_category(cat, category_names(card))

    if kind is None:
        return json.dumps(
            {
                "ok": False,
                "reason": str(notes["unknown_category_tpl"]).format(category=cat, supported=supported_text(card)),
                "suggest_search": True,
                "subsidy": 0,
                "final_price": round(price, 2),
                "quota_label": labels["current"],
            },
            ensure_ascii=False,
        )

    # 品类已登记 -> params_of 必然给全参数; 卡里档位写错会抛, 那是配置问题不是输入问题。
    pol: dict[str, Any] = params_of(card, kind)
    pct = float(pol["rate"])
    cap = float(pol["cap"])
    gate = pol["price_gate"]
    needs_energy = bool(pol["energy_required"])

    if needs_energy and not energy_level:
        return json.dumps(
            {
                "ok": False,
                "reason": notes["need_energy_reason"],
                "need_energy_level": True,
                "subsidy": 0,
                "final_price": round(price, 2),
                "quota_label": labels["current"],
            },
            ensure_ascii=False,
        )
    if needs_energy and _norm_energy(energy_level) not in _ENERGY_LEVEL_1:
        return json.dumps(
            {
                "ok": False,
                "reason": str(notes["bad_energy_tpl"]).format(energy_level=energy_level),
                "subsidy": 0,
                "final_price": round(price, 2),
                "quota_label": labels["current"],
            },
            ensure_ascii=False,
        )
    if gate is not None and price > float(gate):
        return json.dumps(
            {
                "ok": False,
                "reason": str(notes["over_gate_tpl"]).format(price=round(price, 2), hint=notes["over_gate_hint"]),
                "subsidy": 0,
                "final_price": round(price, 2),
                "quota_label": labels["current"],
            },
            ensure_ascii=False,
        )

    subsidy = min(price * pct, cap)
    final_price = price - subsidy
    rate_label = pol["rate_label"]
    result = {
        "ok": True,
        "category": pol["tier_label"],
        "kind": kind,
        "结算价": round(price, 2),
        "补贴比例": rate_label,
        "单件上限": cap,
        "补贴": round(subsidy, 2),
        "到手价": round(final_price, 2),
        "公式": (
            f"补贴 = min(结算价 × {rate_label}, 上限 {cap}) = "
            f"min({round(price, 2)} × {pct}, {cap}) = {round(subsidy, 2)}"
        ),
        "region": region or notes["region_none"],
        "region_basis": str(notes["region_basis_tpl"]).format(region=region) if region else notes["region_basis_none"],
        "assumption": notes["quota_assumption"],
        "口径标签": labels["quota"],
        "note": notes["eligibility_note"],
    }
    return json.dumps(result, ensure_ascii=False) if return_json else str(result)
