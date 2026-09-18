"""`saving_facts` 的回归判据 —— 事实契约在 agent 侧的可执行版本。

这个工具不判定政策、不算钱, 它只保证**喂给本体的事实形状是对的**。因此判据分两层:

1. **结构性校验**按契约返回稳定错误码, 不产出半成品 payload;
2. **契约不变量**在**每个**成功 payload 上都成立 —— 用一条遍历断言钉死, 而不是靠
   逐个用例记得检查。其中最关键的是「`MISSING` 不得被静默填成 `false`」: 「不知道
   用户有没有这张券」与「用户没有这张券」结论完全相反, 一旦填错, 整套推理会给出
   一个看起来合理的错答案。
"""

from __future__ import annotations

import json

# 运行期与 ty 走不同分支: 运行时靠同级 conftest 把 tools 目录挂上 sys.path(裸名导入),
# ty 不认那个插入, 只能按包路径解析。与 test_fusion_memory_tools.py 同套写法。
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from agents.desktop.tools import saving_facts as _saving_facts
else:
    import saving_facts as _saving_facts

# 一份"全都有"的草稿: 后面各用例只改其中一处。
FULL_DRAFT: dict[str, Any] = {
    "as_of": "2026-09-17T15:30:00+08:00",
    "region": {"city": "合肥", "province": "安徽"},
    "product": {"category": "家电", "price": 499.0, "price_basis": "结算价"},
    "coupons": [
        {
            "id": "c1",
            "type": "满减券",
            "face": 100.0,
            "threshold": 500.0,
            "applies_to": ["家电"],
            "channel": "云闪付",
            "valid_from": "2026-09-01",
            "valid_to": "2026-09-30",
            "held": True,
            "source": "合肥市商务局公告",
        }
    ],
}


def _draft(**overrides: Any) -> str:
    draft = json.loads(json.dumps(FULL_DRAFT))  # 深拷贝, 免得用例之间互相污染
    draft.update(overrides)
    return json.dumps(draft, ensure_ascii=False)


async def _call(draft_json: str) -> dict[str, Any]:
    return json.loads(await _saving_facts.saving_facts(draft_json))


def _assert_contract_invariants(payload: dict[str, Any]) -> None:
    """每条成功 payload 都必须满足的契约不变量。"""
    assert payload["contract"] == "facts/1.0"
    assert payload["ok"] is True

    # 1. facts 与 missing 互斥: 同一个 (算子, 实参) 不得两边都出现
    def keys(items: list[dict[str, Any]]) -> set[tuple[str, tuple[str, ...]]]:
        return {(item["A"], tuple(item["args"])) for item in items}

    both = keys(payload["facts"]) & keys(payload["missing"])
    assert not both, f"facts 与 missing 重叠: {both}"

    # 2. 每条事实都带出处, 且 val 绝不是 null(缺失要走 missing, 不许用 null 占位)
    for fact in payload["facts"]:
        assert fact.get("prov"), f"事实缺 prov: {fact}"
        assert fact["val"] is not None, f"事实 val 为 null, 应改走 missing: {fact}"

    # 3. 事实引用的每个实体都必须声明过
    declared = set(payload["entities"])
    for fact in payload["facts"]:
        for arg in fact["args"]:
            assert arg in declared, f"事实引用了未声明的实体 {arg!r}: {fact}"

    # 4. missing 必须说明为什么缺失
    for item in payload["missing"]:
        assert item.get("why"), f"missing 没写原因: {item}"

    # 5. **MISSING 不得被静默填成 false** —— 这条是本工具存在的首要理由
    for item in payload["missing"]:
        assert item["A"] != "持有券" or not any(f["A"] == "持有券" and f["val"] is False for f in payload["facts"]), (
            "缺失的持有券被静默写成了 false"
        )


# --------------------------------------------------------------------------- #
# 契约不变量
# --------------------------------------------------------------------------- #


async def test_full_draft_assembles_a_contract_payload() -> None:
    payload = await _call(_draft())
    _assert_contract_invariants(payload)

    assert payload["as_of"] == "2026-09-17T15:30:00+08:00"
    assert payload["scope"] == {"region": "合肥", "base_region": "安徽"}
    assert payload["entities"]["p"] == {"concept": "商品"}
    assert payload["entities"]["u"] == {"concept": "消费者"}
    assert payload["entities"]["c1"] == {"concept": "消费券"}
    assert payload["missing"] == []
    assert payload["assumptions"] == []

    facts = {(f["A"], tuple(f["args"])): f["val"] for f in payload["facts"]}
    assert facts[("商品品类", ("p",))] == "家电"
    assert facts[("结算价", ("p",))] == 499.0  # 基数是结算价 -> 算子就叫结算价
    assert facts[("用户所在地", ("u",))] == "合肥"
    assert facts[("券面额", ("c1",))] == 100.0
    assert facts[("券门槛", ("c1",))] == 500.0
    assert facts[("券类型", ("c1",))] == "满减券"
    assert facts[("券适用品类", ("c1",))] == ["家电"]
    assert facts[("券有效期止", ("c1",))] == "2026-09-30"
    assert facts[("持有券", ("u", "c1"))] is True


