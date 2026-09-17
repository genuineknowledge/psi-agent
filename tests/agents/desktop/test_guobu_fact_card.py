"""国补政策资料卡(政策参数单一数据源)的回归判据。

钉住三件事:

1. **单一数据源**: 比例 / 上限 / 门槛 / 品类清单只存在于
   ``fact-cards/guobu-2026.yaml``; 两个工具的 ``.py`` 里不得再有第二份
   (《省钱场景交接》§7 坑 1「参数两处重复, 改一处必改另一处」)。
2. **改卡即改行为**: 改掉卡里的上限, ``policy_query`` 与 ``subsidy_calc`` 的
   输出必须**同时**跟着变。这是单一数据源唯一的实证判据 —— 只查字面量查不出
   「又抄了一份、恰好抄得一样」。
3. **口径未漂移**: 参数从代码挪进资料卡是一次纯重构, 逐品类参数、
   ``supported_text``、品类归一的行为与挪之前逐字一致。

``sys.path`` 由同级 ``conftest.py`` 挂好(它把 ``agents/desktop/tools`` 插进来),
所以下面可以直接按裸名 import 工具模块 —— 与 ``test_fusion_memory_*.py`` 同一套。
"""

from __future__ import annotations

# RUF001: 下面若干断言钉的是**期望输出本身** —— 政策口径里的全角冒号/括号/书名号
# 就是用户会看到的字节, 改半角等于改断言、把「口径未漂移」这条判据悄悄废掉。
# ruff: noqa: RUF001
import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

# 两个分支刻意不同: 运行时靠同级 conftest 把 ``agents/desktop/tools`` 挂上 sys.path,
# 工具之间与测试都用裸名 import; 而 ty 不认那个运行时插入, 只能按包路径解析。
# 与 ``test_fusion_memory_tools.py`` 同一套写法。
if TYPE_CHECKING:
    from agents.desktop.tools import _fact_cards, _guobu_categories, policy_query, subsidy_calc
else:
    import _fact_cards
    import _guobu_categories
    import policy_query
    import subsidy_calc

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT / "agents" / "desktop"
TOOLS_DIR = WORKSPACE_ROOT / "tools"
CARD_PATH = WORKSPACE_ROOT / "fact-cards" / "guobu-2026.yaml"

# 迁移前两个工具各自持有的政策字面量。它们现在只该出现在资料卡里。
# 只写浮点: Python 里 ``1500 in {1500.0}`` 为真, 所以整数字面量也一并被抓住。
BANNED_NUMBERS = {0.15, 1500.0, 500.0, 6000.0}
BANNED_STRINGS = {"15%", "1500 元", "500 元", "≤6000 元", "1 级能效/水效", "每人每类 1 件"}

TOOL_SOURCES = ("policy_query.py", "subsidy_calc.py", "_guobu_categories.py")


def _card() -> dict[str, Any]:
    return yaml.safe_load(CARD_PATH.read_text(encoding="utf-8"))


def _docstring_values(tree: ast.Module) -> set[str]:
    """模块/函数/类 docstring 的原文 —— 注释里提到历史口径不算「又抄了一份」。"""
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                values.add(doc)
    return values


def _code_literals(py_name: str) -> tuple[set[Any], set[str]]:
    """(数值字面量, 非 docstring 字符串字面量)。"""
    tree = ast.parse((TOOLS_DIR / py_name).read_text(encoding="utf-8"))
    docstrings = _docstring_values(tree)
    numbers: set[Any] = set()
    strings: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
            continue
        if isinstance(node.value, (int, float)):
            numbers.add(node.value)
        elif isinstance(node.value, str) and node.value not in docstrings:
            strings.add(node.value)
    return numbers, strings


# --------------------------------------------------------------------------- #
# 资料卡本身
# --------------------------------------------------------------------------- #


def test_card_loads_and_declares_its_own_freshness() -> None:
    """卡必须自带版本与时效三元组 —— 政策查询要把它们原样透出给模型。"""
    card = _card()
    assert card["card"] == "guobu"
    for key in ("version", "verified_at", "expires_at", "year"):
        assert card.get(key), f"资料卡缺少 {key}"


