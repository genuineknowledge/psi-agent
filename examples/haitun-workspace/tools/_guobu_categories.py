# ruff: noqa: RUF001, RUF002, RUF003
"""国补品类共享常量与匹配（policy_query / subsidy_calc 共用，杜绝分叉）。

v1（2026-08-26，review 修复 #1）：
- 白名单别名 + 收尾匹配：完全相等或「品牌/系列 + 品类」以别名结尾才命中；
- 组合词排除：电视柜/空调扇/手机壳 等「品类词 + 附属词」不命中；
- 长别名优先：平板电脑 → 平板（数码），不因含「电脑」误归家电。
"""

# 品类 → 别名白名单（归一品类名；完全相等或以别名结尾命中）
ALIASES = {
    "电脑": ["电脑", "笔记本", "笔记本电脑", "台式机", "一体机", "游戏本", "台式电脑"],
    "手机": ["手机", "智能手机"],
    "平板": ["平板", "平板电脑", "平板pad", "pad"],
    "手表": ["手表", "手环", "智能手表", "智能手环", "智能手表手环"],
    "眼镜": ["眼镜", "智能眼镜"],
    "空调": ["空调", "空调机", "柜机", "挂机"],
    "冰箱": ["冰箱", "电冰箱"],
    "洗衣机": ["洗衣机", "滚筒洗衣机"],
    "电视": ["电视", "电视机", "智能电视"],
    "热水器": ["热水器", "电热水器", "燃气热水器"],
}

# 组合词排除：以这些词结尾的一律不算品类（电视柜/空调扇/手机壳/数据线…）
PARTS_WORDS = ("柜", "扇", "壳", "膜", "支架", "配件", "套", "罩", "挂架", "底座", "贴", "线", "充电器", "保护套")


def match_category(subject: str):
    """返回归一品类名；无法确定返回 None。"""
    s = (subject or "").strip()
    if not s:
        return None
    if s.endswith(PARTS_WORDS):
        return None
    best, best_len = None, -1
    for cat, aliases in ALIASES.items():
        for a in aliases:
            if (s == a or s.endswith(a)) and len(a) > best_len:
                best, best_len = cat, len(a)
                if len(a) > best_len:
                    best, best_len = cat, len(a)
    return best


def is_home(kind: str) -> bool:
    """家电类（15%/1500/1级能效）。"""
    return kind in ("电脑", "空调", "冰箱", "洗衣机", "电视", "热水器")


def is_digital(kind: str) -> bool:
    """数码类（15%/500/≤6000）。"""
    return kind in ("手机", "平板", "手表", "眼镜")


def supported_text() -> str:
    return ("2026 国补家电 6 类：冰箱/洗衣机/电视/空调/热水器/电脑；"
            "数码 4 类：手机/平板/智能手表手环/智能眼镜")
