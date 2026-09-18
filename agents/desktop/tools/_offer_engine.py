"""通用优惠计算引擎 —— 满减 / 立减 / 折扣 / 封顶 / 门槛 / 阶梯 / 比例补贴 / 叠加。

《省钱场景交接》§4 第一条:「**一个通用计算引擎** —— 满减/折扣/立减/封顶/门槛/叠加,
只做一个引擎, 各场景只是"填规则", 不是每个场景做一个计算器」。本模块就是那个引擎:
它**不认识任何具体政策**, 只认下面这套规则形状 —— 国补、地方消费券、平台券都只是喂进来的
规则数据。

## 规则从哪来(两个来源, 同一个形状)

- **稳定的** -> 写进 ``fact-cards/``(国补参数已在卡里, 由调用方适配成规则);
- **碎片化的**(地方券 / 平台券)-> 联网查 / 读页面 / 看用户发的截图, **当场整理成同样的规则**,
  不建全量库(§4「资料卡只建稳定的」+ §7 坑 4「碎片化场景不能建全量卡」)。

两个来源进同一个引擎, 这正是"扩场景 = 加数据, 不改能力"能成立的原因。

## 规则形状

必填 ``id``(唯一) 与 ``类型``; 其余按类型给:

| 类型 | 必填 | 减额 |
|---|---|---|
| ``满减`` | ``门槛`` ``面额`` | 结算价 >= 门槛 时减 ``面额`` |
| ``立减`` | ``面额`` | 直接减 ``面额``(可另给 ``门槛``) |
| ``折扣`` | ``折扣``(付多少, 0.95 = 95 折) | ``结算价 × (1 - 折扣)``, 受 ``封顶`` 限制 |
| ``比例补贴`` | ``比例`` | ``结算价 × 比例``, 受 ``封顶`` 限制 |
| ``阶梯`` | ``档位`` | 取满足的最高档 ``[{满, 减}, ...]`` |

通用可选: ``名称`` ``适用品类`` ``适用城市`` ``有效期``(``起``/``止``) ``可叠加``
``前提`` ``来源`` ``核验于``。

``前提`` 是**任何优惠都可能有的前置条件**, 形状 ``[{字段, 在, 说明}]``: 引擎只做
"订单里这个字段的值在不在允许集合里"这一步集合判定, **归一仍留在输入层** ——
比如「1 级能效」的十几种写法由调用方先归一, 引擎只比较相等。

## 刻意不做的事

- **不认识品类枚举**: ``适用品类`` / ``适用城市`` 是自由文本, 命中就用、不命中就排除,
  不做同义归一 —— 归一属于输入层(见 ``_guobu_categories``), 引擎只做判定。
- **不假装顺序无所谓**: 叠加时先后会影响结果(折扣先还是满减先)。v1 **固定一种口径并写进
  返回的 `假设` 里**, 因为「顺序由谁定」是《交接》§5 第 3 步的开放问题 —— 现在不能偷偷定死。
- **不下"能不能用"的最终结论**: 本引擎算的是"按这份规则声明应该减多少"。券能不能真正核销,
  只有下单结算页说了算, 所以返回里固定带这条口径标签。
"""

from __future__ import annotations

# RUF001/002/003: 算式与注释里的 `×` 是**给用户看的公式文本**, 不是散文 ——
# 换成半角 x 反而读不懂。与 subsidy_calc.py 同一处理。
# ruff: noqa: RUF001, RUF002, RUF003
import json
from datetime import date
from typing import Any

_RULE_TYPES = ("满减", "立减", "折扣", "比例补贴", "阶梯")

# 叠加时的口径(见模块 docstring 第三条): v1 各条**一律按原结算价独立计算再相加**。
_COMBINE_ASSUMPTION = (
    "叠加时各条一律按原结算价独立计算再相加; 门槛也按原结算价判定。"
    "真实平台的先后顺序会影响结果, 「顺序由谁定」是《省钱场景交接》§5 第 3 步的开放问题, "
    "本引擎不擅自定死 —— 所以这条口径必须与结果一起展示。"
)

_QUOTA_LABEL = "按规则声明逐条计算; 券能否真正核销以下单结算页为准。"

# 决策实现名。这是**本体接缝**: 今天由本地规则引擎算, 本体接上后同一个入口换实现。
LOCAL_ENGINE = "local-rules"