def test_alias_table_covers_exactly_the_card_categories() -> None:
    """别名表的键必须与卡里的品类一一对应, 少一个多一个都失败。

    这是「品类清单在卡里、别名在代码里」这条分工的接缝: 任何一边单独加品类都会被
    这条挡住, 而不是等到线上映射不出品类才发现。
    """
    assert set(_guobu_categories.ALIASES) == set(_card()["categories"])


def test_every_category_points_at_a_defined_tier() -> None:
    card = _card()
    for kind, entry in card["categories"].items():
        assert entry["tier"] in card["tiers"], f"品类 {kind} 指向了未定义的档位 {entry['tier']}"


def test_every_tier_carries_the_machine_values_the_tools_need() -> None:
    """两个工具只认这四个机器值; 缺一个就是配置错, 不是输入错。"""
    for name, tier in _card()["tiers"].items():
        for field in ("rate", "cap", "price_gate", "energy_required"):
            assert field in tier, f"档位 {name} 缺少 {field}"


def test_supported_text_layout_is_complete() -> None:
    """拼装规则也必须由卡提供 —— 代码里留默认值就等于又开一个第二数据源。"""
    layout = _card()["supported_text"]
    for key in ("prefix", "tier_tpl", "tier_join", "item_join"):
        assert layout.get(key), f"supported_text 缺少 {key}"


def test_card_root_is_the_pack_that_defines_the_tools() -> None:
    """资料卡根必须是**定义这两个工具的那个包**, 不是 sys.path 上先出现的那个包。

    两个能力包各有一份 ``tools/_runtime_paths.py``, 靠裸名 import 时谁先谁赢。曾经用
    ``_runtime_paths.agent_dir()`` 解析资料卡根 —— 全量收集(两个包的 tools 目录都在
    ``sys.path`` 上)时它指向 feishu 包, 资料卡直接找不到。这条判据让那次回归无法悄悄回来。
    """
    assert _fact_cards._roots() == [WORKSPACE_ROOT]
    assert (WORKSPACE_ROOT / "fact-cards" / "guobu-2026.yaml").is_file()


# --------------------------------------------------------------------------- #
# 口径未漂移(迁移前的行为基线)
# --------------------------------------------------------------------------- #


def test_supported_text_is_byte_identical_to_the_pre_migration_string() -> None:
    """这句人话过去是手写常量, 现在由品类表拼装 —— 输出必须一字不差。"""
    assert _fact_cards.supported_text(_card()) == (
        "2026 国补家电 6 类：冰箱/洗衣机/电视/空调/热水器/电脑；数码 4 类：手机/平板/智能手表手环/智能眼镜"
    )


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("冰箱", "冰箱"),
        ("电冰箱", "冰箱"),
        ("笔记本", "电脑"),
        ("游戏本", "电脑"),
        ("平板电脑", "平板"),
        ("pad", "平板"),
        ("智能手表手环", "手表"),
        ("智能眼镜", "眼镜"),
        ("滚筒洗衣机", "洗衣机"),
        # 组合词/配件必须**不**命中: 它们不以品类词结尾
        ("电视柜", None),
        ("空调扇", None),
        ("手机壳", None),
        ("洗衣机罩", None),
        ("", None),
    ],
)
def test_match_category_keeps_its_pre_migration_behaviour(subject: str, expected: str | None) -> None:
    assert _guobu_categories.match_category(subject, _fact_cards.category_names(_card())) == expected


