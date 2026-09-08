"""Strict tests for the v2 todo check cards (2026-09-06 new requirements).

Covers the two new card tools + templates + the authoritative five-point spec:

- ``feishu_todo_check_result_send`` (15:00 five-point result card, per person)
- ``feishu_mentor_check_card_send``    (15:10 mentor check card, alignment #4)

Requirement anchors (马晨柯 2026-09-06, two screenshots):
- Five check points come from ``config/todo-check-points.yaml`` (single source);
  card wording must not drift from it. #4 = 个人 TODO 与小组 TODO 对齐
  (stage 15:00 初判 + 15:10 mentor 复核), exactly the 第四条 the mentor card adds.
- Original text reports/reminders stay; the new cards are additive.
- Test routing: env ``PSI_TODO_CARD_TEST_RECEIVE_ID`` reroutes every card to the
  tester; read at call time so tests can toggle it dynamically.

Strictness properties asserted here:
1. fail-closed: bad rows_json / missing keys / >row cap never reach the sender;
2. template placeholder completeness — a missing key errors out instead of a dirty
   card (value literal ``{key}`` leaking into callbacks is how dirty ids get written);
3. card structure invariants: schema 2.0, table columns exactly as designed,
   no action buttons / empty handlers (pure info cards), multi_use=False;
4. empty-value info regression: mentor template has no ``<info value=""/>`` crash
   (engine rejects empty info values) — the tool must fill ``—`` / default board URL;
5. five-point coupling: result-template comment and the 15:00 TASK step both point
   at config/todo-check-points.yaml, so the five can't fork between files.

Run directly (no pytest needed):
    python3 tests/test_todo_check_cards.py
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
TESTS_DIR = Path(__file__).resolve().parent
CONFIG = WORKSPACE_ROOT / "config" / "todo-check-points.yaml"
TPL_DIR = WORKSPACE_ROOT / "skills" / "card-dsl" / "templates"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _card_dsl import _TEMPLATE_DIR, render_template  # noqa: E402

TEST_RECEIVER = "ou_test_receiver_123"
ME = "ou_e8db482d20ced3e5be710999548c8386"

VALID_FIVE = [
    {"检测点": "① 无过去时间点", "判定": "✅ 合规", "说明": "大目标 12 月、TODO1 最迟 9.6(未来)。"},
    {"检测点": "② 小目标粒度", "判定": "⚠️ 提示拆解", "说明": "小目标1 跨度超 1 周-1 月,建议拆子目标。"},
    {"检测点": "③ 说清用户价值", "判定": "✅ 合规", "说明": "写明为谁带来什么价值。"},
    {"检测点": "④ 个人 TODO 与小组 TODO 对齐", "判定": "✅ 有承接", "说明": "TODO1[9.6] 与 mentor 孙逊 ToDo1 双呼应。"},
    {"检测点": "⑤ 所有重要 TODO 都要列出来", "判定": "❓ 2 项存疑", "说明": "工作树 @你 3 项,2 项无承接。"},
]


def _load(mod: str):
    return importlib.import_module(mod)


def _table_of(card: dict) -> dict:
    for e in card["body"]["elements"]:
        if e.get("tag") == "table":
            return e
    raise AssertionError("card has no <table> element")


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


class TodoCheckCardsResultToolTest(unittest.TestCase):
    """feishu_todo_check_result_send — validation, render, routing."""

    def setUp(self):
        self.mod = _load("feishu_todo_check_result_send")
        self.fake = FakeSend()
        self.patcher = mock.patch.object(
            sys.modules["_feishu_impl"], "send_card_impl", self.fake
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def _call(self, **kw):
        kw.setdefault("receive_id", ME)
        kw.setdefault("person_name", "马晨柯")
        kw.setdefault("cycle_date", "2026-09-04")
        kw.setdefault("rows_json", json.dumps(VALID_FIVE, ensure_ascii=False))
        return json.loads(asyncio.run(self.mod.feishu_todo_check_result_send(**kw)))

    def test_valid_rows_send_ok_and_card_shape(self):
        out = self._call()
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(self.fake.calls), 1)
        recv_id, card_json, _, _, _, handlers_json, multi = self.fake.calls[0]
        self.assertEqual(recv_id, ME)
        card = json.loads(card_json)
        self.assertEqual(card["schema"], "2.0")
        self.assertEqual(card["header"]["title"]["content"], "TODO 五条检测·2026-09-04")
        tbl = _table_of(card)
        self.assertEqual([c["display_name"] for c in tbl["columns"]], ["检测点", "判定", "说明"])
        self.assertEqual(len(tbl["rows"]), 5)
        self.assertEqual(tbl["rows"][0]["c0"], "① 无过去时间点")
        self.assertEqual(json.loads(handlers_json), {})
        self.assertIs(multi, False)

    def test_rows_non_json_rejected_without_send(self):
        out = self._call(rows_json="not-json{{{")
        self.assertFalse(out.get("ok"))
        self.assertIn("rows_json", out.get("error", ""))
        self.assertEqual(len(self.fake.calls), 0)

    def test_rows_not_list_rejected(self):
        out = self._call(rows_json='"x"')
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_rows_over_cap_rejected(self):
        rows = [dict(VALID_FIVE[0]) for _ in range(11)]
        out = self._call(rows_json=json.dumps(rows, ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertIn("最多", out.get("error", ""))
        self.assertEqual(len(self.fake.calls), 0)

    def test_row_missing_point_or_verdict_rejected(self):
        out = self._call(rows_json=json.dumps([{"检测点": "① x"}], ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_row_not_dict_rejected(self):
        out = self._call(rows_json=json.dumps([1, 2], ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_missing_receive_id_fails_closed(self):
        os.environ.pop("PSI_TODO_CARD_TEST_RECEIVE_ID", None)
        out = self._call(receive_id="")
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_test_receiver_env_overrides_real_recipient(self):
        os.environ["PSI_TODO_CARD_TEST_RECEIVE_ID"] = TEST_RECEIVER
        out = self._call(receive_id=ME)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.fake.calls[0][0], TEST_RECEIVER)

    def test_env_unset_sends_real_recipient(self):
        os.environ.pop("PSI_TODO_CARD_TEST_RECEIVE_ID", None)
        out = self._call(receive_id=ME)
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.fake.calls[0][0], ME)

    def test_note_rendered_only_when_present(self):
        out = self._call(note="8.17-8.27 事假,当期免填")
        self.assertTrue(out.get("ok"), out)
        body = json.loads(self.fake.calls[0][1])["body"]["elements"]
        marks = [e["content"] for e in body if e["tag"] == "markdown"]
        self.assertTrue(any(m.startswith("**状态**") and "事假" in m for m in marks))
        self.fake.calls.clear()
        out = self._call(note="")
        body = json.loads(self.fake.calls[0][1])["body"]["elements"]
        marks = [e["content"] for e in body if e["tag"] == "markdown"]
        self.assertFalse(any(m.startswith("**状态**") for m in marks))

    def test_send_failure_surfaced_honestly(self):
        self.fake.ok = False
        out = self._call()
        self.assertFalse(out.get("ok"))
        self.assertIn("send failed", out.get("message", ""))


class TodoCheckCardsMentorToolTest(unittest.TestCase):
    """feishu_mentor_check_card_send — validation, defaults, alignment #4."""

    def setUp(self):
        self.mod = _load("feishu_mentor_check_card_send")
        self.fake = FakeSend()
        self.patcher = mock.patch.object(
            sys.modules["_feishu_impl"], "send_card_impl", self.fake
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def _call(self, **kw):
        kw.setdefault("receive_id", ME)
        kw.setdefault("mentor_name", "孙逊")
        kw.setdefault("cycle_date", "2026-09-04")
        kw.setdefault("filled_summary", "7/9")
        kw.setdefault("missing_summary", "2 人")
        kw.setdefault(
            "rows_json",
            json.dumps(
                [{"成员": "马晨柯", "判定速览": "①✅ ②⚠️ ③✅ ④❓ ⑤❓",
                  "对齐依据": "承接孙逊当期 AI 原生化 todo-list SOP",
                  "待确认": "⑤ 全覆盖 2 项存疑"}],
                ensure_ascii=False,
            ),
        )
        return json.loads(asyncio.run(self.mod.feishu_mentor_check_card_send(**kw)))

    def test_valid_send_ok_card_has_four_columns_with_alignment(self):
        out = self._call()
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(len(self.fake.calls), 1)
        recv_id, card_json, _, _, _, handlers_json, multi = self.fake.calls[0]
        self.assertEqual(recv_id, ME)
        card = json.loads(card_json)
        self.assertEqual(card["schema"], "2.0")
        tbl = _table_of(card)
        labels = [c["display_name"] for c in tbl["columns"]]
        self.assertEqual(labels, ["成员", "①②③④⑤", "④ 对齐依据", "需你人工确认"])
        self.assertEqual(len(tbl["rows"]), 1)
        self.assertIn("④", tbl["rows"][0]["c1"])  # 判定速览含第四条
        self.assertEqual(json.loads(handlers_json), {})
        self.assertIs(multi, False)

    def test_member_row_without_name_rejected(self):
        out = self._call(rows_json=json.dumps([{"判定速览": "①✅"}], ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertEqual(len(self.fake.calls), 0)

    def test_over_ten_members_rejected_not_truncated(self):
        rows = [{"成员": f"成员{i}", "判定速览": "①✅"} for i in range(11)]
        out = self._call(rows_json=json.dumps(rows, ensure_ascii=False))
        self.assertFalse(out.get("ok"))
        self.assertIn("10", out.get("error", ""))
        self.assertEqual(len(self.fake.calls), 0)

    def test_default_board_link_used_when_param_empty(self):
        out = self._call(board_link="")
        self.assertTrue(out.get("ok"), out)
        body = json.loads(self.fake.calls[0][1])["body"]["elements"]
        marks = [e["content"] for e in body if e["tag"] == "markdown"]
        self.assertTrue(any("打开看板" in m and "H6icwLWn1iwpXAk73QMcA6MgnWc" in m for m in marks))

    def test_empty_summaries_do_not_crash_info_render(self):
        # 回归:引擎对 value="" 的 <info> 会报错;工具必须兜底成 "—"。
        out = self._call(filled_summary="", missing_summary="")
        self.assertTrue(out.get("ok"), out)

    def test_test_receiver_env_overrides_mentor(self):
        os.environ["PSI_TODO_CARD_TEST_RECEIVE_ID"] = TEST_RECEIVER
        out = self._call(receive_id="ou_real_mentor")
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(self.fake.calls[0][0], TEST_RECEIVER)


class FivePointSpecCouplingTest(unittest.TestCase):
    """五条检测点 单一事实源:config 与模板/定时不得各自漂移."""

    def test_config_exists_and_lists_five_points_with_alignment_fourth(self):
        self.assertTrue(CONFIG.is_file(), "config/todo-check-points.yaml missing")
        text = CONFIG.read_text(encoding="utf-8")
        for i in range(1, 6):
            self.assertIn(f"- id: {i}", text)
        self.assertIn("个人 TODO 与小组 TODO 对齐", text)
        self.assertIn('stage: "15:00 + 15:10"', text)

    def test_result_template_and_15h00_task_point_at_the_config(self):
        tpl = (TPL_DIR / "todo-check-result.xml").read_text(encoding="utf-8")
        self.assertIn("config/todo-check-points.yaml", tpl)
        self.assertIn("个人 TODO 与小组 TODO 对齐", tpl)
        task = (WORKSPACE_ROOT / "schedules" / "todo-writing-check" / "TASK.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("config/todo-check-points.yaml", task)

    def test_both_new_templates_installed_next_to_engine(self):
        for name in ("todo-check-result.xml", "mentor-check-card.xml"):
            self.assertTrue(
                (Path(_TEMPLATE_DIR) / name).is_file(),
                f"template {name} must live in the engine template dir {_TEMPLATE_DIR}",
            )

    def test_result_template_placeholder_fail_closed(self):
        # 缺 person_name → 未填充占位符报错,不发脏卡(值里残留 {person_name} 会写脏)。
        out = render_template(
            "todo-check-result",
            values_json=json.dumps({"cycle_date": "d"}, ensure_ascii=False),
            context_json=json.dumps({"rows": VALID_FIVE}, ensure_ascii=False),
        )
        self.assertFalse(out.get("ok"))
        self.assertIn("未填充的占位符", out.get("error", ""))

    def test_original_schedule_steps_still_present(self):
        # 原版保留:15:00 原违规私聊报告与 15:10 原文本提醒仍在 TASK.md 中。
        task = (WORKSPACE_ROOT / "schedules" / "todo-writing-check" / "TASK.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("私聊本人**一次**", task)
        # v0.3:原 15:10 mentor-check-remind 独立定时已停用并入 16:00 todo-ledger-push,
        # 故「原版保留」断言指向 16:00 合并版任务文件(见 schedules/todo-ledger-push/TASK.md)。
        m_task = (WORKSPACE_ROOT / "schedules" / "todo-ledger-push" / "TASK.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("并入本任务", m_task)
        self.assertIn("mentor 检查提醒卡", m_task)


if __name__ == "__main__":
    unittest.main(verbosity=2)
