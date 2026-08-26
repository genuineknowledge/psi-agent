# -*- coding: utf-8 -*-
"""subsidy_calc v1.2：确定性补贴计算（精确计算，模型不手算）。

输入：结算价（扣平台优惠后的成交价）/ 品类 / 能效等级（家电类必传）
输出：资格 / 补贴金额 / 到手价 / 公式 / 口径标签

参数：2026 国补口径（事实卡），可按省细则微调：
- 数码类（手机/平板/智能手表手环/智能眼镜）：15%，单件上限 500 元，结算价 ≤6000 元
- 家电类（电脑/笔记本/台式机/一体机/游戏本/空调/冰箱/洗衣机/电视/热水器）：
  15%，单件上限 1500 元，需 1 级能效/水效（能效必传，未提供视为不符合）
- 计算顺序：扣优惠后结算价 → 补贴 = min(结算价 × 15%, 上限)
- v1.1（2026-08-26）：品类与 policy_query 对齐（补家电 6 类 + 游戏本）
- v1.2（2026-08-26）：家电能效改必传校验（漏传不再放行）；数码 >6000 返回提示地方补贴边界
"""
import json
from typing import Optional

_DIGITAL_CATS = ("手机", "平板", "智能手表", "手表", "手环", "智能眼镜", "眼镜")
_HOME_CATS = ("电脑", "笔记本", "台式机", "一体机", "游戏本",
              "空调", "冰箱", "洗衣机", "电视", "热水器")


async def subsidy_calc(
    price: float,
    category: str = "手机",
    energy_level: str = "",
    return_json: bool = True,
) -> str:
    """确定性计算补贴与到手价。price=结算价（扣优惠后）。"""
    cat = category.strip()
    price = float(price)

    # 品类判断
    if any(k in cat for k in _HOME_CATS):
        kind = "家电（以旧换新类）"
        pct, cap, gate = 0.15, 1500.0, None
        # 家电需 1 级能效/水效（必传校验：未提供或非 1 级均视为不符合）
        if not energy_level:
            reason = "家电需 1 级能效/水效，未提供能效等级，无法确认是否符合 2026 国补条件"
            return json.dumps({
                "ok": False, "reason": reason, "need_energy_level": True,
                "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
            }, ensure_ascii=False)
        if "1" not in energy_level and "一级" not in energy_level:
            return json.dumps({
                "ok": False, "reason": "家电需 1 级能效/水效，当前能效为 %s，不符合 2026 国补条件" % energy_level,
                "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
            }, ensure_ascii=False)
    elif any(k in cat for k in _DIGITAL_CATS):
        kind = "数码（数码智能产品类）"
        pct, cap, gate = 0.15, 500.0, 6000.0
        if price > gate:
            return json.dumps({
                "ok": False,
                "reason": ("数码类单件结算价 ≤6000 元，当前 {p} 超门槛，不参与 2026 国补；"
                           "部分省市有 >6000 高端机地方补贴（如 10%、上限 1000，山东/江苏等，安徽未见官方文件），"
                           "需按所在省细则/结算页核实，不得断言『完全无补贴』").format(p=round(price, 2)),
                "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
            }, ensure_ascii=False)
    else:
        return json.dumps({
            "ok": False, "reason": "未知品类：%s（支持 手机/平板/手表/眼镜 或 家电类：电脑/笔记本/游戏本/空调/冰箱/洗衣机/电视/热水器）" % cat,
            "subsidy": 0, "final_price": round(price, 2), "quota_label": "2026 现行",
        }, ensure_ascii=False)

    subsidy = min(price * pct, cap)
    final_price = price - subsidy
    result = {
        "ok": True,
        "category": kind,
        "结算价": round(price, 2),
        "补贴比例": "15%",
        "单件上限": cap,
        "补贴": round(subsidy, 2),
        "到手价": round(final_price, 2),
        "公式": "补贴 = min(结算价 × 15%, 上限 {cap}) = min({p} × 0.15, {cap}) = {s}".format(
            cap=cap, p=round(price, 2), s=round(subsidy, 2)),
        "口径标签": "2026 现行（政策参数非实时，以下单结算页为准）",
        "note": "结算价按扣完平台券/会员/店铺优惠后的成交价传入；省份资格另行确认（eligibility_check）。",
    }
    return json.dumps(result, ensure_ascii=False) if return_json else str(result)


if __name__ == "__main__":
    import asyncio
    # 回归：T13 / T17 / T38 + 家电类 + 能效必传 + >6000
    print(asyncio.run(subsidy_calc(6499, "电脑", "1级")))      # 应 974.85 / 5524.15
    print(asyncio.run(subsidy_calc(6499, "电脑")))             # 应 ok=false（未提供能效）
    print(asyncio.run(subsidy_calc(6499, "电脑", "2级")))      # 应 ok=false（能效不符）
    print(asyncio.run(subsidy_calc(6200, "手机")))             # 应 ok=false（>6000，含地方补贴提示）
    print(asyncio.run(subsidy_calc(6000, "空调", "1级")))      # 应 900 / 5100
