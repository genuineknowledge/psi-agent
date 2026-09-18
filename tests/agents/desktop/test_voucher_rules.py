# ruff: noqa: RUF001, RUF002  # 下面这些是**页面原文**, 全角标点是数据 —— 改了就对不上真实文案。
"""`voucher_rules` 的回归判据 —— 「页面原文 -> 引擎规则」那道护栏。

这一环原本是纯模型行为, 没有代码也没有判据, 而它恰好是整条链路里最容易编的一环。
判据按重要性排:

1. **有效期是硬闸门**。引擎里"规则不带有效期" = "不设窗口" = **一直有效**。所以"没读到"
   绝不能顺着管道流成"一直有效" —— 那正是把过期券说成能领的成因。读不到就不发这条规则。
2. **没有出处不许进链路**。来源 + 核验日期, 缺一即拒。
3. **数字从原文抠, 不让模型自己换算**。`满100减18.8` / `打9.5折` / `最多减￥100`。
   抠不出来就进 `missing`, **不猜**。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from agents.desktop.tools import voucher_rules as _voucher_rules
else:
    import voucher_rules as _voucher_rules

_URL = "http://m.hf.bendibao.com/live/104831.shtm"


def _draft(**over: Any) -> dict[str, Any]:
    base = {
        "id": "hf-feixi-30",
        "原文": "满30减7.8元",
        "来源": _URL,
        "核验于": "2026-09-18",
        "有效期": {"起": "2026-04-24", "止": "2026-05-05"},
    }
    base.update(over)
    return base


async def _call(*drafts: dict[str, Any]) -> dict[str, Any]:
    return json.loads(await _voucher_rules.voucher_rules(drafts_json=json.dumps(list(drafts), ensure_ascii=False)))


# --------------------------------------------------------------------------- #
# 1. 有效期是硬闸门
# --------------------------------------------------------------------------- #


async def test_a_rule_without_a_validity_window_is_not_emitted() -> None:
    """**最重要的一条**: 读不到有效期就不发规则 —— 否则它在引擎里等于"一直有效"。"""
    draft = _draft()
    draft.pop("有效期")

    out = await _call(draft)
    assert out["ok"] is True
    assert out["rules"] == [], "没有有效期的券不许进 rules"
    assert out["missing"][0]["id"] == "hf-feixi-30"
    assert "不能当作一直有效" in out["missing"][0]["why"]


@pytest.mark.parametrize("window", [None, {}, {"起": "2026-04-24"}, {"止": "2026-05-05"}, {"起": "x", "止": "y"}])
async def test_a_partial_or_malformed_window_is_also_refused(window: Any) -> None:
    out = await _call(_draft(有效期=window))
    assert out["rules"] == []
    assert out["missing"]


async def test_a_missing_window_does_not_take_the_other_rules_down_with_it() -> None:
    """一张券读不出有效期, 不该连累同一批里别的券。"""
    bad = _draft(id="bad")
    bad.pop("有效期")
    out = await _call(bad, _draft(id="good"))
    assert [r["id"] for r in out["rules"]] == ["good"]
    assert [m["id"] for m in out["missing"]] == ["bad"]


# --------------------------------------------------------------------------- #
# 2. 没有出处不许进链路
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("field", "code"),
    [("来源", "E_SOURCE_MISSING"), ("核验于", "E_VERIFIED_MISSING")],
)
async def test_provenance_is_mandatory(field: str, code: str) -> None:
    draft = _draft()
    draft.pop(field)
    out = await _call(draft)
    assert out["ok"] is False
    assert code in [e["code"] for e in out["errors"]]


async def test_an_id_is_mandatory() -> None:
    draft = _draft()
    draft.pop("id")
    out = await _call(draft)
    assert "E_RULE_ID_MISSING" in [e["code"] for e in out["errors"]]


async def test_duplicate_ids_are_refused() -> None:
    out = await _call(_draft(), _draft())
    assert "E_RULE_ID_DUPLICATE" in [e["code"] for e in out["errors"]]


# --------------------------------------------------------------------------- #
# 3. 数字从原文抠
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("满30减7.8元", {"类型": "满减", "门槛": 30.0, "面额": 7.8}),
        ("满100减18.8元", {"类型": "满减", "门槛": 100.0, "面额": 18.8}),
        ("立减20", {"类型": "立减", "面额": 20.0}),
    ],
)
async def test_amounts_are_parsed_from_the_page_text(original: str, expected: dict[str, Any]) -> None:
    out = await _call(_draft(原文=original))
    rule = out["rules"][0]
    for key, value in expected.items():
        assert rule[key] == value, (original, key, rule)


async def test_a_discount_is_converted_to_what_you_pay() -> None:
    """「打9.5折」= 付 95%; 引擎的 `折扣` 也是"付多少", 两边口径必须对上。"""
    out = await _call(_draft(原文="打9.5折"))
    assert out["rules"][0]["类型"] == "折扣"
    assert out["rules"][0]["折扣"] == 0.95


async def test_a_cap_inside_the_threshold_text_is_lifted_out() -> None:
    """折扣券的封顶混在门槛文案里(实测 `满1可用，最多减￥100`) —— 要提成独立字段。"""
    out = await _call(_draft(原文="打6折，满180可用，最多减￥400"))
    rule = out["rules"][0]
    assert rule["折扣"] == 0.6
    assert rule["封顶"] == 400.0


async def test_explicit_values_win_over_the_parsed_ones() -> None:
    out = await _call(_draft(原文="满30减7.8元", 门槛=50, 面额=10))
    assert out["rules"][0]["门槛"] == 50
    assert out["rules"][0]["面额"] == 10


async def test_an_unrecognisable_amount_goes_to_missing_rather_than_being_guessed() -> None:
    out = await _call(_draft(原文="具体以页面为准"))
    assert out["rules"] == []
    assert "类型" in out["missing"][0]["why"]


# --------------------------------------------------------------------------- #
# 4. 形状与接缝
# --------------------------------------------------------------------------- #


async def test_engine_fields_are_carried_through() -> None:
    out = await _call(_draft(适用品类=["餐饮"], 适用城市=["合肥"], 可叠加=False, 领取渠道="中国银行APP"))
    rule = out["rules"][0]
    assert rule["适用品类"] == ["餐饮"]
    assert rule["适用城市"] == ["合肥"]
    assert rule["可叠加"] is False
    assert rule["领取渠道"] == "中国银行APP"
    assert rule["来源"] == _URL
    assert rule["核验于"] == "2026-09-18"


async def test_bad_json_is_rejected_before_anything_else() -> None:
    assert (json.loads(await _voucher_rules.voucher_rules(drafts_json="{not json")))["ok"] is False
    assert (json.loads(await _voucher_rules.voucher_rules(drafts_json='{"a": 1}')))["ok"] is False


async def test_the_note_warns_against_filling_defaults() -> None:
    out = await _call(_draft(原文="具体以页面为准"))
    assert "不要替它填默认值" in out["note"]