def _err(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 2)


# --------------------------------------------------------------------------- #
# 规则校验
# --------------------------------------------------------------------------- #


def _check_rule(rule: Any, index: int, seen: set[str], errors: list[dict[str, str]]) -> None:
    path = f"rules[{index}]"
    if not isinstance(rule, dict):
        errors.append(_err("E_RULE_NOT_OBJECT", path, "每条规则必须是 object"))
        return

    rid = rule.get("id")
    if not isinstance(rid, str) or not rid.strip():
        errors.append(_err("E_RULE_ID_MISSING", path, "规则必须有非空 id"))
    elif rid in seen:
        errors.append(_err("E_RULE_ID_DUPLICATE", path, f"规则 id 重复: {rid!r}"))
    else:
        seen.add(rid)

    kind = rule.get("类型")
    if kind not in _RULE_TYPES:
        errors.append(_err("E_RULE_TYPE", f"{path}.类型", f"类型必须是 {' / '.join(_RULE_TYPES)} 之一, 实际 {kind!r}"))
        return

    if kind == "满减" and _money(rule.get("门槛")) is None:
        errors.append(_err("E_RULE_THRESHOLD", f"{path}.门槛", "满减必须给数值门槛"))
    if kind in ("满减", "立减") and _money(rule.get("面额")) is None:
        errors.append(_err("E_RULE_FACE", f"{path}.面额", f"{kind} 必须给数值面额"))
    if kind == "折扣":
        rate = _money(rule.get("折扣"))
        if rate is None or not 0 < rate <= 1:
            errors.append(_err("E_RULE_RATE", f"{path}.折扣", "折扣是「付多少」, 需 0 < 折扣 <= 1(0.95 = 95 折)"))
    if kind == "比例补贴":
        rate = _money(rule.get("比例"))
        if rate is None or not 0 < rate < 1:
            errors.append(_err("E_RULE_RATE", f"{path}.比例", "比例需 0 < 比例 < 1"))
    if kind == "阶梯":
        tiers = rule.get("档位")
        if not isinstance(tiers, list) or not tiers:
            errors.append(_err("E_RULE_TIERS", f"{path}.档位", "阶梯必须给非空档位列表"))
        else:
            for t_index, tier in enumerate(tiers):
                if not isinstance(tier, dict) or _money(tier.get("满")) is None or _money(tier.get("减")) is None:
                    errors.append(_err("E_RULE_TIER_ITEM", f"{path}.档位[{t_index}]", "每档需要数值 满 / 减"))

    window = rule.get("有效期")
    if window is not None:
        if not isinstance(window, dict):
            errors.append(_err("E_RULE_WINDOW", f"{path}.有效期", "有效期需要 {起: YYYY-MM-DD, 止: YYYY-MM-DD}"))
        else:
            for key in ("起", "止"):
                value = window.get(key)
                if value is None:
                    continue
                try:
                    date.fromisoformat(str(value))
                except ValueError:
                    errors.append(_err("E_RULE_DATE", f"{path}.有效期.{key}", f"日期需 YYYY-MM-DD, 实际 {value!r}"))

    for c_index, cond in enumerate(rule.get("前提") or []):
        cond_path = f"{path}.前提[{c_index}]"
        if not isinstance(cond, dict) or not str(cond.get("字段") or "").strip():
            errors.append(_err("E_RULE_COND", cond_path, "前提需要 {字段, 在/不高于/不低于, 说明}"))
            continue
        # 判据有两条路: 集合判定(``在``) 或数值上下界(``不高于``/``不低于``)。**不能要求每条都有 `在`**
        # —— 国补数码类的价格门槛就只有上界(结算价 ≤6000), 根本没有集合可言。
        has_membership = isinstance(cond.get("在"), list) and bool(cond["在"])
        has_bound = _money(cond.get("不高于")) is not None or _money(cond.get("不低于")) is not None
        if not has_membership and not has_bound:
            errors.append(_err("E_RULE_COND", cond_path, "前提至少要有一条判据: 「在」非空数组, 或 不高于 / 不低于"))


