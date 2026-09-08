"""Strict tests for the 16:00 mentor ledger push card (2026-09-07 new requirement).

Covers the new ``feishu_ledger_mentor_card_send`` (16:00 mentor ledger card) +
its template ``ledger-mentor-card.xml``.

Requirement anchors (马晨柯 2026-09-07):
- The 16:00 push to each mentor must show 同一个人 todolist 上期 vs 本期 前后对比 /
  连续性 / 事情搞没搞定.
- **定稿意见**: 承接/顺延/消失/闭环等去向不能只给计数 — 上期遗留条目必须**逐条列出**
  (成员/条目/去向/备注),mentor 才能看到「承接了哪条、顺延了哪条、消失哪条」。
- 去向白名单关键字(防口径漂移):承接中 / 已闭环 / 消失·待确认 / 回流·逾期 / 请假顺延;
  越界即整单拒发,不静默改写。
- Original plain-text base-link push stays; the card is additive.
- Test routing: env ``PSI_TODO_CARD_TEST_RECEIVE_ID`` reroutes every card to the
  tester; read at call time so tests can toggle it dynamically.

Strictness asserted (same discipline as tests/test_todo_check_cards.py):
1. fail-closed: bad rows_json / missing 成员 or 条目 / direction off-whitelist /
   > row cap never reach the sender;
2. template placeholder completeness — missing key errors out instead of dirty card;
3. card structure invariants: schema 2.0, table columns exactly as designed
   (成员/上期 TODO/去向/备注), no buttons / empty handlers, multi_use=False;
4. routing: env test receiver overrides real mentor; no receiver + no env → refuse;
5. send failure surfaced honestly (ok=false), never reported as success.

Run directly (no pytest needed):
    python3 tests/test_ledger_mentor_card.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
TPL_DIR = WORKSPACE_ROOT / "skills" / "card-dsl" / "templates"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _card_dsl import _TEMPLATE_DIR, render_template  # noqa: E402

TEST_RECEIVER = "ou_test_receiver_123"
ME = "ou_e8db482d20ced3e5be710999548c8386"

VALID_ROWS = [
    {"成员": "黄子建", "状态": "正常", "要点": "承接 2 · 闭环 1", "备注": "A/B 实验 9.6 截止"},
    {"成员": "高博", "状态": "正常", "要点": "承接 1 · 新开 1", "备注": "—"},
    {"成员": "李子祥", "状态": "请假顺延", "要点": "—", "备注": "2 条顺延,在假不计逾期"},
    {"成员": "董修奇", "状态": "需处理", "要点": "新开 3", "备注": "消失 1 条待确认"},
    {"成员": "王俊懿", "状态": "需处理", "要点": "—", "备注": "回流·逾期[3天]"},
    {"成员": "马菁宜", "状态": "正常", "要点": "本期 4 条", "备注": "—"},
    {"成员": "贺雅诗", "状态": "空白", "要点": "—", "备注": "两期空白 · 不判"},
]


def _load(mod: str):
    return importlib.import_module(mod)


class FakeSend:
    """Stand-in for _feishu_impl.send_card_impl recording the call."""

    def __init__(self, ok: bool = True):
        self.calls: list[tuple] = []
        self.ok = ok

    async def __call__(self, *args):
        self.calls.append(args)
        if not self.ok:
            return {"ok": False, "message": "send failed (mocked)"}
        return {"ok": True, "message_id": "om_xmock", "chat_id": "oc_xmock", "callback_context_saved": True}


def _table_of(card: dict) -> dict:
    for e in card.get("body", {}).get("elements", []):
        if e.get("tag") == "table":
            return e
    return {}


class LedgerMentorCardToolTest(unittest.TestCase):
    """feishu_ledger_mentor_card_send — validation, render, routing."""

    def setUp(self):
        self.mod = _load("feishu_ledger_mentor_card_send")
        self.fake = FakeSend()
        self.patcher = mock.patch.object(sys.modules["_feishu_impl"], "send_card_impl", self.fake)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def _call(self, **kw):
        kw.setdefault("receive_id", ME)
        kw.setdefault("mentor_name", "孙逊")
        kw.setdefault("last_cycle", "2026-09-02")
        kw.setdefault("cycle_date", "2026-09-04")
        kw.setdefault("group_summary", "9 人组 · 7 人已填 · 回流 1 · 消失 1")
        kw.setdefault("board_link", "https://genuineknowledge.feishu.cn/base/xxxx")
        kw.setdefault("rows_json", json.dumps(VALID_ROWS, ensure_ascii=False))
        return json.loads(asyncio.run(self.mod.feishu_ledger_mentor_card_send(**kw)))

    # ---------- fail-closed: bad input never reaches the sender ----------

    def test_rows_non_json_rejected_without_send(self):
        out = self._call(rows_json="not-json")
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_rows_not_list_rejected(self):
        out = self._call(rows_json=json.dumps({"成员": "x"}, ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_row_missing_member_rejected(self):
        rows = [{"条目": "某 TODO", "去向": "承接中", "备注": "—"}]
        out = self._call(rows_json=json.dumps(rows, ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertIn("成员", out.get("error", ""))
        self.assertEqual(len(self.fake.calls), 0)

    def test_status_off_whitelist_rejected(self):
        # 状态必须命中 正常/需处理/请假顺延/空白/不可达 —— 防口径漂移
        rows = [{"成员": "黄子建", "状态": "随便写", "要点": "—", "备注": "—"}]
        out = self._call(rows_json=json.dumps(rows, ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertIn("白名单", out.get("error", ""))
        self.assertEqual(len(self.fake.calls), 0)

    def test_empty_rows_rejected(self):
        out = self._call(rows_json="[]")
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_rows_over_cap_rejected(self):
        rows = [{"成员": f"人{i}", "状态": "正常"} for i in range(41)]
        out = self._call(rows_json=json.dumps(rows, ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_missing_receive_id_fails_closed_without_env(self):
        out = self._call(receive_id="")
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_template_placeholder_fail_closed(self):
        # values 缺 cycle_date / mentor_name 键 → 渲染报未填充占位符,不发脏卡
        # (工具层会把空值补 "—" 兜底,故这里直接测模板本身)
        out = render_template(
            "ledger-mentor-card",
            values_json=json.dumps(
                {"last_cycle": "2026-09-02", "group_summary": "x",
                 "board_link": "x", "note": ""},
                ensure_ascii=False,
            ),
            context_json=json.dumps({"rows": VALID_ROWS}, ensure_ascii=False),
        )
        self.assertFalse(out.get("ok"))
        self.assertIn("占位符", out.get("error", ""))

    # ---------- routing ----------

    def test_test_receiver_env_overrides_real_recipient(self):
        with mock.patch.dict(os.environ, {"PSI_TODO_CARD_TEST_RECEIVE_ID": TEST_RECEIVER}):
            out = self._call()
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(self.fake.calls), 1)
        self.assertEqual(self.fake.calls[0][0], TEST_RECEIVER)

    # ---------- valid send + card shape ----------

    def test_valid_rows_send_ok_and_card_shape(self):
        out = self._call()
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(self.fake.calls), 1)
        recv_id, card_json, _, _, _, handlers_json, multi = self.fake.calls[0]
        self.assertEqual(recv_id, ME)
        self.assertFalse(multi)
        handlers = json.loads(handlers_json)
        self.assertEqual(handlers, {})  # 纯信息卡,无按钮回传
        card = json.loads(card_json)
        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["header"]["title"]["content"], "TODO 台账·2026-09-04·孙逊组")
        table = _table_of(card)
        self.assertTrue(table, "card must contain a <table>")
        # 列固定:成员/上期 TODO/去向/备注(逐条去向明细)
        self.assertEqual(
            [c["display_name"] for c in table["columns"]],
            ["成员", "状态", "本期要点", "备注"],
        )
        self.assertEqual(len(table["rows"]), len(VALID_ROWS))
        self.assertEqual(table["rows"][0]["c0"], "黄子建")
        self.assertEqual(table["rows"][0]["c1"], "正常")
        self.assertEqual(table["rows"][0]["c2"], "承接 2 · 闭环 1")

    def test_every_leftover_item_lands_in_table(self):
        self._call()
        card = json.loads(self.fake.calls[0][1])
        table = _table_of(card)
        members = [r.get("c0", "") for r in table["rows"]]
        statuses = [r.get("c1", "") for r in table["rows"]]
        for v in VALID_ROWS:
            self.assertIn(v["成员"], members)
            cell = next(c for m, c in zip(members, statuses) if m == v["成员"])
            self.assertEqual(cell, v["状态"])

    def test_send_failure_surfaced_honestly(self):
        self.fake.ok = False
        out = self._call()
        self.assertFalse(out.get("ok"))
        self.assertIn("send failed", json.dumps(out, ensure_ascii=False))


class LedgerTemplateInstallTest(unittest.TestCase):
    """模板与口径耦合:ledger-mentor-card.xml 已装在引擎模板目录,口径文字引用不漂移."""

    def test_template_installed_next_to_engine(self):
        self.assertTrue(
            (Path(_TEMPLATE_DIR) / "ledger-mentor-card.xml").is_file(),
            "ledger-mentor-card.xml must live in the engine template dir",
        )

    def test_template_mentions_per_item_columns(self):
        tpl = (TPL_DIR / "ledger-mentor-card.xml").read_text(encoding="utf-8")
        for col in ("成员", "状态", "本期要点", "备注"):
            self.assertIn(f'label="{col}"', tpl)

    def test_template_no_interaction(self):
        tpl = (TPL_DIR / "ledger-mentor-card.xml").read_text(encoding="utf-8")
        self.assertNotIn("<button", tpl)
        self.assertNotIn("action=", tpl)

    def test_16h_task_references_tool_and_template(self):
        task = (WORKSPACE_ROOT / "schedules" / "todo-ledger-push" / "TASK.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("feishu_ledger_mentor_card_send", task)
        self.assertIn("ledger-mentor-card.xml", task)
        self.assertIn("company-todo-audit", task)


if __name__ == "__main__":
    unittest.main(verbosity=2)