def test_params_merge_category_over_tier() -> None:
    """品类覆盖档位默认值; 未覆盖的字段继承档位。"""
    card = _card()

    fridge = _fact_cards.params_of(card, "冰箱")
    assert fridge["tier"] == "家电"
    assert (fridge["rate"], fridge["cap"], fridge["price_gate"]) == (0.15, 1500.0, None)
    assert fridge["energy_required"] is True
    assert fridge["category_label"] == "家电以旧换新"  # 档位默认, 未被品类覆盖
    assert fridge["gate_label"] == "无"

    computer = _fact_cards.params_of(card, "电脑")
    assert computer["category_label"] == "家电以旧换新（6 类之一）"  # 品类覆盖
    assert computer["gate_label"] == "无（电脑等家电无 6000 元上限）"  # 品类覆盖
    assert computer["source"] == "发改环资〔2025〕1745号 / 商办流通函〔2025〕469号"  # 品类覆盖

    assert _fact_cards.params_of(card, "手表")["display"] == "智能手表手环"  # 品类自己的展示名
    # 没写 display 则用品类名本身, 不是档位名
    assert _fact_cards.params_of(card, "冰箱")["display"] == "冰箱"


async def test_policy_query_output_is_unchanged() -> None:
    """逐字段钉住迁移前的输出。"""
    payload = json.loads(await policy_query.policy_query(subject="电脑", region="江苏"))

    assert payload["ok"] is True
    assert payload["query"] == {"subject": "电脑", "region": "江苏"}
    assert payload["policy"] == {
        "年份": "2026",
        "口径标签": "2026 现行（实施期 2026-01-01 至 2026-12-31）",
        "品类": "家电以旧换新（6 类之一）",
        "补贴比例": "15%",
        "单件上限": "1500 元",
        "能效要求": "1 级能效/水效",
        "价格门槛": "无（电脑等家电无 6000 元上限）",
        "件数": "每人每类 1 件",
        "来源": "发改环资〔2025〕1745号 / 商办流通函〔2025〕469号",
        "印发/实施": "2025-12 印发、2026-01-01 实施",
        "2025旧口径": {
            "家电品类数": "12 类",
            "家电能效": "1 级 20% / 2 级 15%",
            "家电单件上限": "2000 元",
            "数码品类": "手机/平板/智能手表手环（3 类）",
        },
    }
    assert payload["fact_card_version"] == "guobu-v1.0"
    assert payload["verified_at"] == "2026-08-18"
    assert payload["expires_at"] == "2026-12-31"
    assert "2026-12-31" in payload["note"]


async def test_policy_query_rejects_accessory_words() -> None:
    payload = json.loads(await policy_query.policy_query(subject="电视柜"))
    assert payload["ok"] is False
    assert payload["suggest_search"] is True
    assert "电视柜" in payload["reason"]
    assert payload["quota_label"] == "2026 现行"


async def test_subsidy_calc_output_is_unchanged() -> None:
    """家电正例: 结算价 5999、1 级能效、江苏。"""
    payload = json.loads(
        await subsidy_calc.subsidy_calc(price=5999, category="笔记本", energy_level="1级能效", region="江苏")
    )

    assert payload["ok"] is True
    assert payload["category"] == "家电（以旧换新类）"
    assert payload["kind"] == "电脑"
    assert payload["结算价"] == 5999.0
    assert payload["补贴比例"] == "15%"
    assert payload["单件上限"] == 1500.0
    assert payload["补贴"] == 899.85
    assert payload["到手价"] == 5099.15
    assert payload["公式"] == "补贴 = min(结算价 × 15%, 上限 1500.0) = min(5999.0 × 0.15, 1500.0) = 899.85"
    assert payload["region"] == "江苏"
    assert payload["口径标签"] == "2026 现行（政策参数非实时，以下单结算页为准）"
    assert "每人每类限 1 件" in payload["assumption"]


async def test_subsidy_calc_energy_gate_blocks_missing_and_bogus_levels() -> None:
    missing = json.loads(await subsidy_calc.subsidy_calc(price=3000, category="冰箱"))
    assert missing["ok"] is False
    assert missing["need_energy_level"] is True
    assert missing["subsidy"] == 0

    bogus = json.loads(await subsidy_calc.subsidy_calc(price=3000, category="冰箱", energy_level="1.5匹"))
    assert bogus["ok"] is False
    assert "1.5匹" in bogus["reason"]  # 「1.5 匹」是规格, 不是能效


