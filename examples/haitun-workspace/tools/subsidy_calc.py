# ruff: noqa: RUF001, RUF002
"""subsidy_calc v1.3：确定性补贴计算（精确计算，模型不手算）。

输入：结算价（扣平台优惠后的成交价）/ 品类 / 能效等级（家电类必传）/ region（可选）
输出：资格 / 补贴金额 / 到手价 / 公式 / 口径标签 / 额度假设

参数：2026 国补口径（事实卡），可按省细则微调：
- 数码类（手机/平板/智能手表手环/智能眼镜）：15%，单件上限 500 元，结算价 ≤6000 元
- 家电类（电脑/笔记本/台式机/一体机/游戏本/空调/冰箱/洗衣机/电视/热水器）：
  15%，单件上限 1500 元，需 1 级能效/水效（能效必传，白名单精确匹配）
- v1.1：品类与 policy_query 对齐；v1.2：能效必传 + >6000 地方补贴提示；
- v1.3（2026-08-26，review #1/#2/#3/#4）：品类匹配改共享 _guobu_categories（电视柜/空调扇
  不误判）；能效白名单精确匹配（「不是1级」「1.5匹」不放行）；region 可选并返回口径声明；
  返回额度假设 assumption。
"""
import json

from _guobu_categories import is_digital, is_home, match_category, supported_text

_ENERGY_LEVEL_1 = {"1", "1级", "一级", "1级能效", "一级能效", "1级水效", "一级水效",
                   "1级能耗", "一级能耗", "1级标准", "一级标准", "国标一级", "一级（能效）"}


def _norm_energy(energy_level: str) -> str:
    s = (energy_level or "").strip().replace(" ", "").replace("：", "").replace(":", "").replace("=", "")
    for p in ("能效等级", "能效标准", "能耗等级", "国标能效", "能效"):
        if s.startswith(p):
            s = s[len(p):].lstrip(":：= ")
            break
    return s


async def subsidy_calc(
    price: float,
    category: str = "手机",
    energy_level: str = "",
    region: str = "",
    return_json: bool = True,
) -> str:
    """确定性计算补贴与到手价。price=结算价（扣优惠后）；category=品类；region=省份（可选）。"""
    cat = (category or "").strip()
    price = float(price)
    kind = match_category(cat)

    if kind is None:
        return json.dumps({
            "ok": False,
            "reason": (f"未知品类：{cat}（支持 {supported_text()}；"
                       f"电视柜/空调扇/手机壳等非国补品类不算）"),
            "suggest_search": True,
            "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
        }, ensure_ascii=False)

    if is_home(kind):
        pct, cap, gate = 0.15, 1500.0, None
        if not energy_level:
            return json.dumps({
                "ok": False, "reason": "家电需 1 级能效/水效，未提供能效等级，无法确认是否符合 2026 国补条件",
                "need_energy_level": True,
                "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
            }, ensure_ascii=False)
        if _norm_energy(energy_level) not in _ENERGY_LEVEL_1:
            return json.dumps({
                "ok": False,
                "reason": (f"家电需 1 级能效/水效，当前能效为 {energy_level}，不符合 2026 国补条件"
                           f"（仅认 1级/一级 等白名单写法）"),
                "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
            }, ensure_ascii=False)
        kind_label = "家电（以旧换新类）"
    elif is_digital(kind):
        pct, cap, gate = 0.15, 500.0, 6000.0
        if price > gate:
            return json.dumps({
                "ok": False,
                "reason": (f"数码类单件结算价 ≤6000 元，当前 {round(price, 2)} 超门槛，不参与 2026 国补；"
                           "部分省市有 >6000 高端机地方补贴（如 10%、上限 1000，山东/江苏等，安徽未见官方文件），"
                           "需按所在省细则/结算页核实，不得断言『完全无补贴』"),
                "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
            }, ensure_ascii=False)
        kind_label = "数码（数码智能产品类）"
    else:
        return json.dumps({
            "ok": False, "reason": f"未知品类：{cat}",
            "suggest_search": True,
            "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
        }, ensure_ascii=False)

    subsidy = min(price * pct, cap)
    final_price = price - subsidy
    result = {
        "ok": True,
        "category": kind_label,
        "kind": kind,
        "结算价": round(price, 2),
        "补贴比例": "15%",
        "单件上限": cap,
        "补贴": round(subsidy, 2),
        "到手价": round(final_price, 2),
        "公式": f"补贴 = min(结算价 × 15%, 上限 {cap}) = min({round(price, 2)} × 0.15, {cap}) = {round(subsidy, 2)}",
        "region": region or "未指定",
        "region_basis": (f"按全国通用口径估算（{region}）；省细则可能不同，以下单结算页为准")
                        if region else "未指定省份：按全国通用口径估算，省细则可能不同，以下单结算页为准",
        "assumption": "假定本年度该品类补贴额度尚未使用（每人每类限 1 件）；若用户可能已用额度，需先确认再计算",
        "口径标签": "2026 现行（政策参数非实时，以下单结算页为准）",
        "note": "结算价按扣完平台券/会员/店铺优惠后的成交价传入；省份资格另行确认（eligibility_check）。",
    }
    return json.dumps(result, ensure_ascii=False) if return_json else str(result)