async def test_unknown_holding_becomes_missing_not_false() -> None:
    """`held: null` = 不知道 -> 进 missing; 绝不能被填成 false。"""
    draft = json.loads(_draft())
    draft["coupons"][0]["held"] = None

    payload = await _call(json.dumps(draft, ensure_ascii=False))
    _assert_contract_invariants(payload)

    assert ("持有券", ("u", "c1")) in {(m["A"], tuple(m["args"])) for m in payload["missing"]}
    assert not [f for f in payload["facts"] if f["A"] == "持有券"]
    why = next(m["why"] for m in payload["missing"] if m["A"] == "持有券")
    assert "券包" in why  # 说明 agent 拿不到, 而不是含糊带过


async def test_negative_assertion_without_evidence_is_rejected() -> None:
    """`held: false` 是否定断言, 没出处就 fail —— 这正是"把不知道写成没有"。"""
    draft = json.loads(_draft())
    draft["coupons"][0]["held"] = False
    draft["coupons"][0].pop("source")

    payload = await _call(json.dumps(draft, ensure_ascii=False))
    assert payload["ok"] is False
    assert [e["code"] for e in payload["errors"]] == ["E_NEGATION_WITHOUT_EVIDENCE"]


async def test_negative_assertion_with_evidence_is_accepted() -> None:
    draft = json.loads(_draft())
    draft["coupons"][0]["held"] = False  # 仍留有 source

    payload = await _call(json.dumps(draft, ensure_ascii=False))
    _assert_contract_invariants(payload)
    facts = {(f["A"], tuple(f["args"])): f["val"] for f in payload["facts"]}
    assert facts[("持有券", ("u", "c1"))] is False


async def test_every_success_path_holds_the_invariants() -> None:
    """换几种真实形态跑一遍, 不变量在每条成功路径上都成立。"""
    variants: list[dict[str, Any]] = []

    minimal = {"product": {"category": "手机", "price": 4000.0, "price_basis": "标价"}}
    variants.append(minimal)

    no_coupons = dict(FULL_DRAFT)
    no_coupons["coupons"] = []
    variants.append(no_coupons)

    unknown_hold = json.loads(_draft())
    unknown_hold["coupons"][0]["held"] = None
    variants.append(unknown_hold)

    expired = json.loads(_draft())
    expired["coupons"][0]["valid_from"] = "2026-08-01"
    expired["coupons"][0]["valid_to"] = "2026-08-31"
    variants.append(expired)

    for draft in variants:
        payload = await _call(json.dumps(draft, ensure_ascii=False))
        _assert_contract_invariants(payload)


# --------------------------------------------------------------------------- #
# 金额基数: 失败样例 6 的教训, 在 A2 这条路上先挡住
# --------------------------------------------------------------------------- #


async def test_price_without_basis_is_rejected() -> None:
    """标价与结算价是两个量; 基数不标就不给过 —— 传错不会报错, 只会安静算错。"""
    draft = json.loads(_draft())
    draft["product"].pop("price_basis")

    payload = await _call(json.dumps(draft, ensure_ascii=False))
    assert payload["ok"] is False
    assert [e["code"] for e in payload["errors"]] == ["E_PRICE_BASIS"]
    assert "结算价" in payload["errors"][0]["message"]


async def test_basis_picks_the_operator_name() -> None:
    """基数不是备注, 它决定算子: 标价 -> 商品价格, 结算价 -> 结算价。"""
    listed = json.loads(_draft())
    listed["product"]["price_basis"] = "标价"
    payload = await _call(json.dumps(listed, ensure_ascii=False))
    operators = {f["A"] for f in payload["facts"]}
    assert "商品价格" in operators
    assert "结算价" not in operators

    settled = await _call(_draft())
    operators = {f["A"] for f in settled["facts"]}
    assert "结算价" in operators
    assert "商品价格" not in operators


async def test_basis_note_survives_in_provenance() -> None:
    payload = await _call(_draft())
    fact = next(f for f in payload["facts"] if f["A"] == "结算价")
    assert "结算价" in fact["prov"].get("note", "")


# --------------------------------------------------------------------------- #
# 缺失事实: 进 missing 并说明原因, 而不是悄悄少一条
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mutate", "operator", "keyword"),
    [
        (lambda d: d.pop("region"), "用户所在地", "地域分层"),
        (lambda d: d["product"].pop("category"), "商品品类", "品类"),
        (lambda d: d["product"].pop("price"), "商品价格", "价格"),
    ],
)
async def test_missing_facts_are_declared_with_a_reason(mutate: Any, operator: str, keyword: str) -> None:
    draft = json.loads(_draft())
    mutate(draft)

    payload = await _call(json.dumps(draft, ensure_ascii=False))
    _assert_contract_invariants(payload)
    item = next(m for m in payload["missing"] if m["A"] == operator)
    assert keyword in item["why"]
    assert not [f for f in payload["facts"] if f["A"] == operator]


