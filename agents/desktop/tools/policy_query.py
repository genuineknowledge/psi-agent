"""policy_query v1.4: 政策参数查询(结构化政策参数 + 口径标签 + 文号/来源 + 2025 对照 + 时效)。

定位: 解决 B1(2025 口径 vs 2026)、T01(来源 URL)、T24(文件内容混淆)等
- 返回 2026 现行政策参数(结构化), 含来源、文号与日期;
- 口径标签: 明确「2026 现行」, 并给 2025 旧口径对照;
- v1.1: 补全 2026 国补品类(家电 6 类 + 数码 4 类); 未知品类返回 suggest_search;
- v1.2: 配件词排除;
- v1.3(2026-08-26, review #1/#5): 品类匹配改共享 _guobu_categories(白名单+收尾匹配,
  电视柜/空调扇/平板电脑 不再误判); 返回带 fact_card_version / verified_at / expires_at,
  是否过期由模型/提示词判断(超过 expires_at 须检索最新官方);
- v1.4(本次): **政策参数全部移出代码**, 改从资料卡 ``fact-cards/guobu-2026.yaml`` 读
  (``_fact_cards.load_card``)。本文件不再持有任何比例 / 上限 / 门槛 / 品类清单的字面量,
  与 ``subsidy_calc`` 读同一张卡 —— 消掉「两处各写一份, 改一处必改另一处」(交接文档 §7 坑 1)。
- 确定性程序: 不联网、不实时检索, 回答必须标注「政策口径以官方文件/结算页为准」。
"""

import json

from _fact_cards import category_names, load_card, params_of, supported_text
from _guobu_categories import match_category

# 资料卡名(``<agent>/fact-cards/<name>.yaml``)。改政策改那张卡, 不改本文件。
_CARD = "guobu-2026"


async def policy_query(subject: str = "", region: str = "", return_json: bool = True) -> str:
    """查询 2026 政策参数(结构化 + 口径标签 + 2025 对照 + 时效); subject=品类, region=省份(当前为占位)。"""
    card = await load_card(_CARD)
    notes = card["notes"]
    labels = card["labels"]
    supported = supported_text(card)
    key = match_category(subject, category_names(card))
    if not key:
        return json.dumps(
            {
                "ok": False,
                "reason": str(notes["unknown_category_tpl"]).format(category=subject, supported=supported),
                "quota_label": labels["current"],
                "suggest_search": True,
                "hint": notes["policy_hint"],
                "fact_card_version": card["version"],
                "verified_at": card["verified_at"],
                "expires_at": card["expires_at"],
            },
            ensure_ascii=False,
        )

    pol = params_of(card, key)
    result = {
        "ok": True,
        "query": {"subject": subject, "region": region or notes["region_none"]},
        "policy": {
            "年份": card["year"],
            "口径标签": labels["policy"],
            "品类": pol["category_label"],
            "补贴比例": pol["rate_label"],
            "单件上限": pol["cap_label"],
            "能效要求": pol["energy_label"],
            "价格门槛": pol["gate_label"],
            "件数": pol["quota_label"],
            "来源": pol["source"],
            "印发/实施": card["issued"],
            "2025旧口径": card["previous_year"],
        },
        "fact_card_version": card["version"],
        "verified_at": card["verified_at"],
        "expires_at": card["expires_at"],
        "note": str(notes["policy_note"]).format(expires_at=card["expires_at"]),
    }
    return json.dumps(result, ensure_ascii=False) if return_json else str(result)
