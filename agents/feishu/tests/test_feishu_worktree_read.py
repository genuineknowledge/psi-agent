"""feishu_worktree_read 的解析层单测 —— 不调真实 API,只测节点树 → 每人 @ 清单的变换。

工作树 API 返回的是平铺节点(parent_id 关联),工具负责重建层级、归集 @ 提及、
上溯路径。这里用构造的节点数据钉住这些行为。
"""

from __future__ import annotations

import importlib

# 与其余 feishu 测试一致:经 _feishu_impl 取函数(直接 from _feishu.worktree
# 会与 _feishu_impl 末尾的 re-export 形成循环导入)。
_impl = importlib.import_module("_feishu_impl")

build_people = _impl.build_people
_node_text = _impl._node_text


def _node(node_id: str, texts: list, parent_id: str = "") -> dict:
    n = {"node_id": node_id, "texts": texts}
    if parent_id:
        n["parent_id"] = parent_id
    return n


def _text(content: str) -> dict:
    return {"element_type": "text", "text": {"content": content}}


def _mention(open_id: str) -> dict:
    return {"element_type": "user", "mention_user": {"user": open_id}}


def test_build_people_collects_mentions_with_full_paths() -> None:
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("子任务"), _mention("ou_A")], parent_id="r"),
        _node("b", [_text("孙任务"), _mention("ou_B")], parent_id="a"),
    ]
    people, mentioned = build_people(nodes, {"ou_A": "张三", "ou_B": "李四"})
    assert mentioned == {"ou_A", "ou_B"}
    assert len(people) == 2
    assert people[0]["open_id"] == "ou_A"
    assert people[0]["items"] == ["根 / 子任务@张三"]
    assert people[1]["items"] == ["根 / 子任务@张三 / 孙任务@李四"]


def test_build_people_dedupes_repeated_mentions() -> None:
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("任务"), _mention("ou_A"), _text(" "), _mention("ou_A")], parent_id="r"),
    ]
    people, _ = build_people(nodes, {"ou_A": "张三"})
    assert len(people) == 1
    assert people[0]["count"] == 1  # 同一节点同人多 @ 只算一项


def test_build_people_skips_unmentioned_nodes() -> None:
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("没有 @ 任何人")], parent_id="r"),
    ]
    people, mentioned = build_people(nodes, {})
    assert mentioned == set()
    assert people == []


def test_build_people_sorted_by_count_desc() -> None:
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("一"), _mention("ou_A")], parent_id="r"),
        _node("b", [_text("二"), _mention("ou_B")], parent_id="r"),
        _node("c", [_text("三"), _mention("ou_B")], parent_id="r"),
    ]
    people, _ = build_people(nodes, {"ou_A": "甲", "ou_B": "乙"})
    assert [p["open_id"] for p in people] == ["ou_B", "ou_A"]
    assert people[0]["count"] == 2


def test_missing_name_falls_back_to_open_id() -> None:
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("任务"), _mention("ou_unknown")], parent_id="r"),
    ]
    people, _ = build_people(nodes, {})
    assert people[0]["name"] == "ou_unknown"  # 名字查不到时不静默丢弃


def test_node_text_strips_zero_width_space() -> None:
    # 思维导图节点里大量夹杂零宽空格(​),展示前必须清掉
    node = _node("x", [_text("任务"), _text("​"), _mention("ou_A")])
    text = _node_text(node, {"ou_A": "张三"})
    assert "​" not in text
    assert text == "任务@张三"


def test_node_text_plain_text_only() -> None:
    node = _node("x", [_text("普通文本")])
    assert _node_text(node, {}) == "普通文本"


def test_same_person_parent_child_mentions_merge_into_one() -> None:
    """任务节点 @ 负责人,子分支又 @ 一遍同一人 → 只算一项(任务不是两件)。"""
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("Todo List"), _mention("ou_A")], parent_id="r"),
        _node("b", [_mention("ou_A")], parent_id="a"),  # 子分支重复 @,无文本
    ]
    people, _ = build_people(nodes, {"ou_A": "张三"})
    assert len(people) == 1
    assert people[0]["count"] == 1
    assert people[0]["items"] == ["根 / Todo List@张三"]


def test_child_mention_of_another_person_stays_separate() -> None:
    """父层 @ 甲、子分支 @ 乙 → 甲、乙各算一项,互不吞并。"""
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("demo"), _mention("ou_A")], parent_id="r"),
        _node("b", [_mention("ou_B")], parent_id="a"),
    ]
    people, _ = build_people(nodes, {"ou_A": "甲", "ou_B": "乙"})
    by_open_id = {p["open_id"]: p for p in people}
    assert by_open_id["ou_A"]["count"] == 1
    assert by_open_id["ou_B"]["count"] == 1
    assert by_open_id["ou_B"]["items"] == ["根 / demo@甲 / @乙"]


def test_deep_chain_mentions_merge_to_topmost_named_node() -> None:
    """三层链 @ 同一人 → 保留最浅的带名字节点,深层无文本节点并入。"""
    nodes = [
        _node("r", [_text("根")]),
        _node("a", [_text("任务"), _mention("ou_A")], parent_id="r"),
        _node("b", [_mention("ou_A")], parent_id="a"),
        _node("c", [_mention("ou_A")], parent_id="b"),
    ]
    people, _ = build_people(nodes, {"ou_A": "张三"})
    assert len(people) == 1
    assert people[0]["count"] == 1