# --------------------------------------------------------------------------- #
# 结构性校验: 稳定错误码 + 不产出半成品
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("draft_json", "code"),
    [
        ("{not json", "E_BAD_JSON"),
        ("[1, 2, 3]", "E_NOT_OBJECT"),
        ('{"coupons": "c1"}', "E_COUPONS_NOT_LIST"),
        ('{"coupons": [{"type": "满减券"}]}', "E_COUPON_ID_MISSING"),
        ('{"coupons": [{"id": "a"}]}', "E_COUPON_TYPE_MISSING"),
        ('{"coupons": [{"id": "a", "type": "满减券"}, {"id": "a", "type": "折扣券"}]}', "E_COUPON_ID_DUPLICATE"),
        ('{"coupons": [{"id": "a", "type": "满减券", "face": -1}]}', "E_MONEY_NEGATIVE"),
        ('{"coupons": [{"id": "a", "type": "满减券", "face": "一百"}]}', "E_MONEY_NOT_NUMBER"),
        (
            '{"coupons": [{"id": "a", "type": "满减券", "valid_from": "2026-09-30", "valid_to": "2026-09-01"}]}',
            "E_DATE_ORDER",
        ),
    ],
)
async def test_structural_errors_return_stable_codes(draft_json: str, code: str) -> None:
    payload = await _call(draft_json)
    assert payload["ok"] is False
    assert code in [e["code"] for e in payload["errors"]]
    assert "facts" not in payload  # 失败不产出半成品


async def test_errors_carry_a_path_so_the_caller_can_locate_them() -> None:
    draft = json.loads(_draft())
    draft["coupons"][0]["face"] = -5
    payload = await _call(json.dumps(draft, ensure_ascii=False))
    assert payload["errors"][0]["path"] == "coupons[0].face"


# --------------------------------------------------------------------------- #
# 有效期: 只报事实(日期比较), 不替本体判定"失效"
# --------------------------------------------------------------------------- #


async def test_expired_coupon_is_reported_as_a_warning_not_a_verdict() -> None:
    draft = json.loads(_draft())
    draft["coupons"][0]["valid_from"] = "2026-08-01"
    draft["coupons"][0]["valid_to"] = "2026-08-31"  # 起止自洽, 但整体早于 as_of

    payload = await _call(json.dumps(draft, ensure_ascii=False))
    _assert_contract_invariants(payload)
    assert any("已过期" in w for w in payload.get("warnings", []))
    # 事实照旧如实带上有效期, 判定留给本体
    facts = {f["A"]: f["val"] for f in payload["facts"]}
    assert facts["券有效期止"] == "2026-08-31"


async def test_as_of_defaults_to_now_when_the_draft_omits_it() -> None:
    draft = json.loads(_draft())
    draft.pop("as_of")
    payload = await _call(json.dumps(draft, ensure_ascii=False))
    _assert_contract_invariants(payload)
    assert payload["as_of"][:2] == "20"  # ISO 8601 且带年
    assert "T" in payload["as_of"]


# --------------------------------------------------------------------------- #
# 工具说明就是契约: 模型读的是 docstring, 不是校验代码
# --------------------------------------------------------------------------- #


def test_the_docstring_documents_every_coupon_field() -> None:
    """券字段加了一个却忘了写进 docstring, 每个调用点就会再踩一次。

    这条不是形式主义。实测跑端到端时, 草稿就是照着 docstring 写的, 结果漏了 `id`,
    被 `E_COUPON_ID_MISSING` 挡在门外 —— 而那个要求当时只存在于校验代码里,
    docstring 一个字都没提。
    """
    doc = _saving_facts.saving_facts.__doc__ or ""

    for name in ("id", "type", "held"):
        assert name in doc, f"docstring 没提到必填字段 {name}"

    for field, _operator in _saving_facts._COUPON_ATTRS:
        assert field in doc, f"docstring 没提到券字段 {field}(_COUPON_ATTRS 里有)"


def test_the_docstring_says_expiry_is_a_warning_not_an_error() -> None:
    """过期是**时效判断**, 不是形状错误。

    这条不写清, 调用方会以为"过期"会被拦下来、于是自己先拦一道 —— 而按契约它应当
    原样进 payload, 由本体决定怎么用。``valid_from`` / ``valid_to`` 同时是**必读项**:
    实测有一篇 recent(147 天前)的文章, 里面的券只有 2 天有效期。
    """
    doc = _saving_facts.saving_facts.__doc__ or ""
    assert "warnings" in doc
    assert "valid_from" in doc and "valid_to" in doc
    assert "必读项" in doc
