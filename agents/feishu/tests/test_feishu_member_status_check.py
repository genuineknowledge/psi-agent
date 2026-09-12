"""feishu_member_status_check 结构钉 —— 看板人名三分类(在职/已离职或冻结/解析失败)。"""

from __future__ import annotations

import importlib
import json


def test_every_name_lands_in_exactly_one_bucket() -> None:
    f = importlib.import_module("_feishu_impl")
    members = [
        {"name": "张三", "open_id": "ou_a"},
        {"name": "李四", "open_id": "ou_b"},
        {"name": "王五", "open_id": "ou_c"},
        {"name": "王五", "open_id": "ou_d"},  # 重名 → unresolved
    ]
    out = f._classify_names(["张三", "李四", "王五", "赵六"], members)
    assert out["active"] == [{"name": "张三", "open_id": "ou_a"}, {"name": "李四", "open_id": "ou_b"}]
    assert out["resigned"] == ["赵六"]
    assert out["unresolved"] == ["王五"]
    # 每人恰好进一个桶
    assert len(out["active"]) + len(out["resigned"]) + len(out["unresolved"]) == 4


def test_names_are_trimmed_before_matching() -> None:
    f = importlib.import_module("_feishu_impl")
    out = f._classify_names(
        [" 张三 ", "李四"],
        [{"name": "张三", "open_id": "ou_a"}, {"name": "李四", "open_id": "ou_b"}],
    )
    assert out["active"][0]["name"] == "张三"
    assert out["active"][1]["name"] == "李四"
    assert not out["resigned"] and not out["unresolved"]


def test_tool_rejects_bad_names_json() -> None:
    f = importlib.import_module("_feishu_impl")
    err = json.loads(f.dumps_result(f._error("names_json must be valid JSON: x")))
    assert err["ok"] is False
    assert "JSON" in err["message"]


def test_status_flags_push_to_resigned() -> None:
    """status.is_resigned/is_exited/is_frozen 任一为真 → resigned;查不到也是。"""
    f = importlib.import_module("_feishu_impl")
    members = [
        {"name": "张三", "open_id": "ou_a", "status": {"is_activated": True}},
        {"name": "李四", "open_id": "ou_b", "status": {"is_activated": True, "is_frozen": True}},
        {"name": "王五", "open_id": "ou_c", "status": {"is_resigned": True}},
    ]
    out = f._classify_names(["张三", "李四", "王五", "赵六"], members)
    assert out["active"] == [{"name": "张三", "open_id": "ou_a"}]
    assert sorted(out["resigned"]) == ["李四", "王五", "赵六"]
    assert not out["unresolved"]


def test_no_status_defaults_to_active() -> None:
    """status 缺省(批量查询失败的退化路径)按活跃处理。"""
    f = importlib.import_module("_feishu_impl")
    out = f._classify_names(
        ["张三", "赵六"],
        [{"name": "张三", "open_id": "ou_a", "status": {}}],
    )
    assert out["active"] == [{"name": "张三", "open_id": "ou_a"}]
    assert out["resigned"] == ["赵六"]