def _validate(rules: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(rules, list):
        return [_err("E_RULES_NOT_LIST", "rules", "rules 必须是数组")]
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        _check_rule(rule, index, seen, errors)
    return errors


# --------------------------------------------------------------------------- #
# 判定与计算
# --------------------------------------------------------------------------- #


def _norm_order(order: Any) -> dict[str, Any]:
    """归一订单。**额外字段原样带过** —— ``前提`` 要按名字读订单里的字段, 引擎不能只认四个键。"""
    src = dict(order) if isinstance(order, dict) else {}
    as_of = src.get("日期")
    if not isinstance(as_of, str) or not as_of.strip():
        as_of = date.today().isoformat()
    norm: dict[str, Any] = {k: v for k, v in src.items() if isinstance(v, (str, int, float, bool))}
    norm.update(
        {
            "结算价": _money(src.get("结算价")),
            "品类": str(src.get("品类") or "").strip(),
            "城市": str(src.get("城市") or "").strip(),
            "日期": as_of,
        }
    )
    return norm


def _eligibility(rule: dict[str, Any], order: dict[str, Any]) -> str:
    """返回不可用原因; 可用返回空串。**只判声明里写了的**, 没写的不猜。"""
    window = rule.get("有效期")
    if isinstance(window, dict):
        today = str(order["日期"])[:10]
        start, end = window.get("起"), window.get("止")
        if isinstance(end, str) and today > end[:10]:
            return f"已过期(止 {end[:10]})"
        if isinstance(start, str) and today < start[:10]:
            return f"尚未开始(起 {start[:10]})"

    categories = rule.get("适用品类")
    if isinstance(categories, list) and categories:
        want = order["品类"]
        if not want:
            return "规则限定了适用品类, 但订单没给品类"
        if not any(str(c).strip() and str(c).strip() in want for c in categories):
            return f"不适用品类 {want!r}(限 {' / '.join(str(c) for c in categories)})"

    cities = rule.get("适用城市")
    if isinstance(cities, list) and cities:
        want = order["城市"]
        if not want:
            return "规则限定了适用城市, 但订单没给城市"
        if not any(str(c).strip() and str(c).strip() in want for c in cities):
            return f"不适用城市 {want!r}(限 {' / '.join(str(c) for c in cities)})"

    price = order["结算价"]
    if price is None:
        return "订单没给结算价"
    threshold = _money(rule.get("门槛"))
    if threshold is not None and price < threshold:
        return f"未达门槛(需满 {threshold}, 当前 {price})"

    for cond in rule.get("前提") or []:
        if not isinstance(cond, dict):
            continue
        field = str(cond.get("字段") or "").strip()
        if not field:
            continue
        label = str(cond.get("说明") or field)
        actual = str(order.get(field) or "").strip()

        allowed = cond.get("在")
        if isinstance(allowed, list) and allowed and actual not in [str(a).strip() for a in allowed]:
            # 允许集合大时(比如「1 级能效」有十几种写法)不要把整个白名单倒给用户 —— 说明里
            # 已经写了人话, 罗列写法只是噪音。
            shown = " / ".join(str(a) for a in allowed) if len(allowed) <= 4 else ""
            tail = f"需 {shown}, " if shown else ""
            return f"不满足前提: {label}({tail}实际 {actual or '未提供'})"

        # 数值上下界。与 `门槛` 方向不同: 门槛是"满 X 才能用"(下界),
        # `不高于` 是"价格超过 X 就不能用"(上界) —— 国补数码类的价格门槛就是后者。
        value = order.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            ceiling = _money(cond.get("不高于"))
            if ceiling is not None and float(value) > ceiling:
                return f"不满足前提: {label}(需 {field} 不高于 {ceiling}, 实际 {value})"
            floor = _money(cond.get("不低于"))
            if floor is not None and float(value) < floor:
                return f"不满足前提: {label}(需 {field} 不低于 {floor}, 实际 {value})"
    return ""


def _amount(rule: dict[str, Any], price: float) -> tuple[float, str]:
    """算这一条的减额与算式。调用前必须已确认可用。

    **返回未取整的值**: 取整只发生在出参上。先取整再相减会与直觉差一分钱 ——
    ``subsidy_calc`` 就是拿未取整的补贴去做减法的, 两边必须一致。算式里仍然给人看取整后的数。
    """
    kind = rule["类型"]
    cap = _money(rule.get("封顶"))

    if kind == "满减":
        face = _money(rule.get("面额")) or 0.0
        return face, f"满 {_money(rule.get('门槛'))} 减 {face}"

    if kind == "立减":
        face = _money(rule.get("面额")) or 0.0
        return face, f"立减 {face}"

    if kind == "折扣":
        rate = _money(rule.get("折扣")) or 1.0
        raw = price * (1 - rate)
        if cap is not None and raw > cap:
            return cap, f"min({price} × (1 - {rate}), 封顶 {cap}) = {round(cap, 2)}"
        return raw, f"{price} × (1 - {rate}) = {round(raw, 2)}"

    if kind == "比例补贴":
        rate = _money(rule.get("比例")) or 0.0
        raw = price * rate
        if cap is not None and raw > cap:
            return cap, f"min({price} × {rate}, 封顶 {cap}) = {round(cap, 2)}"
        return raw, f"{price} × {rate} = {round(raw, 2)}"

    # 阶梯: 取满足的最高档
    tiers = [t for t in (rule.get("档位") or []) if _money(t.get("满")) is not None]
    hit = None
    for tier in sorted(tiers, key=lambda t: float(t["满"])):
        if price >= float(tier["满"]):
            hit = tier
    if hit is None:
        return 0.0, "无一档满足"
    face = _money(hit.get("减")) or 0.0
    return face, f"满 {_money(hit.get('满'))} 减 {face}"


def _plans(usable: list[dict[str, Any]], price: float) -> list[dict[str, Any]]:
    """给候选方案: 每张不可叠加的单独用, 以及"所有可叠加的一起用"。"""
    plans: list[dict[str, Any]] = []
    stackable = [u for u in usable if u["可叠加"]]
    solo = [u for u in usable if not u["可叠加"]]

    for item in solo:
        total = min(item["减"], price)
        plans.append(
            {
                "说明": f"只用 {item['名称']}",
                "用": [item["id"]],
                "共减": round(total, 2),
                "到手价": round(price - total, 2),
            }
        )

    if stackable:
        total = min(sum(u["减"] for u in stackable), price)
        plans.append(
            {
                "说明": f"叠加 {len(stackable)} 张可叠加券",
                "用": [u["id"] for u in stackable],
                "共减": round(total, 2),
                "到手价": round(price - total, 2),
            }
        )

    if not plans:
        plans.append({"说明": "没有可用优惠", "用": [], "共减": 0.0, "到手价": round(price, 2)})

    plans.sort(key=lambda p: (-p["共减"], len(p["用"])))
    return plans


def compute(order: Any, rules: Any, *, engine: str = LOCAL_ENGINE) -> dict[str, Any]:
    """按规则算「能减多少、到手多少」。

    ``engine`` 是**本体接缝**: 今天只认 ``local-rules``; 本体引擎接上后由调用方换实现,
    入参(订单 + 规则)与出参(方案 + 口径)的形状不变。
    """
    errors = _validate(rules)
    if errors:
        return {"ok": False, "engine": engine, "errors": errors}

    norm = _norm_order(order)
    price = norm["结算价"]
    if price is None:
        return {
            "ok": False,
            "engine": engine,
            "errors": [_err("E_ORDER_PRICE", "order.结算价", "订单必须给数值结算价")],
        }

    usable: list[dict[str, Any]] = []
    unusable: list[dict[str, Any]] = []
    for rule in rules:
        reason = _eligibility(rule, norm)
        name = str(rule.get("名称") or rule.get("id"))
        if reason:
            unusable.append({"id": rule.get("id"), "名称": name, "原因": reason})
            continue
        amount, formula = _amount(rule, price)
        usable.append(
            {
                "id": rule.get("id"),
                "名称": name,
                "类型": rule.get("类型"),
                "减": amount,
                "算式": formula,
                "可叠加": bool(rule.get("可叠加")),
                "来源": str(rule.get("来源") or ""),
                "核验于": str(rule.get("核验于") or ""),
            }
        )

    plans = _plans(usable, price)
    # 取整只发生在出参上(见 _amount 的说明)。排序仍按未取整的值, 免得两笔相差不到一分的
    # 减额因为先取整而调换名次。
    ranked = sorted(usable, key=lambda u: -u["减"])
    for item in ranked:
        item["减"] = round(item["减"], 2)
    return {
        "ok": True,
        "engine": engine,
        "订单": norm,
        "可用": ranked,
        "不可用": unusable,
        "方案": plans,
        "最优": plans[0],
        "口径标签": _QUOTA_LABEL,
        "假设": [_COMBINE_ASSUMPTION],
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
