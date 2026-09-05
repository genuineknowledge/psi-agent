"""feishu_todo_compare_table 结构钉 —— 个人对比表字段与表名约定。"""

from __future__ import annotations

import importlib


def test_compare_table_name_prefix() -> None:
    mod = importlib.import_module("feishu_todo_compare_table")
    assert mod._compare_table_name("2026-09-04") == "个人对比-2026-09-04"
    assert mod._compare_table_name(" 2026-09-07 ") == "个人对比-2026-09-07"


def test_compare_schema_person_and_number_types() -> None:
    mod = importlib.import_module("feishu_todo_compare_table")
    fields = {f["field_name"]: f for f in mod._COMPARE_SCHEMA_FIELDS}
    # 成员/mentor 是人员字段,六项是数字字段,结论/待确认是文本
    assert fields["成员"]["type"] == 11
    assert fields["mentor"]["type"] == 11
    for metric in ("新开", "承接", "消失", "已闭环", "回流", "请假顺延"):
        assert fields[metric]["type"] == 2
    assert fields["结论"]["type"] == 1
    assert fields["待确认"]["type"] == 1
    # 六项词表固定,一个不多一个不少
    assert {m for m in fields if m not in ("周期日期", "成员", "mentor", "结论", "待确认")} == {
        "新开", "承接", "消失", "已闭环", "回流", "请假顺延",
    }
