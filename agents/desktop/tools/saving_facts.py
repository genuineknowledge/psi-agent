"""saving_facts v1: 省钱事实草稿 -> 事实契约 payload(校验 + 组装)。

**只做校验与组装, 不判定、不计算、不联网。** 判定与计算在本体侧(本体 §3.1 的公式
通道就是通用计算引擎), 本工具是 agent 侧的**事实供给方**。

存在的理由: 《Agent 组 -> 本体组: 运行期事实供给契约》§3 定了四态语义, 其中两条
最容易在被模型手写时丢掉, 而它们恰恰决定答案是"说错"还是"说不出":

1. **`MISSING` 不得被静默填成 `false`。** 「不知道用户有没有这张券」与「用户没有
   这张券」结论完全相反。草稿里把 `held` 留空(null)是 MISSING(进 `missing[]`),
   写 `false` 则是一个**否定断言, 必须有证据**。
2. **金额基数必须标明。** `标价` 与 `结算价` 是两个不同的量, 国补/券的补贴都按
   结算价算; 基数传错不会报错, 只会安静地算错(实测差 30 元且返回体看不出异常)。

契约的其余部分(出处 `prov`、facts/missing 互斥、金额与日期的格式)也在这里一次性
归一, 免得每个调用点各写一遍。

本工具**不认识任何政策参数**: 比例、上限、门槛值、品类枚举全部由调用方给出(将来
由资料卡/本体提供), 这里只检查它们"形状对不对"。

## 券的形状住在工具说明里

**字段清单与示例在下面 ``saving_facts`` 的 docstring 里** —— 那是模型实际读到的工具说明,
不在这里再抄一份(抄两份必然漂移)。这一节只记两条踩过的坑:

- **``id`` 不是装饰。** 契约里券按 id 引用(``entities[c1] = {"concept": "消费券"}``), 没有它
  就无法把"券面额"与"持有券"挂到同一张券上。实测跑端到端时草稿漏了它, 直接被
  ``E_COUPON_ID_MISSING`` 挡在门外 —— 所以它必须写在**工具说明**里, 不能只活在校验代码里。
- **已过期不是形状错误。** ``valid_to`` 早于 ``as_of`` 只进 ``warnings``; 过期与否是**时效
  判断**, 由本体决定怎么用, 这里不拦。会报错的只有起止顺序(``E_DATE_ORDER``)与格式。

``valid_from`` / ``valid_to`` 同时是**必读项**: 实测有一篇 ``recent``(147 天前)的文章, 里面的
券发放窗口只有 12 天、单张有效期只有 2 天 —— 早就过期了。**「文章还新」推不出「券还没过期」**,
读不到这两个日期时应当留空并说明, 不要拿文章日期替它填。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

CONTRACT = "facts/1.0"

# 金额基数。刻意只认这两个值 —— 见模块 docstring 理由 2。
PRICE_BASES = ("标价", "结算价")

# 券的可判定属性 -> 契约里的算子名。键是草稿字段, 值是事实名。
_COUPON_ATTRS = (
    ("type", "券类型"),
    ("face", "券面额"),
    ("threshold", "券门槛"),
    ("applies_to", "券适用品类"),
    ("channel", "券领取渠道"),
    ("valid_from", "券有效期起"),
    ("valid_to", "券有效期止"),
)

_MONEY_FIELDS = ("face", "threshold")


def _err(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_money(value: Any, path: str, errors: list[dict[str, str]]) -> float | None:
    if value is None or value == "":
        return None
    if not _is_number(value):
        errors.append(_err("E_MONEY_NOT_NUMBER", path, f"{path} 必须是数值, 实际是 {type(value).__name__}"))
        return None
    if value < 0:
        errors.append(_err("E_MONEY_NEGATIVE", path, f"{path} 不能为负: {value}"))
        return None
    return round(float(value), 2)


def _check_dates(start: Any, end: Any, path: str, errors: list[dict[str, str]]) -> None:
    """有效期起止必须自洽。只做日期比较 —— 「过期即失效」是本体的事, 见下 warnings。"""
    if not isinstance(start, str) or not isinstance(end, str):
        return
    try:
        if date.fromisoformat(end) < date.fromisoformat(start):
            errors.append(_err("E_DATE_ORDER", path, f"券有效期止({end}) 早于 起({start})"))
    except ValueError:
        errors.append(_err("E_DATE_FORMAT", path, f"券有效期必须是 YYYY-MM-DD: {start!r} / {end!r}"))


def _validate_coupons(coupons: Any, errors: list[dict[str, str]]) -> list[dict[str, Any]]:
    if coupons is None:
        return []
    if not isinstance(coupons, list):
        errors.append(_err("E_COUPONS_NOT_LIST", "coupons", "coupons 必须是数组"))
        return []
    seen: set[str] = set()
    for index, coupon in enumerate(coupons):
        path = f"coupons[{index}]"
        if not isinstance(coupon, dict):
            errors.append(_err("E_COUPON_NOT_OBJECT", path, "每张券必须是 object"))
            continue
        cid = coupon.get("id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append(_err("E_COUPON_ID_MISSING", path, "券必须有非空 id"))
        elif cid in seen:
            errors.append(_err("E_COUPON_ID_DUPLICATE", path, f"券 id 重复: {cid!r}"))
        else:
            seen.add(cid)
        if not isinstance(coupon.get("type"), str) or not str(coupon.get("type")).strip():
            errors.append(_err("E_COUPON_TYPE_MISSING", path, "券必须有 type(满减券/折扣券/...)"))

        for field in _MONEY_FIELDS:
            _check_money(coupon.get(field), f"{path}.{field}", errors)

        # 四态契约: false 是**否定断言**, 必须给出证据; 不知道就留 null 走 missing。
        if coupon.get("held") is False and not str(coupon.get("source") or "").strip():
            errors.append(
                _err(
                    "E_NEGATION_WITHOUT_EVIDENCE",
                    f"{path}.held",
                    "held=false 是否定断言, 必须给 source; 不确定请留 null(进 missing[], 不得填 false)",
                )
            )
        _check_dates(coupon.get("valid_from"), coupon.get("valid_to"), path, errors)
    return [c for c in coupons if isinstance(c, dict)]


def _coupon_facts(cid: str, coupon: dict[str, Any], prov: dict[str, Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for field, operator in _COUPON_ATTRS:
        value = coupon.get(field)
        if value is None or value == "":
            continue
        if field in _MONEY_FIELDS:
            value = round(float(value), 2)
        facts.append({"A": operator, "args": [cid], "val": value, "prov": dict(prov)})
    return facts


async def saving_facts(draft_json: str, return_json: bool = True) -> str:
    """把省钱事实草稿校验并组装成事实契约 payload。

    草稿由调用方(通常是模型从检索线索里整理出来的)给出, 形如:
    {"region": {"city": "合肥", "province": "安徽"},
     "product": {"category": "家电", "price": 499.0, "price_basis": "结算价"},
     "coupons": [{"id": "c1", "type": "满减券", "face": 100.0, "threshold": 500.0,
                  "applies_to": ["家电"], "valid_from": "2026-09-01", "valid_to": "2026-09-30",
                  "held": null, "source": "合肥市商务局公告"}]}

    返回契约 payload(contract/as_of/scope/entities/facts/missing/assumptions/warnings)。
    校验不通过时返回 {"ok": false, "errors": [...]}, 不产出半成品 payload。

    每张券的字段(缺一个都会被稳定错误码挡下):
    - `id` **必填且唯一** —— 契约里券按 id 引用, 没它就无法把"券面额"与"持有券"挂到同一张券上。
    - `type` **必填**(满减券 / 折扣券 / ...)。
    - `held` 留 null 表示"不知道", 进 missing[]; 写 false 则必须给 source。
    - 可选: `face` / `threshold` / `applies_to` / `channel` / `valid_from` / `valid_to`。

    另外两条:
    - `price_basis` 必填且只能是 标价 / 结算价。
    - `valid_from` / `valid_to` 是**必读项**(读不到就别填, 不要拿线索的发布日期替它填);
      已过期**不报错**, 只进 warnings —— 过期与否由本体判断。
    """
    try:
        draft = json.loads(draft_json)
    except (TypeError, ValueError) as exc:
        return json.dumps(
            {"ok": False, "contract": CONTRACT, "errors": [_err("E_BAD_JSON", "draft", f"草稿不是合法 JSON: {exc}")]},
            ensure_ascii=False,
        )
    if not isinstance(draft, dict):
        return json.dumps(
            {
                "ok": False,
                "contract": CONTRACT,
                "errors": [_err("E_NOT_OBJECT", "draft", "草稿顶层必须是 object")],
            },
            ensure_ascii=False,
        )

    errors: list[dict[str, str]] = []
    warnings: list[str] = []

    product = draft.get("product") if isinstance(draft.get("product"), dict) else {}
    region = draft.get("region") if isinstance(draft.get("region"), dict) else {}

    price = _check_money(product.get("price"), "product.price", errors)
    basis = product.get("price_basis")
    if price is not None and basis not in PRICE_BASES:
        errors.append(
            _err(
                "E_PRICE_BASIS",
                "product.price_basis",
                f"给了 price 就必须标明基数, 只能是 {' / '.join(PRICE_BASES)}; 实际 {basis!r}",
            )
        )

    coupons = _validate_coupons(draft.get("coupons"), errors)
    if errors:
        return json.dumps({"ok": False, "contract": CONTRACT, "errors": errors}, ensure_ascii=False)

    as_of = draft.get("as_of") if isinstance(draft.get("as_of"), str) and draft["as_of"].strip() else _now()
    city = str(region.get("city") or "").strip()
    province = str(region.get("province") or "").strip()

    entities: dict[str, dict[str, str]] = {"p": {"concept": "商品"}, "u": {"concept": "消费者"}}
    usr_prov: dict[str, Any] = {"src": "user", "at": as_of}
    prod_prov: dict[str, Any] = {"src": str(product.get("source") or "user"), "at": as_of}

    facts: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    category = str(product.get("category") or "").strip()
    if category:
        facts.append({"A": "商品品类", "args": ["p"], "val": category, "prov": dict(prod_prov)})
    else:
        missing.append({"A": "商品品类", "args": ["p"], "why": "未识别出商品品类, 需向用户确认"})

    if price is not None:
        facts.append(
            {
                "A": "结算价" if basis == "结算价" else "商品价格",
                "args": ["p"],
                "val": price,
                "prov": {**prod_prov, "note": f"基数={basis}"},
            }
        )
    else:
        missing.append({"A": "商品价格", "args": ["p"], "why": "未提供价格, 需向用户确认"})

    energy = str(product.get("energy_level") or "").strip()
    if energy:
        facts.append({"A": "能效等级", "args": ["p"], "val": energy, "prov": dict(prod_prov)})

    if city:
        facts.append({"A": "用户所在地", "args": ["u"], "val": city, "prov": dict(usr_prov)})
    else:
        missing.append({"A": "用户所在地", "args": ["u"], "why": "未提供所在城市; 补贴按地域分层, 需向用户确认"})

    for index, coupon in enumerate(coupons, start=1):
        cid = f"c{index}"
        entities[cid] = {"concept": "消费券"}
        coupon_prov: dict[str, Any] = {
            "src": "search" if coupon.get("source") else "user",
            "ref": coupon.get("source") or "",
            "at": str(coupon.get("verified_at") or as_of),
        }
        facts.extend(_coupon_facts(cid, coupon, coupon_prov))

        held = coupon.get("held")
        if isinstance(held, bool):
            facts.append({"A": "持有券", "args": ["u", cid], "val": held, "prov": dict(coupon_prov)})
        else:
            missing.append(
                {
                    "A": "持有券",
                    "args": ["u", cid],
                    "why": "用户未说明是否持有该券; agent 无法查询用户券包, 需向用户确认",
                }
            )

        valid_to = coupon.get("valid_to")
        if isinstance(valid_to, str) and valid_to < as_of[:10]:
            warnings.append(f"券 {cid} 有效期止 {valid_to}, 相对 as_of({as_of[:10]}) 已过期")

    payload: dict[str, Any] = {
        "ok": True,
        "contract": CONTRACT,
        "as_of": as_of,
        "scope": {"region": city or "", "base_region": province or "全国"},
        "entities": entities,
        "facts": facts,
        "missing": missing,
        "assumptions": [],
    }
    if warnings:
        payload["warnings"] = warnings
    return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)
