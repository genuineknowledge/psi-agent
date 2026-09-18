"""国补工具的**已知失败**探测器 —— 把《国补当前失败样例》变成可执行的。

## 为什么不直接写测试

直接给这三个工具写测试, 会把 **bug 固化下来**: 「`一级能效/水效` 被判不符合国补条件」
会变成一个**通过的用例**, 从此谁也不敢改。所以分两半:

- **正确的行为** -> 普通用例(本文的「边界守卫」与 `test_guobu_fact_card.py`);
- **已知错误的行为** -> `xfail(strict=True)`, 写明它是哪一条失败、修好后要做什么。

`strict=True` 是刻意的: 这些用例**一旦开始通过**, pytest 记成 `XPASS` 并让整个测试
**失败** —— 这逼人来摘掉标记, 而不是让标记烂在原地。所以:

> **看到 XPASS 不要改断言, 那是「这一条修好了」的信号。**

## 覆盖

| 编号 | 失败 | 性质 |
|---|---|---|
| 1 | 能效: 工具用来陈述要求的措辞, 正是它拒绝的措辞 | 假否定 |
| 2 | 品类: `iPad` 被判未知品类, 与手机壳并列 | 假否定 + 措辞误导 |
| 3 | 额度: 用户明说买过了, 工具无入参, 仍断言"假定未使用" | 结构性缺口 |
| 4 | 数量: 「买两台」无法表达 | 结构性缺口 |
| 5 | region: 四个不同省份返回逐字节相同 | 能力缺口 |
| 6 | 基数: 传错结算价, 回显把错值标成「结算价」 | 静默错误 |

外加一条已记录但未编号的缺口(品类源覆盖), 见文末。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

if TYPE_CHECKING:
    from agents.desktop.tools import _guobu_categories, policy_query, review_search, subsidy_calc
else:
    import _guobu_categories
    import policy_query
    import review_search
    import subsidy_calc

WORKSPACE_ROOT = Path(__file__).resolve().parents[3] / "agents" / "desktop"
CARD_PATH = WORKSPACE_ROOT / "fact-cards" / "guobu-2026.yaml"


def _card() -> dict[str, Any]:
    return yaml.safe_load(CARD_PATH.read_text(encoding="utf-8"))


def _names() -> tuple[str, ...]:
    return tuple(_card()["categories"])


def _params_of(func: Any) -> list[str]:
    return sorted(inspect.signature(func).parameters)


# =========================================================================== #
# 失败 1 · 能效: 工具用来陈述要求的措辞, 正是它拒绝的措辞
# =========================================================================== #


@pytest.mark.xfail(strict=True, reason="失败样例 1: 政策原文措辞『一级能效/水效』被判不符合国补条件")
async def test_policy_phrasing_from_the_card_itself_is_accepted() -> None:
    """卡里的 `energy_label` 就是「1 级能效/水效」—— 照它说, 不该被判不符合。

    修法: 值空间由本体给出权威字面量(或换成能接受政策措辞的归一化), 然后摘掉标记。
    """
    payload = json.loads(await subsidy_calc.subsidy_calc(price=3000, category="冰箱", energy_level="一级能效/水效"))
    assert payload["ok"] is True


@pytest.mark.xfail(strict=True, reason="失败样例 1: 能效同义写法被白名单外判为不符合(9 个合法写法里 7 个被拒)")
@pytest.mark.parametrize("level", ["一级能效等级", "国家一级能效", "1级(能效)", "一级能效或水效"])
async def test_energy_synonyms_are_accepted(level: str) -> None:
    """同义写法 -> 应归一成 1 级。

    与上一条同源: 白名单是有限枚举, 自然语言的说法空间是开放的 —— 加词只是把爆点往后推。
    """
    payload = json.loads(await subsidy_calc.subsidy_calc(price=3000, category="冰箱", energy_level=level))
    assert payload["ok"] is True


@pytest.mark.xfail(strict=True, reason="失败样例 1: 把『认不出』表达成『不符合』—— MISSING 被静默填成 false")
async def test_unrecognized_energy_does_not_claim_ineligibility() -> None:
    """**失败方向**才是要害: `ok:false` 本身没错, 错在 `reason` 给的是一个**确定的否定结论**,
    而工具实际知道的只是"这个写法我不认识"。契约里 `MISSING` 与 `false` 必须分开
    (`agent-to-ontology-facts.md` §3)。
    """
    payload = json.loads(await subsidy_calc.subsidy_calc(price=3000, category="冰箱", energy_level="一级能效/水效"))
    assert "不符合" not in payload["reason"]


async def test_energy_whitelist_still_accepts_the_plain_spellings() -> None:
    """边界守卫: 白名单里的写法**确实**通过 —— 免得摘 xfail 时改坏它们。"""
    for level in ("一级", "1级", "1级能效", "一级能耗", "国标一级"):
        payload = json.loads(await subsidy_calc.subsidy_calc(price=3000, category="冰箱", energy_level=level))
        assert payload["ok"] is True, level


# =========================================================================== #
# 失败 2 · 品类: iPad 被判未知品类
# =========================================================================== #


@pytest.mark.xfail(strict=True, reason="失败样例 2: match_category 大小写敏感(iPad 失败 / ipad 成功)")
@pytest.mark.parametrize("token", ["iPad", "iPhone 17", "MacBook Pro"])
def test_brand_names_normalize_to_a_category(token: str) -> None:
    """品牌名 -> 品类。

    这是 agent 侧**可独立修**的(ALIASES 归 agent, 契约文档 §6.1)。本次刻意不修: 值空间
    归谁裁决还没定, 先修会把那个问题盖住。
    """
    assert _guobu_categories.match_category(token, _names()) is not None


@pytest.mark.xfail(strict=True, reason="失败样例 2: iPad(真实国补品类)与手机壳并列, 措辞读起来像『不属于国补』")
async def test_unknown_category_message_does_not_list_a_real_category_with_accessories() -> None:
    """真实品类不该和「手机壳/电视柜」并列 —— 那句措辞本身在给用户一个错的政策结论。"""
    payload = json.loads(await subsidy_calc.subsidy_calc(price=4999, category="iPad"))
    assert "手机壳等非国补品类不算" not in payload["reason"]


def test_accessory_words_are_still_rejected() -> None:
    """边界守卫: 组合词/配件**确实**不命中 —— 这是设计意图, 别在修上面时改坏。"""
    for token in ("电视柜", "空调扇", "手机壳", "洗衣机罩"):
        assert _guobu_categories.match_category(token, _names()) is None, token


def test_plain_aliases_still_normalize() -> None:
    """边界守卫: 别名表里那些写法是对的。"""
    for token, expected in (("笔记本", "电脑"), ("游戏本", "电脑"), ("平板电脑", "平板"), ("电冰箱", "冰箱")):
        assert _guobu_categories.match_category(token, _names()) == expected, token


# =========================================================================== #
# 失败 3 · 额度: 用户明说买过了, 工具照样算
# =========================================================================== #


@pytest.mark.xfail(strict=True, reason="失败样例 3: 签名没有承接『额度已用』的位置, 返回却断言『假定未使用』")
async def test_capacity_already_used_can_be_expressed() -> None:
    """「今年买过了」必须能被表达。

    修法是**本体侧**把「每人每类限 1 件」建成规则并声明它需要什么事实; agent 侧的参数
    只是那个事实的入口 —— **不是** agent 自己加个布尔就完事。
    """
    assert any("quota" in n or "used" in n for n in _params_of(subsidy_calc.subsidy_calc)), _params_of(
        subsidy_calc.subsidy_calc
    )


async def test_assumption_is_at_least_declared() -> None:
    """边界守卫: 今天至少把假设**写出来了**(而不是藏起来) —— 这点别丢。"""
    payload = json.loads(await subsidy_calc.subsidy_calc(price=3000, category="冰箱", energy_level="一级"))
    assert "额度" in payload["assumption"]


# =========================================================================== #
# 失败 4 · 数量: 买两台无法表达
# =========================================================================== #


@pytest.mark.xfail(strict=True, reason="失败样例 4: 无数量入参; 第二台应为 0 补贴, 工具给不出任何信号")
async def test_quantity_can_be_expressed() -> None:
    """「买两台」必须能被表达。

    与失败 3 同源: 一旦「限 1 件」进规则, `优惠金额` 的输入面就暴露了 —— 要么需要
    「数量」, 要么必须明确它是**单件口径**并在概念上标注。由本体定义输入面之后决定。
    """
    assert any("quantity" in n or "count" in n for n in _params_of(subsidy_calc.subsidy_calc)), _params_of(
        subsidy_calc.subsidy_calc
    )


# =========================================================================== #
# 失败 5 · region: 四个省份返回逐字节相同
# =========================================================================== #


@pytest.mark.xfail(strict=True, reason="失败样例 5: region 只被回显, policy 里无任何地域字段")
async def test_region_reaches_the_policy_payload() -> None:
    """不同省份的 **policy 内容**应不同(至少标出实际适用的是哪一级细则)。

    刻意只比 `policy` 子字典: `query.region` 今天确实回显了, 比整个输出会**假通过**。
    """
    shandong = json.loads(await policy_query.policy_query(subject="手机", region="山东"))["policy"]
    jiangsu = json.loads(await policy_query.policy_query(subject="手机", region="江苏"))["policy"]
    assert shandong != jiangsu


@pytest.mark.xfail(strict=True, reason="失败样例 5: 山东 >6000 的地方增量规则未建模, 一律按全国门槛挡掉")
async def test_local_subsidy_above_the_gate_can_be_answered() -> None:
    """山东 >6000 高端机有地方补贴 —— 工具应能算, 而不是笼统挡掉。

    要本体 §3.6 的地域分层(`rule.region` + `scope_region`/`base_region`)落地。
    """
    payload = json.loads(await subsidy_calc.subsidy_calc(price=6100, category="手机", region="山东"))
    assert payload["ok"] is True


async def test_region_is_at_least_echoed_back() -> None:
    """边界守卫: 至少今天 region 被回显了、免责话术也写了 —— 这点别丢。"""
    payload = json.loads(
        await subsidy_calc.subsidy_calc(price=3000, category="冰箱", energy_level="一级", region="江苏")
    )
    assert payload["region"] == "江苏"
    assert "江苏" in payload["region_basis"]


# =========================================================================== #
# 失败 6 · 基数: 传错结算价, 回显把错值标成「结算价」
# =========================================================================== #


@pytest.mark.xfail(strict=True, reason="失败样例 6: 签名是 price: float 接收一切, 传错基数无任何提示")
async def test_price_basis_is_declared_in_the_input_surface() -> None:
    """基数必须标明, 否则传错不报错、只会安静算错。

    `saving_facts`(#981)已强制 `price_basis`; 这里要的是 `subsidy_calc` 这一侧同样不能
    对基数沉默(本体侧要建 `标价` / `结算价` 两个概念)。
    """
    assert any("basis" in n for n in _params_of(subsidy_calc.subsidy_calc)), _params_of(subsidy_calc.subsidy_calc)


async def test_echoed_price_is_at_least_labelled() -> None:
    """边界守卫: 返回到手价与公式都在, 口径标签也在 —— 这些是对的。"""
    payload = json.loads(await subsidy_calc.subsidy_calc(price=4099, category="空调", energy_level="一级"))
    assert payload["结算价"] == 4099.0
    assert "口径标签" in payload
    assert "公式" in payload


# =========================================================================== #
# 已记录但未编号的缺口: review_search 的品类源只覆盖 3/10 国补品类
# =========================================================================== #


@pytest.mark.xfail(strict=True, reason="已记录缺口(架构文档 §七 #7): 7 个国补品类无垂直源")
def test_review_search_covers_every_guobu_category() -> None:
    """导购检索对 10 个国补品类都该有垂直源。

    今天只有 笔记本/电脑/游戏本/手机/平板/耳机 六个 key —— 其中「耳机」**不在国补枚举里**;
    而手表/眼镜/空调/冰箱/洗衣机/电视/热水器这 7 个只能走 bing/ddg 降级。偏偏国补家电一侧
    的上限更高(1500 vs 500), 是更值得给候选的一侧。
    """
    covered = {key for key in _names() if review_search._category_sources(key)}
    assert covered == set(_names()), sorted(set(_names()) - covered)


def test_review_search_helpers_still_behave() -> None:
    """边界守卫: 纯函数部分是对的, 别在补源时改坏。

    刻意不测网络路径 —— 工具的价值在「只返回真实抓取到的」, 那由运行时保证。
    """
    assert review_search._category_sources("笔记本")
    assert review_search._category_sources("空调") == []

    query = review_search._build_query("笔记本", 5000, 6000, "轻薄", "江苏")
    assert "笔记本" in query
    assert "5000" in query
    assert "江苏" in query

    kept = review_search._light_filter([{"title": "某笔记本评测"}, {"title": "中关村在线报价大全"}])
    assert [a["title"] for a in kept] == ["某笔记本评测"]
