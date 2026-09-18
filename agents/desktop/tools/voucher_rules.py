"""voucher_rules: 「页面原文 -> 引擎规则」的**护栏**。

承 ``voucher_clues``(给线索) 与 ``saving_calc``(算钱): 中间那一步 —— 打开线索指向的页面、
把券读成规则 —— 原本是纯模型行为, **没有代码、没有判据**。而它恰好是整条链路里最容易编的
一环: 数字要从中文里抠, 有效期常被跳过, 来源一漏就无法追。

本工具把这一步收成**可校验的形状**: 调用方(模型)照着页面把看到的东西**原样**填进来,
这里负责

1. **从原文抠数**(``满100减18.8`` / ``打9.5折`` / ``立减20``), 而不是让模型自己换算;
2. **把"读不到"与"没有"分开**: 读不到 -> 进 ``missing[]``, 草稿里空着就是空着;
3. **卡住三样不能少的**: ``来源`` / ``核验于`` / ``有效期``。前两样是溯源, 第三样是**时效** ——
   有效期读不到就**不发这条规则**, 因为"不知道有效期"一旦变成默认值, 就等于"永远有效",
   而那正是把过期券说成能领的成因。

## 为什么有效期是硬闸门

``_offer_engine`` 里, 规则不带有效期 = 不设窗口 = **一直有效**。所以"没读到"绝不能顺着
管道流下去变成"一直有效"。要么读出来, 要么这条规则就停在这里并说明原因。

## 与 saving_facts 同源

两者都是"把模型整理出来的草稿校验成可执行形状", 也都守着同一条线: `MISSING` 不得被静默
填成 `false`。区别只在管的东西不同 —— 那个管**事实**, 这个管**规则**。
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

# 券类型 -> 引擎的规则类型(_offer_engine 认的那五种)
_ENGINE_TYPES = ("满减", "立减", "折扣", "比例补贴", "阶梯")

# 中文面额写法。**只认这几种通用写法**, 不猜站点特有的排版。
_MAN_JIAN_RE = re.compile(r"满\s*([0-9]+(?:\.[0-9]+)?)\s*元?\s*减\s*([0-9]+(?:\.[0-9]+)?)")
_LI_JIAN_RE = re.compile(r"立减\s*([0-9]+(?:\.[0-9]+)?)")
_ZHE_RE = re.compile(r"(?:打)?\s*([0-9]+(?:\.[0-9]+)?)\s*折")
_CAP_RE = re.compile(r"最多减\s*[￥¥]?\s*([0-9]+(?:\.[0-9]+)?)")
_THRESHOLD_RE = re.compile(r"满\s*([0-9]+(?:\.[0-9]+)?)\s*元?\s*可用")


def _err(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 2)


def _parse_original(text: str) -> dict[str, Any]:
    """从页面原文里抠出**能确定**的部分。抠不出来就什么都不给 —— 不猜。"""
    out: dict[str, Any] = {}
    if not isinstance(text, str) or not text.strip():
        return out

    man_jian = _MAN_JIAN_RE.search(text)
    if man_jian:
        out["类型"] = "满减"
        out["门槛"] = round(float(man_jian.group(1)), 2)
        out["面额"] = round(float(man_jian.group(2)), 2)
    else:
        li_jian = _LI_JIAN_RE.search(text)
        if li_jian:
            out["类型"] = "立减"
            out["面额"] = round(float(li_jian.group(1)), 2)

    zhe = _ZHE_RE.search(text)
    if zhe:
        # 「打9.5折」= 付 95%, 引擎的 `折扣` 也是"付多少"。
        rate = round(float(zhe.group(1)) / 10, 4)
        if 0 < rate <= 1:
            out["类型"] = "折扣"
            out["折扣"] = rate

    cap = _CAP_RE.search(text)
    if cap:
        out["封顶"] = round(float(cap.group(1)), 2)
    elif "门槛" not in out:
        threshold = _THRESHOLD_RE.search(text)
        if threshold:
            out["门槛"] = round(float(threshold.group(1)), 2)
    return out


def _window_ok(window: Any) -> bool:
    if not isinstance(window, dict):
        return False
    start, end = window.get("起"), window.get("止")
    if not isinstance(start, str) or not isinstance(end, str):
        return False
    try:
        date.fromisoformat(start)
        date.fromisoformat(end)
    except ValueError:
        return False
    return True


def _build_rule(
    draft: Any, index: int, errors: list[dict[str, str]], missing: list[dict[str, Any]]
) -> dict[str, Any] | None:
    path = f"drafts[{index}]"
    if not isinstance(draft, dict):
        errors.append(_err("E_DRAFT_NOT_OBJECT", path, "每条草稿必须是 object"))
        return None

    rid = str(draft.get("id") or "").strip()
    if not rid:
        errors.append(_err("E_RULE_ID_MISSING", path, "券必须有非空 id"))
        return None

    source = str(draft.get("来源") or "").strip()
    if not source:
        errors.append(_err("E_SOURCE_MISSING", f"{path}.来源", "必须给来源 URL —— 没有出处的券不许进链路"))
    verified = str(draft.get("核验于") or "").strip()
    if not verified:
        errors.append(_err("E_VERIFIED_MISSING", f"{path}.核验于", "必须给核验日期(YYYY-MM-DD)"))

    window = draft.get("有效期")
    if not _window_ok(window):
        # **硬闸门**: 不发这条规则。理由见模块 docstring —— 引擎里"没有窗口"等于"一直有效"。
        missing.append(
            {
                "id": rid,
                "why": "有效期读不到(需要 有效期.起 / 有效期.止 两个 YYYY-MM-DD); "
                "**不能当作一直有效** —— 请回到页面确认, 或明确说这张券的有效期没写出来",
            }
        )
        return None

    # 先看草稿显式给了什么; 没给的从原文抠。
    rule: dict[str, Any] = {
        "id": rid,
        "名称": str(draft.get("名称") or draft.get("原文") or rid)[:120],
        "来源": source,
        "核验于": verified,
        "有效期": {"起": window["起"], "止": window["止"]},
    }
    for key in ("类型", "门槛", "面额", "折扣", "封顶", "比例", "档位"):
        value = draft.get(key)
        if value is not None and value != "":
            rule[key] = value

    derived = _parse_original(str(draft.get("原文") or ""))
    for key, value in derived.items():
        rule.setdefault(key, value)

    for key, value in (
        ("可叠加", draft.get("可叠加")),
        ("适用品类", draft.get("适用品类")),
        ("适用城市", draft.get("适用城市")),
    ):
        if value is not None and value != "":
            rule[key] = value
    if draft.get("领取渠道"):
        rule["领取渠道"] = str(draft["领取渠道"])[:120]

    if rule.get("类型") not in _ENGINE_TYPES:
        missing.append(
            {
                "id": rid,
                "why": f"认不出券的类型(引擎认 {' / '.join(_ENGINE_TYPES)}); "
                "请把页面上的面额写法原样填进 `原文`, 或直接给 `类型` + 面额/折扣",
            }
        )
        return None
    return rule


async def voucher_rules(drafts_json: str, return_json: bool = True) -> str:
    """把「照页面抄下来的券草稿」校验成**引擎能直接算的规则**。确定性转换, 不要自己换算。

    这一步是 ``voucher_clues``(线索) 与 ``saving_calc``(算钱) 之间的桥。

    drafts_json: 草稿数组的 JSON 字符串。每张券::

        {"id": "hf-feixi-30",
         "原文": "满30减7.8元",          # 页面上的面额写法, 原样抄
         "来源": "http://...",           # 必填: 出处
         "核验于": "2026-09-18",         # 必填: 核验日期
         "有效期": {"起": "...", "止": "..."},   # 必填: 见下
         "适用品类": ["餐饮"], "适用城市": ["合肥"],
         "领取渠道": "中国银行APP", "可叠加": false}

    可选 ``类型`` / ``门槛`` / ``面额`` / ``折扣`` / ``封顶`` —— 不给就从 ``原文`` 里抠
    (``满100减18.8`` / ``打9.5折`` / ``立减20`` / ``最多减￥100``)。

    **三条硬闸门**, 缺了就不发这条规则:
    - ``来源`` 与 ``核验于``: 没有出处的券不许进链路;
    - ``有效期``: 引擎里"没有窗口"等于"**一直有效**", 所以读不到就必须停下 ——
      否则过期券会被当成能领, 而这正是本场景最贵的一类错。

    返回 ``rules``(可直接喂给 ``saving_calc`` 的 ``rules_json``) / ``missing``(读不到的,
    **带原因**) / ``errors``(形状错的)。``missing`` 里的券**不在 rules 里** —— 不是被当成
    没这条, 而是"还不能用, 缺什么已经写清楚了"; 要补齐就回页面找, **不要替它填默认值**。
    """
    try:
        drafts = json.loads(drafts_json)
    except ValueError as exc:
        return _fail("drafts_json 不是合法 JSON", detail=str(exc))
    if not isinstance(drafts, list):
        return _fail("drafts_json 必须是 JSON 数组")

    errors: list[dict[str, str]] = []
    missing: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, draft in enumerate(drafts):
        rule = _build_rule(draft, index, errors, missing)
        if rule is None:
            continue
        if rule["id"] in seen:
            errors.append(_err("E_RULE_ID_DUPLICATE", f"drafts[{index}].id", f"券 id 重复: {rule['id']!r}"))
            continue
        seen.add(rule["id"])
        rules.append(rule)

    if errors:
        return json.dumps({"ok": False, "errors": errors}, ensure_ascii=False)

    payload = {
        "ok": True,
        "rules": rules,
        "missing": missing,
        "note": (
            "`rules` 可直接作为 `saving_calc` 的 `rules_json`。"
            "`missing` 里的券**没有进 rules**: 它们不是「没有这张券」, 而是「还读不出必要信息」 —— "
            "回到页面补齐即可, **不要替它填默认值**(尤其不要替它填有效期)。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)


def _fail(reason: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "reason": reason}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)
