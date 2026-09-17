"""国补品类别名表 —— 把用户说法归一成资料卡认得的品类名。

职责刻意只剩「自然语言 → 品类名」这一件:

- **品类清单不在这里**, 在资料卡 ``fact-cards/guobu-2026.yaml`` 的 ``categories``
  (单一数据源)。本模块只补别名; 卡里有、别名表没登记的品类按名字本身命中。
- **档位归属(家电/数码)也不在这里**, 在资料卡的 ``tier``, 由
  ``_fact_cards.params_of()`` 合并后交给调用方。

本模块的键必须与资料卡 ``categories`` 的键一致 —— 由
``tests/agents/desktop/test_guobu_fact_card.py`` 钉死, 少一个、多一个都失败。

版本:
- v2(2026-09-01, 枚举化改造): 品类归一化交给模型, 工具侧只做确定性兜底;
- v1.3: 删组合词穷举(PARTS_WORDS) —— 电视柜/空调扇/手机壳由「模型映射不出枚举
  → 不调工具」兜底, 工具侧的收尾匹配天然不命中(它们不以品类词**结尾**);
- v1.4: 清单与档位移交资料卡, 本模块只留别名。
"""

from collections.abc import Iterable

# 品类 → 别名白名单(归一品类名; 完全相等或以别名结尾命中)
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


def match_category(subject: str, categories: Iterable[str]) -> str | None:
    """返回归一品类名; 无法确定返回 ``None``。

    *categories* 是资料卡登记的品类名集合(``_fact_cards.category_names()``)。
    对每个品类, 命中词 = 品类名本身 + ``ALIASES`` 里的别名; **取最长命中**,
    这样「平板电脑」落到平板而不是电脑。

    只做确定性兜底, 不做语义猜测: 电视柜 / 空调扇 / 手机壳这类组合词不以品类词
    结尾, 天然不命中, 由「模型映射不出枚举 → 不调工具」在上一层拦住。
    """
    text = (subject or "").strip()
    if not text:
        return None
    best: str | None = None
    best_len = -1
    for cat in categories:
        for alias in (cat, *ALIASES.get(cat, ())):
            if (text == alias or text.endswith(alias)) and len(alias) > best_len:
                best, best_len = cat, len(alias)
    return best
