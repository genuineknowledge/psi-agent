"""feishu_todo_fill_status 结构钉 —— 缺写判定管线:五类桶,每人恰好一桶。"""

from __future__ import annotations

import importlib
import json


def test_buckets_are_exclusive_and_complete() -> None:
    f = importlib.import_module("_feishu_impl")
    people = [
        {"name": "在职已填", "filled": True},
        {"name": "在职缺写", "filled": False},
        {"name": "在职请假", "filled": False},
        {"name": "离职", "filled": False},
        {"name": "重名", "filled": False},
        {"name": "名字对不上请假", "filled": False},
    ]
    out = f._build_buckets(
        "9.9",
        "孙逊",
        people,
        resigned={"离职"},
        unresolved={"重名"},
        on_leave={"在职请假"},
        needs_fix={"名字对不上请假"},
    )
    assert out["缺写"] == ["在职缺写"]
    assert out["请假免填"] == ["在职请假"]
    assert out["已离职/冻结"] == ["离职"]
    assert out["解析失败"] == ["重名", "名字对不上请假"]
    assert out["已填"] == ["在职已填"]
    total = sum(len(out[k]) for k in ("缺写", "请假免填", "已离职/冻结", "解析失败", "已填"))
    assert total == len(people)


def test_find_col_exact_and_substring() -> None:
    f = importlib.import_module("_feishu_impl")
    header = ["任务负责人", "mentor", "9.7", "9.9"]
    assert f._find_col(header, ("负责人", "姓名")) == 0
    assert f._find_col(header, ("mentor", "上级")) == 1
    assert f._find_col(header, ("9.9",)) == 3
    assert f._find_col(header, ("不存在的列",)) == -1


def test_tool_rejects_missing_args() -> None:
    f = importlib.import_module("_feishu_impl")
    err = json.loads(f.dumps_result(f._error("board_link is required.")))
    assert err["ok"] is False
    err2 = json.loads(f.dumps_result(f._error("cycle_date is required (the column header, e.g. 9.9).")))
    assert "cycle_date" in err2["message"]
