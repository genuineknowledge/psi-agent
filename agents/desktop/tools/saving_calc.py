"""saving_calc: 通用优惠计算 —— **一个引擎, 各场景只填规则**。

《省钱场景交接》§4 第一条:「一个通用计算引擎 —— 满减/折扣/立减/封顶/门槛/叠加, 只做一个
引擎, 各场景只是"填规则", 不是每个场景做一个计算器」。本工具是那个引擎的入口; 引擎本体在
``_offer_engine``, 它**不认识任何具体政策** —— 国补、地方消费券、平台券都只是喂进去的规则。

## 规则从哪来(两个来源, 同一个形状)

- **固定的** -> 传 ``card="guobu-2026"``, 参数从 ``fact-cards/guobu-2026.yaml`` 读出来**适配**成
  规则。这里**不再抄一份比例/上限** —— 抄两份就是《交接》§7 坑 1「参数两处重复」。
- **碎片化的**(地方券 / 平台券)-> 联网查、读页面、看用户发的截图之后, 把规则**当场整理成
  JSON** 传进 ``rules_json``。按 §4「资料卡只建稳定的」与 §7 坑 4, 这类**不建全量库**。

两个来源可以同时给, 合并后进同一个引擎 —— 这就是"扩场景 = 加数据, 不改能力"。

## 本体接缝(留着的那道口)

引擎调用点只认一个 ``engine`` 名, 今天只有 ``local-rules``。**本体引擎接上后换实现**,
而入参(订单 + 规则)与出参(可用 / 不可用 / 方案 / 口径)的形状不变。所以调用方不必知道
今天是谁在算 —— 这就是"留着本体的接口"的具体含义, 也是为什么规则要**数据化**:
本体接上后填的是同一套规则, 换的只是判定它的那台机器。

## 刻意不做的事

- **不判最终能不能用**: 本工具算的是"按这份规则声明应该减多少"。券能否真正核销,
  以下单结算页为准 —— 返回里固定带这句口径标签。
- **不猜叠加顺序**: 见返回的 ``假设``。顺序由谁定是《交接》§5 第 3 步的开放问题。
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _offer_engine as _engine
from _fact_cards import category_names, load_card, params_of, supported_text
from _guobu_categories import match_category
from subsidy_calc import _ENERGY_LEVEL_1, _norm_energy


def _fail(reason: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "reason": reason}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


async def _rules_from_card(card_name: str, order: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """把资料卡里的政策参数**适配**成引擎规则(比例补贴 + 门槛/能效前提)。

    参数只从卡里读, 这里一个比例/上限的字面量都没有 —— 与 ``subsidy_calc`` 同源。
    """
    card = await load_card(card_name)
    kind = match_category(str(order.get("品类") or ""), category_names(card))
    if kind is None:
        want = str(order.get("品类") or "")
        return [], f"资料卡 {card_name} 里没有品类 {want!r}; 卡里支持的是 {supported_text(card)}"

    pol: dict[str, Any] = params_of(card, kind)
    rule: dict[str, Any] = {
        "id": f"{card_name}:{kind}",
        "名称": str(pol.get("display") or kind),
        "类型": "比例补贴",
        "比例": float(pol["rate"]),
        "封顶": float(pol["cap"]),
        "来源": str(pol.get("source") or ""),
        "核验于": str(card.get("verified_at") or ""),
    }
    # 刻意**不写** `适用品类`: 品类归一是**输入层**的活(上面 match_category 已经做过),
    # 而引擎只做判定、不做归一。把规范名(电脑)写进规则、再让引擎拿它去比订单里的原始写法
    # (笔记本)必然对不上 —— 这是把归一的活推给了判定层。

    conds: list[dict[str, Any]] = []
    gate = pol.get("price_gate")
    if gate is not None:
        # 卡里的 price_gate 是**上限**(结算价不得超过), 与满减的「门槛」(下界)方向相反。
        conds.append(
            {
                "字段": "结算价",
                "不高于": float(gate),
                "说明": str(pol.get("gate_label") or "价格门槛"),
            }
        )
    if pol.get("energy_required"):
        conds.append(
            {
                "字段": "能效等级",
                "在": sorted(_ENERGY_LEVEL_1),
                "说明": str(pol.get("energy_label") or "能效要求"),
            }
        )
    if conds:
        rule["前提"] = conds
    return [rule], ""


def _norm_order(order: dict[str, Any]) -> dict[str, Any]:
    """输入归一(引擎不做归一): 能效写法在这里收敛成白名单里的规范写法。"""
    norm = dict(order)
    if "能效等级" in norm:
        norm["能效等级"] = _norm_energy(str(norm.get("能效等级") or ""))
    return norm


async def saving_calc(
    order_json: str,
    rules_json: str = "",
    card: str = "",
    return_json: bool = True,
) -> str:
    """按规则算「能减多少、到手多少、哪个方案最省」。确定性计算, 不要手算。

    order_json: 订单, JSON 对象字符串。至少给 ``结算价``(扣完其他优惠后的成交价);
                可选 ``品类`` / ``城市`` / ``日期``(YYYY-MM-DD, 默认今天),
                以及规则 ``前提`` 里点名的字段(如 ``能效等级``)。
    rules_json: 规则数组的 JSON 字符串。碎片化场景(地方券 / 平台券)**读页面或联网查到之后
                当场整理**成规则传进来; 不建全量库。
    card:       资料卡名(``<agent>/fact-cards/<name>.yaml``), 如 ``guobu-2026``。
                传了就把卡里的政策参数适配成规则, 与 ``rules_json`` 合并。
    return_json: 默认返回 JSON 文本。

    规则形状(必填 ``id`` / ``类型``):
    - ``满减``: ``门槛`` + ``面额``; ``立减``: ``面额``(+可选 ``门槛``)
    - ``折扣``: ``折扣``(付多少, 0.95 = 95 折) [+ ``封顶``]; ``比例补贴``: ``比例`` [+ ``封顶``]
    - ``阶梯``: ``档位`` = ``[{满, 减}, ...]``, 取满足的最高档
    通用可选: ``名称`` ``适用品类`` ``适用城市`` ``有效期``({起, 止}) ``可叠加``
    ``前提``([{字段, 在/不高于/不低于, 说明}]) ``来源`` ``核验于``。

    返回 ``可用`` / ``不可用``(带原因) / ``方案``(按共减排序) / ``最优`` / ``口径标签`` /
    ``假设``。**``不可用`` 里每条都写了为什么**, 直接可以用来向用户解释。

    两条不要越界的地方: ① 本工具算的是"按规则声明应该减多少", **能不能真正核销以下单结算页
    为准**; ② 叠加顺序是开放问题, 见返回的 ``假设``, 不要把它当成已知事实。
    """
    try:
        order_raw = json.loads(order_json) if order_json.strip() else {}
    except ValueError as exc:
        return _fail("order_json 不是合法 JSON", detail=str(exc))
    if not isinstance(order_raw, dict):
        return _fail("order_json 必须是 JSON 对象")

    rules: list[dict[str, Any]] = []
    notes: list[str] = []

    if card.strip():
        try:
            card_rules, why = await _rules_from_card(card.strip(), order_raw)
        except (OSError, ValueError, KeyError) as exc:
            return _fail(f"资料卡 {card!r} 读不出来", detail=str(exc))
        if why:
            return _fail(why, card=card.strip())
        rules.extend(card_rules)

    if rules_json.strip():
        try:
            extra = json.loads(rules_json)
        except ValueError as exc:
            return _fail("rules_json 不是合法 JSON", detail=str(exc))
        if not isinstance(extra, list):
            return _fail("rules_json 必须是 JSON 数组")
        rules.extend(extra)

    if not rules:
        return _fail("没有规则可算: 给 card, 或把读到的券整理成 rules_json 传进来")

    payload = _engine.compute(_norm_order(order_raw), rules)
    if not payload.get("ok"):
        return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)

    payload["规则来源"] = {"card": card.strip() or None, "条数": len(rules)}
    if notes:
        payload["假设"] = [*payload.get("假设", []), *notes]
    return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)