async def test_subsidy_calc_does_not_require_energy_for_digital() -> None:
    payload = json.loads(await subsidy_calc.subsidy_calc(price=4000, category="手机"))
    assert payload["ok"] is True
    assert payload["kind"] == "手机"
    assert payload["单件上限"] == 500.0
    assert payload["补贴"] == 500.0  # min(600, 500) 撞上限


async def test_subsidy_calc_price_gate_only_applies_where_the_card_says_so() -> None:
    """数码超 6000 被挡; 家电没有门槛, 同价位照算。"""
    digital = json.loads(await subsidy_calc.subsidy_calc(price=6100, category="手机"))
    assert digital["ok"] is False
    assert "不得断言" in digital["reason"]

    home = json.loads(await subsidy_calc.subsidy_calc(price=6100, category="冰箱", energy_level="一级"))
    assert home["ok"] is True
    assert home["补贴"] == 915.0  # min(6100 x 0.15, 1500)


# --------------------------------------------------------------------------- #
# 单一数据源
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("py_name", TOOL_SOURCES)
def test_tool_sources_hold_no_policy_literals(py_name: str) -> None:
    """两个工具的代码里不得再出现比例 / 上限 / 门槛的数字或展示串。

    docstring 除外 —— 版本注记里写「v1.2: 超门槛地方补贴提示」是在记历史, 不是在
    持有参数。只扫代码里的常量。
    """
    numbers, strings = _code_literals(py_name)
    leaked_numbers = sorted(numbers & BANNED_NUMBERS)
    leaked_strings = sorted(strings & BANNED_STRINGS)
    assert not leaked_numbers, f"{py_name} 里残留政策数字: {leaked_numbers}"
    assert not leaked_strings, f"{py_name} 里残留政策展示串: {leaked_strings}"


@pytest.mark.parametrize("py_name", TOOL_SOURCES)
def test_tool_sources_hold_no_category_enumeration(py_name: str) -> None:
    """品类清单也不得在工具里再枚举一遍 —— 它属于资料卡。

    抓的是迁移前 ``is_home`` / ``is_digital`` 那种「元组里逐个列品类」的写法。
    """
    source = (TOOLS_DIR / py_name).read_text(encoding="utf-8")
    assert '"冰箱", "洗衣机"' not in source, f"{py_name} 又枚举了家电 6 类"
    assert '"手机", "平板"' not in source, f"{py_name} 又枚举了数码 4 类"


async def test_editing_the_card_moves_both_tools_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """改卡里一个上限, 两个工具必须同时跟着变。

    这是单一数据源唯一的实证判据: 只扫字面量抓不出「又抄了一份、恰好抄得一样」,
    而这里两个工具读到的是同一份被改过的卡。

    ``cap`` 只管算钱(机器值), ``cap_label`` 只管展示 —— 两个都改, 才能证明两个
    工具读的是同一张卡而不是各自那份恰好一致的副本。
    """
    card = _card()
    card["tiers"]["家电"]["cap"] = 777.0
    card["tiers"]["家电"]["cap_label"] = "777 元"
    edited = tmp_path / "fact-cards"
    edited.mkdir()
    (edited / "guobu-2026.yaml").write_text(yaml.safe_dump(card, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(_fact_cards, "_roots", lambda: [tmp_path])

    calc = json.loads(await subsidy_calc.subsidy_calc(price=6000, category="冰箱", energy_level="1级"))
    assert calc["单件上限"] == 777.0
    assert calc["补贴"] == 777.0  # min(900, 777) —— 撞的是改过后的上限

    query = json.loads(await policy_query.policy_query(subject="冰箱"))
    assert query["policy"]["单件上限"] == "777 元"

    # 别的档位没被顺手改坏
    digital = json.loads(await subsidy_calc.subsidy_calc(price=4000, category="手机"))
    assert digital["单件上限"] == 500.0


async def test_loading_a_missing_card_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """卡丢了要报错, 不能静默回落到「没有政策参数」——那种失败会变成一脸平静的错答案。"""
    monkeypatch.setattr(_fact_cards, "_roots", lambda: [tmp_path])
    with pytest.raises(FileNotFoundError):
        await _fact_cards.load_card("guobu-2026")
