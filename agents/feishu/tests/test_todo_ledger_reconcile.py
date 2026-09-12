"""feishu_todo_ledger_reconcile 结构钉 —— 台账行 vs 看板 mentor 列的对账。

The ledger stamps ``负责人``/``mentor`` once, at cycle-sync time, and never updates them
again; a board edit made afterwards therefore leaves every ledger consumer reporting the
pre-edit relation. These tests pin the three states that decision depends on:

- a board edit the ledger has not caught up with → ``mentor_changed`` + record_id;
- a field rewrite that did **not** move the row to the new mentor's base → ``needs_move``,
  which is the difference between "已对齐" and "看起来对齐了";
- an owner the board does not have → a human-required bucket, never a guess.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"


def _module() -> Any:
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    return importlib.import_module("_feishu.ledger_reconcile")


def _tool() -> Any:
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    return importlib.import_module("feishu_todo_ledger_reconcile")


def _core_module() -> Any:
    """The shared client layer, for monkeypatching ``_invoke`` in the board-reader tests."""
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    return importlib.import_module("_feishu_impl")


def test_board_edit_after_the_rows_exist_is_reported() -> None:
    """看板把王五从甲改到乙,但台账行还写着甲 —— 必须报出来,且带 record_id。"""
    out = _module().classify_rows(
        {"王五": "乙"},
        [{"record_id": "rec1", "owner": "王五", "mentor": "甲"}],
        expected_mentor="甲",
    )
    assert out["mentor_changed_total"] == 1
    assert out["mentor_changed"][0] == {
        "record_id": "rec1",
        "person": "王五",
        "row_mentor": "甲",
        "board_mentor": "乙",
    }
    # 行的字段与它所在的 base(甲)一致,所以这一行不是 needs_move —— 它只是没跟上已变的看板。
    assert out["needs_move_total"] == 0
    assert out["consistent"] == 0


def test_field_fix_without_moving_the_row_is_not_alignment() -> None:
    """把字段改成乙之后行还躺在甲的 base 里 —— 这不算对齐,必须仍报 needs_move。"""
    out = _module().classify_rows(
        {"王五": "乙"},
        [{"record_id": "rec1", "owner": "王五", "mentor": "乙"}],
        expected_mentor="甲",
    )
    assert out["mentor_changed_total"] == 0
    assert out["consistent"] == 1, "字段已与看板一致"
    assert out["needs_move_total"] == 1, "但行还在旧 mentor 的本里,按 base 枚举的消费者仍按旧关系读到它"
    assert out["needs_move"][0]["expected_base"] == "甲"


def test_unknown_person_is_never_reclassified_as_changed() -> None:
    """看板查不到的人 → 交人工,不猜测、更不按看板某个名字改写。"""
    out = _module().classify_rows(
        {"张三": "郑淳"},
        [{"record_id": "rec9", "owner": "查无此人", "mentor": "甲"}],
        expected_mentor="甲",
    )
    assert out["mentor_changed_total"] == 0
    assert out["person_not_on_board_total"] == 1
    assert out["person_not_on_board"][0]["person"] == "查无此人"


def test_missing_mentor_on_either_side_is_its_own_bucket() -> None:
    board = {"看板无 mentor": "", "两边都有": "甲"}
    rows = [
        {"record_id": "r1", "owner": "看板无 mentor", "mentor": "甲"},
        {"record_id": "r2", "owner": "两边都有", "mentor": ""},
        {"record_id": "r3", "owner": "", "mentor": "甲"},
    ]
    out = _module().classify_rows(board, rows)
    assert out["board_missing_mentor_total"] == 1
    assert out["row_missing_mentor_total"] == 1
    assert out["row_missing_owner_total"] == 1
    assert out["mentor_changed_total"] == 0
    assert out["row_missing_owner"][0] == {"record_id": "r3"}


def test_every_row_lands_in_exactly_one_primary_bucket() -> None:
    board = {"甲的人": "甲", "乙的人": "乙"}
    rows = [
        {"record_id": "r1", "owner": "甲的人", "mentor": "甲"},  # consistent
        {"record_id": "r2", "owner": "乙的人", "mentor": "甲"},  # mentor_changed
        {"record_id": "r3", "owner": "外来人", "mentor": "甲"},  # person_not_on_board
        {"record_id": "r4", "owner": "", "mentor": "甲"},  # row_missing_owner
        {"record_id": "r5", "owner": "甲的人", "mentor": ""},  # row_missing_mentor
    ]
    out = _module().classify_rows(board, rows)
    primary = (
        out["mentor_changed_total"]
        + out["person_not_on_board_total"]
        + out["row_missing_owner_total"]
        + out["row_missing_mentor_total"]
        + out["board_missing_mentor_total"]
        + out["consistent"]
    )
    assert out["rows"] == len(rows)
    assert primary == len(rows), "每行恰好落一个主桶,不许重复计数也不许漏"


def test_bucket_totals_survive_clipping() -> None:
    """清单最多回 50 条,但总数必须是真值 —— 截断不许把「还有多少条」一起吃掉。"""
    board = {"王五": "乙"}
    rows = [{"record_id": f"rec{i}", "owner": "王五", "mentor": "甲"} for i in range(60)]
    out = _module().classify_rows(board, rows)
    assert out["mentor_changed_total"] == 60
    assert len(out["mentor_changed"]) == 50
    assert out["truncated"]["mentor_changed"] is True


def test_norm_strips_the_mention_prefix_the_board_writes() -> None:
    """看板的人名/mentor 列是 @提及 单元格,台账人员字段是裸名字 —— 两边必须同一套归一。"""
    norm = _module()._norm
    assert norm("@张三") == "张三"
    assert norm("  @张三  ") == "张三"
    assert norm("张三") == "张三"
    assert norm(None) == ""
    assert norm("") == ""


def test_person_field_names_are_read_from_the_bitable_shape() -> None:
    names = _module()._person_names([{"id": "ou_x", "name": "张三"}, {"id": "ou_y", "name": "李四"}])
    assert names == ["张三", "李四"]
    assert _module()._person_names([{"id": "ou_x"}]) == []
    assert _module()._person_names(None) == []


@pytest.mark.anyio
async def test_board_reader_normalizes_mention_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    """真读看板那一步也必须剥 @:表头认列 + 单元格 @张三 → 键是「张三」。"""
    mod = _module()
    core = _core_module()

    async def fake_invoke(req: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "data": {"node": {"obj_token": "sht1"}}}

    async def fake_grid(
        token: str, range_: str = "", max_rows: int = 50, start_row: int = 1, user_key: str = ""
    ) -> dict[str, Any]:
        if range_ == "!A1:AZ1":
            return {"ok": True, "rows": [["tag", "人名", "mentor", "9.9"]], "has_more": False}
        if start_row == 2:
            return {
                "ok": True,
                "rows": [["", "@张三", "@郑淳", "todo"], ["", "@李四", "@孙逊", "todo"]],
                "has_more": False,
            }
        return {"ok": True, "rows": [], "has_more": False}

    monkeypatch.setattr(core, "_invoke", fake_invoke)
    monkeypatch.setattr(mod, "read_sheet_grid_impl", fake_grid)

    out = await mod._read_board("https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc", "ou_x")
    assert out["ok"] is True
    assert out["board"] == {"张三": "郑淳", "李四": "孙逊"}
    assert out["columns"] == {"person": "人名", "mentor": "mentor"}
    assert out["people"] == 2


@pytest.mark.anyio
async def test_board_reader_errors_when_the_mentor_column_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """mentor 列认不出来时报错并回显表头 —— 不许退化成「这个 mentor 名下没人」。"""
    mod = _module()
    core = _core_module()

    async def fake_invoke(req: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "data": {"node": {"obj_token": "sht1"}}}

    async def fake_grid(
        token: str, range_: str = "", max_rows: int = 50, start_row: int = 1, user_key: str = ""
    ) -> dict[str, Any]:
        if range_ == "!A1:AZ1":
            return {"ok": True, "rows": [["tag", "人名", "带教", "9.9"]], "has_more": False}
        return {"ok": True, "rows": [["", "@张三", "@郑淳", "todo"]], "has_more": False}

    monkeypatch.setattr(core, "_invoke", fake_invoke)
    monkeypatch.setattr(mod, "read_sheet_grid_impl", fake_grid)

    out = await mod._read_board("https://x.feishu.cn/wiki/ABC", "ou_x")
    assert out["ok"] is False
    assert "mentor" in out["message"]
    assert "带教" in out["message"], "表头必须回显,人才能判断是别名缺失还是看板本身没有 mentor 列"


def test_parse_ledgers_defaults_and_rejections() -> None:
    parse = _module()._parse_ledgers
    parsed, problem = parse('[{"app_token":"bascn1","mentor_name":"孙逊"}]')
    assert problem is None and parsed is not None
    assert parsed[0]["person_field"] == "负责人"
    assert parsed[0]["mentor_field"] == "mentor"
    assert parsed[0]["mentor_name"] == "孙逊"

    for bad in ("[]", "not json", '[{"table_id":"tbl1"}]', '["bascn1"]'):
        parsed, problem = parse(bad)
        assert parsed is None and problem, f"{bad!r} 必须被拒"


@pytest.mark.anyio
async def test_impl_rejects_missing_inputs() -> None:
    mod = _module()
    out = await mod.ledger_reconcile_impl("")
    assert out["ok"] is False and "board_link" in out["message"]
    out = await mod.ledger_reconcile_impl("https://x.feishu.cn/wiki/ABC")
    assert out["ok"] is False and "ledger" in out["message"]
    out = await mod.ledger_reconcile_impl("https://x.feishu.cn/wiki/ABC", folder_token="fld1", cycle_date="")
    assert out["ok"] is False and "cycle_date" in out["message"]


def test_tool_entrypoint_is_async() -> None:
    """``load_tools_from_workspace`` 只加载 async def —— 写成同步函数会被静默跳过。"""
    assert inspect.iscoroutinefunction(_tool().feishu_todo_ledger_reconcile)


def test_tool_is_declared_where_the_model_reads() -> None:
    """工具必须出现在 agent 实际读到的文档里,否则它不会被人调用。"""
    name = "feishu_todo_ledger_reconcile"
    for doc in ("AGENTS.md", "TOOLS.md"):
        text = (WORKSPACE_ROOT / doc).read_text(encoding="utf-8")
        assert name in text, f"{name} is exposed to the model but never declared in {doc}"
    audit = (WORKSPACE_ROOT / "skills" / "company-todo-audit" / "SKILL.md").read_text(encoding="utf-8")
    assert name in audit, "台账推送上游的技能必须要求先对账,再按 mentor 分组出卡"
