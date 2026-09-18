"""`_offer_engine` / `saving_calc` 的回归判据 —— 通用优惠计算引擎。

《省钱场景交接》§4 第一条要求「**只做一个引擎, 各场景只是"填规则"**」。所以本文件的
第一组判据不是"引擎能算", 而是**引擎算出来的东西与已经上线、有 golden 数据集的
`subsidy_calc` 完全一致** —— 那才证明它真的通用, 而不是又一个场景专用计算器。

其余三组:

- **规则从哪来都得进同一个引擎**: 固定参数从资料卡适配(`card=`), 碎片化参数由调用方
  整理成 JSON(`rules_json=`)。两条路都走过同一个 `compute`。
- **判不准就说判不准**: 每条不可用**必须带原因**, 且原因里要有人看得懂的话 ——
  这份返回是直接拿去跟用户解释的, 空原因等于让模型自己编。
- **本体接缝**: 引擎名走一个字段(`engine`), 出入参形状与本体接缝无关。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from agents.desktop.tools import _offer_engine
    from agents.desktop.tools import saving_calc as _saving_calc
    from agents.desktop.tools import subsidy_calc as _subsidy_calc
else:
    import _offer_engine
    import saving_calc as _saving_calc
    import subsidy_calc as _subsidy_calc


async def _calc(**kwargs: Any) -> dict[str, Any]:
    return json.loads(await _saving_calc.saving_calc(**kwargs))


def _order(**over: Any) -> str:
    base = {"结算价": 200, "品类": "餐饮", "城市": "合肥", "日期": "2026-04-30"}
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


# 端到端实跑时从原文读到的肥西五一餐饮券三档。
FEIXI = [
    {"id": "f30", "名称": "满30减7.8", "类型": "满减", "门槛": 30, "面额": 7.8},
    {"id": "f100", "名称": "满100减18.8", "类型": "满减", "门槛": 100, "面额": 18.8},
    {"id": "f200", "名称": "满200减28.8", "类型": "满减", "门槛": 200, "面额": 28.8},
]


def _rules(*rules: dict[str, Any]) -> str:
    return json.dumps(list(rules), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 1. 通用性的证明: 与已上线的 subsidy_calc 逐例对齐
# --------------------------------------------------------------------------- #

#: (结算价, 品类, 能效) —— 覆盖家电/数码两档, 以及门槛边界两侧。
_GUOBU_CASES = [
    (3000, "空调", "一级"),
    (12000, "空调", "一级"),
    (299.5, "冰箱", "一级"),
    (5000, "手机", "一级"),
    (6000, "平板", "一级"),
    (7000, "手机", "一级"),
    (1500, "笔记本", "一级"),
]


@pytest.mark.parametrize(("price", "category", "energy"), _GUOBU_CASES)
async def test_engine_reproduces_subsidy_calc(price: float, category: str, energy: str) -> None:
    """**本文件最重要的一条**: 引擎必须复现已上线工具的每一个数。

    国补只是喂给引擎的一份规则(比例补贴 + 价格上界 + 能效前提), 所以两边必须逐例相等
    —— 相等才说明"一个引擎各场景填规则"不是口号。
    """
    official = json.loads(
        await _subsidy_calc.subsidy_calc(price=price, category=category, energy_level=energy, region="安徽")
    )
    mine = await _calc(order_json=_order(结算价=price, 品类=category, 能效等级=energy), card="guobu-2026")

    assert bool(official["ok"]) is bool(mine["可用"]), f"{category} {price}: 结论不一致 {official} / {mine}"
    if not official["ok"]:
        # 口径不同: subsidy_calc 用 ok 表达整体结论, 而引擎**刻意不整体失败** ——
        # 它把这条规则判为不可用并说清原因(见下一个用例)。
        assert mine["不可用"], "被挡了却没给原因"
        assert mine["最优"]["共减"] == 0
        return
    assert abs(float(official["补贴"]) - float(mine["最优"]["共减"])) < 0.005, (
        f"{category} {price}: 补贴不一致 {official['补贴']} vs {mine['最优']['共减']}"
    )
    assert abs(float(official["到手价"]) - float(mine["最优"]["到手价"])) < 0.005, (
        f"{category} {price}: 到手价不一致 {official['到手价']} vs {mine['最优']['到手价']}"
    )


@pytest.mark.parametrize(("price", "category", "energy"), _GUOBU_CASES)
async def test_a_rejected_case_still_returns_a_structured_conclusion(price: float, category: str, energy: str) -> None:
    """被挡时不能整体失败 —— 要给出「不可用 + 原因」, 否则模型只能自己编一句解释。"""
    official = json.loads(await _subsidy_calc.subsidy_calc(price=price, category=category, energy_level=energy))
    mine = await _calc(order_json=_order(结算价=price, 品类=category, 能效等级=energy), card="guobu-2026")

    assert mine["ok"] is True, "引擎应当永远给得出结构化结论"
    if official["ok"]:
        assert not mine["不可用"]
        return
    assert mine["不可用"] and mine["不可用"][0]["原因"]
    assert mine["最优"]["共减"] == 0


async def test_energy_shortfall_is_reported_with_a_readable_reason() -> None:
    mine = await _calc(order_json=_order(结算价=3000, 品类="空调", 能效等级="2级"), card="guobu-2026")
    reason = mine["不可用"][0]["原因"]
    assert "不满足前提" in reason
    assert "2级" in reason
    # 白名单有十几种写法, 不该整个倒给用户
    assert reason.count("/") <= 4, reason


async def test_a_category_the_card_does_not_know_fails_before_computing() -> None:
    mine = await _calc(order_json=_order(结算价=3000, 品类="电视柜"), card="guobu-2026")
    assert mine["ok"] is False
    assert "电视柜" in mine["reason"]


# --------------------------------------------------------------------------- #
# 2. 两种规则来源进同一个引擎
# --------------------------------------------------------------------------- #


async def test_extracted_rules_go_through_the_same_engine() -> None:
    """碎片化场景(地方券)的规则由调用方当场整理成 JSON —— 与资料卡走同一条路。"""
    mine = await _calc(order_json=_order(结算价=200), rules_json=json.dumps(FEIXI, ensure_ascii=False))
    assert mine["ok"] is True
    assert mine["engine"] == _offer_engine.LOCAL_ENGINE
    assert mine["最优"]["用"] == ["f200"]
    assert mine["最优"]["共减"] == 28.8
    assert mine["最优"]["到手价"] == 171.2


async def test_card_and_inline_rules_merge() -> None:
    mine = await _calc(
        order_json=_order(结算价=3000, 品类="空调", 能效等级="一级"),
        card="guobu-2026",
        rules_json=_rules({"id": "shop", "类型": "立减", "面额": 50}),
    )
    assert {u["id"] for u in mine["可用"]} == {"guobu-2026:空调", "shop"}


async def test_no_rules_at_all_is_a_loud_failure() -> None:
    mine = await _calc(order_json=_order())
    assert mine["ok"] is False
    assert "没有规则" in mine["reason"]


# --------------------------------------------------------------------------- #
# 3. 判定: 每条不可用都必须说清为什么
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("rule", "needle"),
    [
        ({"id": "a", "类型": "满减", "门槛": 500, "面额": 50}, "未达门槛"),
        (
            {"id": "b", "类型": "满减", "门槛": 1, "面额": 5, "有效期": {"起": "2026-01-01", "止": "2026-01-31"}},
            "已过期",
        ),
        (
            {"id": "c", "类型": "满减", "门槛": 1, "面额": 5, "有效期": {"起": "2026-12-01", "止": "2026-12-31"}},
            "尚未开始",
        ),
        ({"id": "d", "类型": "满减", "门槛": 1, "面额": 5, "适用品类": ["汽车"]}, "不适用品类"),
        ({"id": "e", "类型": "满减", "门槛": 1, "面额": 5, "适用城市": ["北京"]}, "不适用城市"),
        (
            {"id": "f", "类型": "满减", "门槛": 1, "面额": 5, "前提": [{"字段": "结算价", "不高于": 100}]},
            "不高于",
        ),
    ],
)
async def test_every_rejection_says_why(rule: dict[str, Any], needle: str) -> None:
    mine = await _calc(order_json=_order(结算价=200), rules_json=_rules(rule))
    assert mine["可用"] == []
    assert len(mine["不可用"]) == 1
    reason = mine["不可用"][0]["原因"]
    assert needle in reason, reason
    assert mine["不可用"][0]["id"] == rule["id"]


async def test_an_order_missing_a_field_a_rule_needs_is_rejected_not_guessed() -> None:
    """规则限定了品类, 订单却没给品类 —— 不能默认通过。"""
    mine = await _calc(
        order_json=json.dumps({"结算价": 200}, ensure_ascii=False),
        rules_json=_rules({"id": "a", "类型": "满减", "门槛": 1, "面额": 5, "适用品类": ["餐饮"]}),
    )
    assert "没给品类" in mine["不可用"][0]["原因"]


# --------------------------------------------------------------------------- #
# 4. 算钱: 五种形态
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ({"id": "a", "类型": "满减", "门槛": 100, "面额": 20}, 20.0),
        ({"id": "b", "类型": "立减", "面额": 15}, 15.0),
        ({"id": "c", "类型": "折扣", "折扣": 0.9}, 20.0),  # 200 打 9 折, 减 20
        ({"id": "d", "类型": "折扣", "折扣": 0.5, "封顶": 60}, 60.0),
        ({"id": "e", "类型": "比例补贴", "比例": 0.15}, 30.0),
        ({"id": "f", "类型": "比例补贴", "比例": 0.15, "封顶": 25}, 25.0),
        ({"id": "g", "类型": "阶梯", "档位": [{"满": 100, "减": 10}, {"满": 200, "减": 45}]}, 45.0),
        ({"id": "h", "类型": "阶梯", "档位": [{"满": 100, "减": 10}, {"满": 500, "减": 99}]}, 10.0),
    ],
)
async def test_amounts_per_rule_type(rule: dict[str, Any], expected: float) -> None:
    mine = await _calc(order_json=_order(结算价=200), rules_json=_rules(rule))
    assert mine["可用"][0]["减"] == expected, mine["可用"][0]
    assert "算式" in mine["可用"][0]


async def test_a_discount_never_drives_the_price_below_zero() -> None:
    mine = await _calc(
        order_json=_order(结算价=10),
        rules_json=_rules({"id": "a", "类型": "立减", "面额": 50}),
    )
    assert mine["最优"]["共减"] == 10.0
    assert mine["最优"]["到手价"] == 0.0


# --------------------------------------------------------------------------- #
# 5. 方案: 不可叠加只能单选, 可叠加才合并
# --------------------------------------------------------------------------- #


async def test_non_stackable_coupons_are_offered_one_at_a_time() -> None:
    mine = await _calc(order_json=_order(结算价=200), rules_json=json.dumps(FEIXI, ensure_ascii=False))
    for plan in mine["方案"]:
        assert len(plan["用"]) <= 1, plan
    assert mine["方案"][0]["共减"] == 28.8


async def test_stackable_coupons_are_offered_together() -> None:
    mine = await _calc(
        order_json=_order(结算价=200),
        rules_json=_rules(
            {"id": "a", "类型": "满减", "门槛": 100, "面额": 20, "可叠加": True},
            {"id": "b", "类型": "立减", "面额": 10, "可叠加": True},
        ),
    )
    assert mine["最优"]["用"] == ["a", "b"]
    assert mine["最优"]["共减"] == 30.0


async def test_no_usable_coupon_is_a_clean_zero_plan() -> None:
    mine = await _calc(
        order_json=_order(结算价=10),
        rules_json=_rules({"id": "a", "类型": "满减", "门槛": 500, "面额": 50}),
    )
    assert mine["最优"] == {"说明": "没有可用优惠", "用": [], "共减": 0.0, "到手价": 10.0}


# --------------------------------------------------------------------------- #
# 6. 结构与接缝
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("rules", "code"),
    [
        ("[]", None),
        (json.dumps([{"类型": "满减", "门槛": 1, "面额": 1}]), "E_RULE_ID_MISSING"),
        (
            json.dumps([{"id": "a", "类型": "满减", "门槛": 1, "面额": 1}, {"id": "a", "类型": "立减", "面额": 1}]),
            "E_RULE_ID_DUPLICATE",
        ),
        (json.dumps([{"id": "a", "类型": "说不清", "面额": 1}]), "E_RULE_TYPE"),
        (json.dumps([{"id": "a", "类型": "满减", "门槛": 1}]), "E_RULE_FACE"),
        (json.dumps([{"id": "a", "类型": "折扣", "折扣": 95}]), "E_RULE_RATE"),
        (json.dumps([{"id": "a", "类型": "阶梯", "档位": []}]), "E_RULE_TIERS"),
        (
            json.dumps([{"id": "a", "类型": "满减", "门槛": 1, "面额": 1, "有效期": {"起": "2026-13-99"}}]),
            "E_RULE_DATE",
        ),
        (json.dumps([{"id": "a", "类型": "满减", "门槛": 1, "面额": 1, "前提": [{"字段": "x"}]}]), "E_RULE_COND"),
    ],
)
async def test_bad_rule_shapes_return_stable_codes(rules: str, code: str | None) -> None:
    mine = await _calc(order_json=_order(), rules_json=rules)
    if code is None:
        assert mine["ok"] is False  # 空规则数组: 走到"没有规则可算"
        return
    assert mine["ok"] is False
    assert code in [e["code"] for e in mine["errors"]], mine["errors"]


async def test_every_success_payload_carries_the_quota_label_and_the_ordering_caveat() -> None:
    """口径标签与"叠加顺序未定"这两句必须在**每个**成功返回里 —— 它们是这份结果的边界。"""
    mine = await _calc(order_json=_order(结算价=200), rules_json=json.dumps(FEIXI, ensure_ascii=False))
    assert "结算页" in mine["口径标签"]
    assert mine["假设"] and "顺序" in mine["假设"][0]
    assert mine["规则来源"]["card"] is None


async def test_the_engine_seam_is_visible_in_the_payload() -> None:
    """**本体接缝**: 谁在算这件事写在返回里, 接上本体后这一个字段会变, 形状不变。"""
    mine = await _calc(order_json=_order(结算价=200), rules_json=json.dumps(FEIXI, ensure_ascii=False))
    assert mine["engine"] == "local-rules"


async def test_bad_json_is_rejected_before_touching_anything() -> None:
    assert (await _calc(order_json="{not json"))["ok"] is False
    assert (await _calc(order_json=_order(), rules_json="{not json"))["ok"] is False
    assert (await _calc(order_json=_order(), rules_json='{"a": 1}'))["ok"] is False  # 不是数组
