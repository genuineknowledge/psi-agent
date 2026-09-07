from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

# ruff: noqa: RUF001

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _positive_negative_list.models import (  # noqa: E402  # ty: ignore[unresolved-import]
    CaseDraft,
    LedgerQuery,
    LedgerRecord,
)
from _positive_negative_list.validation import validate_case  # noqa: E402  # ty: ignore[unresolved-import]


def _negative_case() -> CaseDraft:
    return CaseDraft.from_mapping(
        {
            "writer_user_key": "ou_subject",
            "reporter_user_key": "ou_reporter",
            "subject_user_key": "ou_subject",
            "occurred_at": "2026-09-01",
            "observed_behavior": "已知延期后未同步风险",
            "context": "项目交付",
            "impact": "上下游等待",
            "evidence_sources": ["聊天记录"],
            "nature": "negative",
            "category": "迅速行动、及时反馈",
            "primary_rule_id": "pn-test-negative",
            "secondary_rule_ids": [],
            "rule_version": "6.0-shadow",
            "fact_summary": "XXX 已知延期后未及时同步, 导致上下游等待。",
            "agent_inference": "根据已提供聊天记录判断。",
            "correct_behavior": "发现延期风险后立即同步现状、影响和新时间。",
            "immediate_remedy": "现在补充同步并明确补救负责人和时间。",
            "prevention": "设置里程碑风险反馈节点。",
            "workflow": "ready_for_confirmation",
            "case_id": "case_test",
        }
    )


@pytest.fixture()
def feishu_network(monkeypatch):
    feishu = importlib.import_module("_feishu_impl")
    calls = {"send": [], "edit": []}
    counter = {"value": 0}

    async def fake_send_card(
        receive_id,
        card_json,
        receive_id_type,
        user_key=None,
        business_context_json="{}",
        action_handlers_json="{}",
        multi_use=False,
        **_kwargs,
    ):
        counter["value"] += 1
        message_id = f"om_candidate_{counter['value']}"
        calls["send"].append(
            {
                "message_id": message_id,
                "receive_id": receive_id,
                "card": json.loads(card_json),
                "action_handlers": json.loads(action_handlers_json or "{}"),
                "multi_use": multi_use,
            }
        )
        return {"ok": True, "message_id": message_id}

    async def fake_edit_card(message_id, card_json, user_key=""):
        calls["edit"].append({"message_id": message_id, "card": json.loads(card_json)})
        return {"ok": True}

    monkeypatch.setattr(feishu, "send_card_impl", fake_send_card)
    monkeypatch.setattr(feishu, "edit_card_impl", fake_edit_card)
    return calls


def test_bitable_role_permission_denial_is_not_reported_as_empty_success() -> None:
    feishu = importlib.import_module("_feishu_impl")
    response = SimpleNamespace(
        code=0,
        msg="RolePermNotAllow",
        raw=SimpleNamespace(content=b'{"code":0,"msg":"RolePermNotAllow","data":{}}', status_code=200),
    )

    result = feishu._resp_to_result(response)

    assert result["ok"] is False
    assert result["code"] == 0
    assert "permission" in result["message"].lower()


def test_public_source_reader_requests_only_columns_known_to_the_table(monkeypatch) -> None:
    reader = importlib.import_module("_positive_negative_list.reader")
    calls: list[dict[str, Any]] = []

    async def fake_search(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "records": [], "has_more": False, "page_token": ""}

    monkeypatch.setattr(reader._f, "search_bitable_records_impl", fake_search)
    client = reader.FeishuLedgerClient(
        "app",
        "table",
        {
            "case_id": "记录ID",
            "nature": "正负面归属",
            "subject_user_key": "员工姓名",
            "reporter_user_key": "填写人",
            "occurred_at": "记录日期",
            "fact_summary": "事件描述",
        },
        strict_field_names=True,
    )

    asyncio.run(client.list_records(LedgerQuery(), "ou_reader"))
    requested = json.loads(calls[0]["field_names"])
    assert requested == ["员工姓名", "填写人", "正负面归属", "记录日期", "事件描述"]


def test_feishu_rich_text_is_normalized_for_analysis() -> None:
    record = LedgerRecord.from_mapping(
        {
            "record_id": "rec_1",
            "fields": {
                "事件描述": [{"text": "未更新 TODO", "type": "text"}],
                "正负面归属": "负面清单",
                "员工姓名": [{"id": "ou_subject", "name": "XXX"}],
                "记录日期": 1786896000000,
                "填写人": [{"id": "ou_reporter", "name": "报告人"}],
            },
        },
        field_names={
            "case_id": "记录ID",
            "nature": "正负面归属",
            "subject_user_key": "员工姓名",
            "reporter_user_key": "填写人",
            "occurred_at": "记录日期",
            "fact_summary": "事件描述",
        },
    )
    assert record.fact_summary == "未更新 TODO"
    assert record.subject_user_key == "ou_subject"
    assert record.nature == "negative"


def test_reader_does_not_return_verbose_raw_feishu_fields() -> None:
    reader = importlib.import_module("_positive_negative_list.reader")

    class FakeClient:
        field_names: ClassVar[dict[str, str]] = {
            "nature": "正负面归属",
            "subject_user_key": "员工姓名",
            "reporter_user_key": "填写人",
            "occurred_at": "记录日期",
            "fact_summary": "事件描述",
        }

        async def list_records(self, query, user_key):
            return {
                "ok": True,
                "records": [
                    {
                        "record_id": "rec_1",
                        "fields": {
                            "事件描述": [{"text": "未更新 TODO", "type": "text"}],
                            "正负面归属": "负面清单",
                            "员工姓名": [
                                {
                                    "id": "ou_subject",
                                    "name": "XXX",
                                    "avatar_url": "https://example.invalid/avatar",
                                    "email": "subject@example.invalid",
                                }
                            ],
                            "记录日期": 1786896000000,
                            "填写人": [{"id": "ou_reporter", "name": "报告人"}],
                        },
                    }
                ],
                "has_more": False,
                "page_token": "",
            }

    result = asyncio.run(reader.read_records(FakeClient(), LedgerQuery(), "ou_reader"))

    assert result["ok"] is True
    record = result["records"][0]
    assert record["subject_user_key"] == "ou_subject"
    assert record["fact_summary"] == "未更新 TODO"
    assert "fields" not in record
    assert "avatar_url" not in json.dumps(result, ensure_ascii=False)
    assert "subject@example.invalid" not in json.dumps(result, ensure_ascii=False)


def test_analyze_tool_returns_user_facing_summary_without_internal_metadata(monkeypatch) -> None:
    analyze = importlib.import_module("positive_negative_case_analyze")

    class FakeAdapter:
        async def list_records(self, query, user_key):
            return {
                "ok": True,
                "records": [
                    {
                        "record_id": "rec_1",
                        "fields": {
                            "事件描述": "未及时同步延期风险",
                            "正负面归属": "负面清单",
                            "员工姓名": "XXX",
                            "记录日期": "2026-08-28",
                            "填写人": "报告人",
                        },
                    },
                    {
                        "record_id": "rec_2",
                        "fields": {
                            "事件描述": "主动补位完成闭环",
                            "正负面归属": "正面清单",
                            "员工姓名": "XXX",
                            "记录日期": "2026-08-29",
                            "填写人": "报告人",
                        },
                    },
                ],
                "has_more": False,
                "page_token": "",
            }

    monkeypatch.setattr(analyze.runtime, "configured_read_table_adapter", lambda: FakeAdapter())
    payload = json.loads(asyncio.run(analyze.positive_negative_case_analyze(user_key="ou_reader")))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    assert payload["摘要"]["记录总数"] == 2
    assert payload["摘要"]["正面记录数"] == 1
    assert payload["摘要"]["负面记录数"] == 1
    assert payload["摘要"]["证据来源未填写"] == 2
    assert payload["摘要"]["负面记录待复盘"] == 1
    assert "evidence_sources" not in serialized
    assert "review_status" not in serialized
    assert "has_more" not in serialized
    assert "page_token" not in serialized
    assert "6.0-shadow" not in serialized
    assert "pn-" not in serialized


def test_read_tool_returns_chinese_record_fields_without_internal_metadata(monkeypatch) -> None:
    read = importlib.import_module("positive_negative_case_read")

    class FakeClient:
        field_names: ClassVar[dict[str, str]] = {
            "nature": "正负面归属",
            "subject_user_key": "员工姓名",
            "reporter_user_key": "填写人",
            "occurred_at": "记录日期",
            "fact_summary": "事件描述",
        }

        async def list_records(self, query, user_key):
            return {
                "ok": True,
                "records": [
                    {
                        "record_id": "rec_1",
                        "fields": {
                            "事件描述": "未及时同步延期风险",
                            "正负面归属": "负面清单",
                            "员工姓名": "XXX",
                            "记录日期": "2026-08-28",
                            "填写人": "报告人",
                        },
                    }
                ],
                "has_more": False,
                "page_token": "",
            }

    class FakeAdapter:
        _client = FakeClient()

    monkeypatch.setattr(read.runtime, "configured_read_table_adapter", lambda: FakeAdapter())
    payload = json.loads(asyncio.run(read.positive_negative_case_read(user_key="ou_reader")))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["ok"] is True
    record = payload["记录"][0]
    assert record["记录编号"] == "rec_1"
    assert record["行为性质"] == "负面清单"
    assert "evidence_sources" not in serialized
    assert "review_status" not in serialized
    assert "has_more" not in serialized
    assert "page_token" not in serialized
    assert "case_id" not in serialized


def test_official_write_target_preflight_uses_public_ledger_view_without_extra_config(monkeypatch) -> None:
    runtime = importlib.import_module("_positive_negative_list.runtime")
    config = {
        "write_target": {
            "mode": "existing_columns",
            "field_names": dict(runtime._LEDGER_FIELD_NAMES),
        }
    }
    client = runtime.ConfiguredTableClient(runtime._SOURCE_APP_TOKEN, runtime._SOURCE_TABLE_ID, config)

    async def fake_list_fields(*args, **kwargs):
        type_by_name = {
            "事件描述": 1,
            "正负面归属": 3,
            "员工姓名": 11,
            "记录日期": 5,
            "备注": 1,
            "填写人": 11,
        }
        return {
            "ok": True,
            "fields": [
                {
                    "field_id": f"field_{index}",
                    "name": name,
                    "type": field_type,
                    "property": (
                        {"options": [{"name": option} for option in ("正面清单", "负面清单", "中性", "证据不足")]}
                        if name == "正负面归属"
                        else {}
                    ),
                }
                for index, (name, field_type) in enumerate(type_by_name.items())
            ],
        }

    monkeypatch.setattr(runtime._f, "list_bitable_fields_impl", fake_list_fields)
    result = asyncio.run(client.preflight("ou_writer"))
    assert result.ok is True
    assert result.schema is not None
    assert result.schema.view_purposes == {runtime._SOURCE_VIEW_ID: "public_ledger"}


def test_writer_may_be_subject_when_reporter_is_a_different_person() -> None:
    assert validate_case(_negative_case()) == ()


def test_self_reported_case_is_valid_for_private_chat() -> None:
    case = CaseDraft.from_mapping(
        _negative_case().to_mapping()
        | {
            "reporter_user_key": "ou_subject",
            "writer_user_key": "ou_subject",
        }
    )

    assert validate_case(case) == ()


def test_negative_case_notice_contains_guidance() -> None:
    notifications = importlib.import_module("_positive_negative_list.notifications")
    case = _negative_case()

    notice = notifications._notice_text(case, "https://feishu.cn/base/test")
    assert "客观原因" in notice
    assert "补足动作" in notice
    assert "防止再犯" in notice
    assert "不写回表格" in notice
    assert "行为性质：负面行为" in notice


def test_confirmation_card_uses_display_name_but_keeps_open_id_in_case(monkeypatch) -> None:
    positive_negative = importlib.import_module("positive_negative_list")

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        assert user_ids == "ou_subject"
        assert user_id_type == "open_id"
        return {"ok": True, "users": [{"open_id": "ou_subject", "name": "王炜博"}]}

    monkeypatch.setattr(positive_negative._f, "get_users_batch_impl", fake_get_users_batch)
    case = _negative_case()

    card = asyncio.run(positive_negative._confirmation_card(case, "digest_test"))
    content = card["body"]["elements"][0]["content"]

    assert card["schema"] == "2.0"
    assert all(element.get("tag") != "action" for element in card["body"]["elements"])
    assert "behaviors" in json.dumps(card, ensure_ascii=False)
    assert "对象**　王炜博" in content
    assert "ou_subject" not in content
    assert case.subject_user_key == "ou_subject"


def test_prepare_result_preview_uses_people_names_without_changing_internal_draft(monkeypatch, tmp_path) -> None:
    positive_negative = importlib.import_module("positive_negative_list")
    case = _negative_case()

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        assert user_id_type == "open_id"
        return {
            "ok": True,
            "users": [
                {"open_id": "ou_subject", "name": "王炜博"},
                {"open_id": "ou_reporter", "name": "罗霖"},
            ],
        }

    async def fake_send_card(*args, **kwargs):
        return {"ok": True, "message_id": "msg_card"}

    monkeypatch.setattr(positive_negative._f, "get_users_batch_impl", fake_get_users_batch)
    monkeypatch.setattr(positive_negative._f, "send_card_impl", fake_send_card)

    async def fake_resolve_appdata_root():
        return tmp_path

    monkeypatch.setattr(positive_negative, "_resolve_appdata_root", fake_resolve_appdata_root)
    monkeypatch.setattr(positive_negative, "_get_session_id", lambda: "session_test")

    payload = json.loads(
        asyncio.run(
            positive_negative.positive_negative_case_prepare(
                json.dumps(case.to_mapping(), ensure_ascii=False),
                source_event_id="evt_preview_names",
                user_key="ou_subject",
            )
        )
    )

    assert payload["preview"]["涉事人"] == "王炜博"
    assert payload["preview"]["报告人"] == "罗霖"
    assert payload["preview"]["写入者"] == "王炜博"
    assert "ou_" not in json.dumps(payload["preview"], ensure_ascii=False)


def test_people_display_keeps_names_and_never_falls_back_to_open_id(monkeypatch) -> None:
    display = importlib.import_module("_assignment_display")

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        assert user_ids == "ou_known,ou_unknown"
        return {"ok": True, "users": [{"open_id": "ou_known", "name": "王炜博"}]}

    actual = asyncio.run(display.resolve_people_display("已有姓名,ou_known，ou_unknown", fake_get_users_batch))

    assert actual == "已有姓名、王炜博、姓名未解析"
    assert "ou_" not in actual


def test_public_read_projection_resolves_people_to_display_names(monkeypatch) -> None:
    reader = importlib.import_module("_positive_negative_list.reader")

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        assert user_id_type == "open_id"
        return {
            "ok": True,
            "users": [
                {"open_id": "ou_subject", "name": "王炜博"},
                {"open_id": "ou_reporter", "name": "罗霖"},
            ],
        }

    result = {
        "ok": True,
        "records": [
            LedgerRecord.from_mapping(
                {
                    "record_id": "rec_1",
                    "subject_user_key": "ou_subject",
                    "reporter_user_key": "ou_reporter",
                    "occurred_at": "2026-09-01",
                    "nature": "negative",
                    "category": "工作方式方法",
                    "fact_summary": "未形成闭环",
                }
            ).to_mapping()
        ],
        "has_more": False,
    }
    monkeypatch.setattr(reader._f, "get_users_batch_impl", fake_get_users_batch)

    public = asyncio.run(reader.public_result_with_names(result))

    record = public["记录"][0]
    assert record["涉事人"] == "王炜博"
    assert record["报告人"] == "罗霖"
    assert "ou_" not in json.dumps(public, ensure_ascii=False)


def test_record_notice_resolves_subject_name(monkeypatch) -> None:
    notifications = importlib.import_module("_positive_negative_list.notifications")
    case = _negative_case()

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        return {"ok": True, "users": [{"open_id": "ou_subject", "name": "王炜博"}]}

    monkeypatch.setattr(notifications._feishu_impl, "get_users_batch_impl", fake_get_users_batch)
    text = asyncio.run(notifications.notice_text_with_names(case, "rec_1"))

    assert "涉事人：王炜博" in text
    assert "ou_subject" not in text


def test_record_notice_cards_use_card_2_grammar_and_only_negative_has_review_button() -> None:
    notifications = importlib.import_module("_positive_negative_list.notifications")
    negative = LedgerRecord.from_mapping(
        {
            "record_id": "rec_negative",
            "subject_user_key": "ou_subject",
            "reporter_user_key": "ou_reporter",
            "occurred_at": "2026-09-01",
            "nature": "negative",
            "category": "工作方式方法",
            "fact_summary": "方案确定后未倒排，导致任务未闭环",
            "correct_behavior": "先倒排节点并明确交付物",
            "immediate_remedy": "补齐节点并同步上下游",
            "prevention": "开工前检查倒排表",
        }
    )
    positive = LedgerRecord.from_mapping(negative.to_mapping() | {"record_id": "rec_positive", "nature": "positive"})

    negative_card = notifications.render_record_notice_card(negative, "王炜博")
    positive_card = notifications.render_record_notice_card(positive, "王炜博")
    negative_json = json.dumps(negative_card, ensure_ascii=False)
    positive_json = json.dumps(positive_card, ensure_ascii=False)

    assert negative_card["schema"] == "2.0"
    assert all(element.get("tag") != "action" for element in negative_card["body"]["elements"])
    assert negative_json.count("pn_record_review_start") == 1
    assert "开始复盘" in negative_json
    assert "开始复盘" not in positive_json


def test_remind_result_resolves_subject_name(monkeypatch, tmp_path) -> None:
    remind = importlib.import_module("positive_negative_case_remind")
    notifications = importlib.import_module("_positive_negative_list.notifications")
    case = LedgerRecord.from_mapping(
        {
            "record_id": "rec_1",
            "subject_user_key": "ou_subject",
            "reporter_user_key": "ou_reporter",
            "occurred_at": "2026-09-01",
            "nature": "positive",
            "category": "组织向心力",
            "fact_summary": "主动同步信息",
        }
    )

    async def fake_send(*args, **kwargs):
        return {"ok": True, "message_id": "msg_1"}

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        return {"ok": True, "users": [{"open_id": "ou_subject", "name": "王炜博"}]}

    monkeypatch.setattr(notifications, "send_card_impl", fake_send)
    monkeypatch.setattr(notifications._feishu_impl, "get_users_batch_impl", fake_get_users_batch)

    async def fake_resolve_appdata_root():
        return tmp_path

    monkeypatch.setattr(remind, "_resolve_appdata_root", fake_resolve_appdata_root)

    payload = json.loads(
        asyncio.run(
            remind.positive_negative_case_remind(
                record_json=json.dumps(case.to_mapping(), ensure_ascii=False),
                user_key="ou_reporter",
            )
        )
    )

    assert payload["涉事人"] == "王炜博"
    assert "ou_subject" not in json.dumps(payload, ensure_ascii=False)


def test_record_notice_card_callback_starts_private_review_idempotently(monkeypatch, tmp_path) -> None:
    remind = importlib.import_module("positive_negative_case_remind")
    review_tool = importlib.import_module("positive_negative_case_review")
    record = LedgerRecord.from_mapping(
        {
            "record_id": "rec_review",
            "subject_user_key": "ou_subject",
            "reporter_user_key": "ou_reporter",
            "occurred_at": "2026-09-01",
            "nature": "negative",
            "category": "工作方式方法",
            "fact_summary": "方案确定后未倒排，导致任务未闭环",
        }
    )

    sent: list[str] = []

    async def fake_send(receive_id, text, receive_id_type):
        sent.append(receive_id)
        return {"ok": True, "message_id": "msg_review"}

    async def fake_root():
        return tmp_path

    def fail_if_table_is_read():
        raise AssertionError("notice callback must not read the formal table")

    monkeypatch.setattr(review_tool, "configured_table_adapter", fail_if_table_is_read)
    monkeypatch.setattr(review_tool, "_resolve_appdata_root", fake_root)
    monkeypatch.setattr(review_tool.reviews, "send_message_impl", fake_send)
    monkeypatch.setattr(remind, "_resolve_appdata_root", fake_root)
    callback = json.dumps(
        {
            "action": {
                "value": {
                    "action": "pn_record_review_start",
                    "record_id": "rec_review",
                    "subject_user_key": "ou_subject",
                    "record": record.to_mapping(),
                }
            },
            "operator": {"open_id": "ou_subject"},
        },
        ensure_ascii=False,
    )

    first = json.loads(
        asyncio.run(remind.positive_negative_case_remind(card_action_json=callback, user_key="ou_subject"))
    )
    second = json.loads(
        asyncio.run(remind.positive_negative_case_remind(card_action_json=callback, user_key="ou_subject"))
    )
    assert first["status"] == "review_started"
    assert second["status"] == "review_already_started"
    assert sent == ["ou_subject"]


def test_failed_review_prompt_does_not_leave_an_active_draft(monkeypatch, tmp_path) -> None:
    review_tool = importlib.import_module("positive_negative_case_review")
    record = LedgerRecord.from_mapping(
        {
            "record_id": "rec_retry",
            "subject_user_key": "ou_subject",
            "reporter_user_key": "ou_reporter",
            "occurred_at": "2026-09-01",
            "nature": "negative",
            "category": "工作方式方法",
            "fact_summary": "方案确定后未倒排，导致任务未闭环",
        }
    )
    attempts = 0

    async def fake_send(receive_id, text, receive_id_type):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"ok": False, "message": "temporary failure"}
        return {"ok": True, "message_id": "msg_retry"}

    async def fake_root():
        return tmp_path

    monkeypatch.setattr(review_tool, "_resolve_appdata_root", fake_root)
    monkeypatch.setattr(review_tool.reviews, "send_message_impl", fake_send)
    kwargs = {
        "record_json": json.dumps(record.to_mapping(), ensure_ascii=False),
        "user_key": "ou_subject",
    }

    first = json.loads(asyncio.run(review_tool.positive_negative_case_review_start(**kwargs)))
    second = json.loads(asyncio.run(review_tool.positive_negative_case_review_start(**kwargs)))

    assert first["status"] == "review_notification_failed"
    assert second["status"] == "review_started"
    assert attempts == 2


def test_prepare_error_lists_legal_case_field_names(monkeypatch) -> None:
    positive_negative = importlib.import_module("positive_negative_list")

    result = asyncio.run(
        positive_negative.positive_negative_case_prepare(
            json.dumps({"员工姓名": "王炜博", "行为事实": "未及时同步"}),
            user_key="ou_writer",
        )
    )
    payload = json.loads(result)

    assert payload["ok"] is False
    assert "allowed_case_fields" in payload
    assert "subject_user_key" in payload["allowed_case_fields"]
    assert "fact_summary" in payload["allowed_case_fields"]


def test_write_target_is_the_formal_ledger_with_six_column_mapping() -> None:
    runtime = importlib.import_module("_positive_negative_list.runtime")

    assert runtime._target_coordinates({}, "write") == (
        runtime._SOURCE_APP_TOKEN,
        runtime._SOURCE_TABLE_ID,
        runtime._SOURCE_VIEW_ID,
    )
    assert runtime._LEDGER_FIELD_NAMES == {
        "nature": "正负面归属",
        "subject_user_key": "员工姓名",
        "fact_summary": "事件描述",
        "occurred_at": "记录日期",
        "note": "备注",
        "reporter_user_key": "填写人",
    }


def test_existing_columns_store_human_note_and_formal_nature_label() -> None:
    runtime = importlib.import_module("_positive_negative_list.runtime")
    preflight = importlib.import_module("_positive_negative_list.preflight")
    case = _negative_case()
    fields = [
        {"field_id": "f_desc", "field_name": "事件描述", "type": 1},
        {
            "field_id": "f_nature",
            "field_name": "正负面归属",
            "type": 3,
            "property": {"options": [{"name": "正面清单"}, {"name": "负面清单"}]},
        },
        {"field_id": "f_subject", "field_name": "员工姓名", "type": 11},
        {"field_id": "f_date", "field_name": "记录日期", "type": 5},
        {"field_id": "f_note", "field_name": "备注", "type": 1},
        {"field_id": "f_reporter", "field_name": "填写人", "type": 11},
    ]
    client = runtime.ConfiguredTableClient(
        "app_test",
        "table_test",
        {"write_target": {"mode": "existing_columns", "field_names": runtime._LEDGER_FIELD_NAMES}},
    )
    schema_result = preflight.validate_table_schema(
        fields,
        {
            "nature": {"field_id": "f_nature", "field_name": "正负面归属", "type": 3},
            "subject_user_key": {"field_id": "f_subject", "field_name": "员工姓名", "type": 11},
            "fact_summary": {"field_id": "f_desc", "field_name": "事件描述", "type": 1},
            "occurred_at": {"field_id": "f_date", "field_name": "记录日期", "type": 5},
            "reporter_user_key": {"field_id": "f_reporter", "field_name": "填写人", "type": 11},
            "source_key": {"field_id": "f_note", "field_name": "备注", "type": 1},
            "canonical_incident_id": {"field_id": "f_note", "field_name": "备注", "type": 1},
            "cross_source_fingerprint": {"field_id": "f_note", "field_name": "备注", "type": 1},
        },
        {"nature": {"field_id": "f_nature", "options": frozenset({"正面清单", "负面清单"})}},
        app_token="app_test",
        target_table_id="table_test",
        candidate_table_ids=("table_test",),
        view_purposes={"table_test": "public_ledger"},
        can_create_records=True,
        notification_user_key="ou_writer",
        notification_identity_provenance="trusted_feishu_context",
        allow_deduplication_aliases=True,
    )
    assert schema_result.ok is True
    encoded = client.build_existing_case_fields(case, schema_result.schema)

    assert encoded["f_desc"] == case.fact_summary
    assert encoded["f_nature"] == "负面清单"
    assert "分类：" in encoded["f_note"]
    assert "正确做法：" in encoded["f_note"]
    assert case.cross_source_fingerprint in encoded["f_note"]


def test_candidate_event_merge_keeps_one_event_package_and_all_source_sentences() -> None:
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    batch = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[
            {"text": "方案确定后直接开工，没有倒排节点", "context": "项目启动"},
            {"text": "做到中途才发现上下游都在等", "context": "项目启动"},
        ],
        source_key="meeting:2026-09-06:am",
    )

    merged = batches.merge_candidate(batch, 1, 0)

    assert len([row for row in merged["rows"] if row["status"] != "merged"]) == 1
    target = merged["rows"][0]
    assert target["status"] == "pending"
    assert target["source_candidates"] == [
        "方案确定后直接开工，没有倒排节点",
        "做到中途才发现上下游都在等",
    ]
    assert "直接开工" in target["text"] and "上下游都在等" in target["text"]
    assert merged["rows"][1]["status"] == "merged"


def test_evaluative_candidate_requires_observable_behavior() -> None:
    batches = importlib.import_module("_positive_negative_list.candidate_batches")

    quality = batches.assess_candidate_quality("XXX学习能力强、靠谱")

    assert quality["status"] == "needs_observable_behavior"
    assert "行为" in quality["reason"]


def test_candidate_batch_source_key_is_idempotent(tmp_path, monkeypatch) -> None:
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    monkeypatch.setattr(batches, "_resolve_appdata_root", lambda: tmp_path)

    first = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[{"text": "未同步风险"}],
        source_key="meeting:2026-09-06:am",
    )
    second = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[{"text": "未同步风险"}],
        source_key="meeting:2026-09-06:am",
    )

    asyncio.run(batches.save_batch(first))
    duplicate = asyncio.run(batches.find_batch_by_source_key("meeting:2026-09-06:am"))

    assert duplicate is not None
    assert duplicate["batch_id"] == first["batch_id"]
    assert second["batch_id"] != first["batch_id"]


def test_candidate_card_is_organize_only_and_never_offers_direct_record_action(feishu_network) -> None:
    card_tool = importlib.import_module("positive_negative_candidate_card")
    payload = asyncio.run(
        card_tool.positive_negative_candidate_card(
            receive_id="ou_subject",
            person_name="王炜博",
            source_label="会议纪要",
            meeting_date="2026-09-06",
            candidates_json=json.dumps(
                [
                    {"text": "方案确定后直接开工，没有倒排节点", "context": "项目启动"},
                    {"text": "做到中途才发现上下游都在等", "context": "项目启动"},
                ],
                ensure_ascii=False,
            ),
            source_key="meeting:2026-09-06:am",
            user_key="ou_subject",
        )
    )
    result = json.loads(payload)
    assert result["ok"] is True
    card = feishu_network["send"][-1]["card"]
    rendered = json.dumps(card, ensure_ascii=False)
    assert "确认记录" not in rendered
    assert "直接写入" not in rendered
    assert "纳入候选" in rendered
    assert "暂时忽略" in rendered
    assert "合并到" not in rendered
    assert "补充证据" not in rendered
    assert not (Path(os.environ["PSI_APPDATA"]) / "positive-negative-list" / "records.json").exists()


def test_candidate_card_keep_and_legacy_merge_is_rejected_without_writing(feishu_network) -> None:
    card_tool = importlib.import_module("positive_negative_candidate_card")
    sent = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                receive_id="ou_subject",
                person_name="王炜博",
                candidates_json=json.dumps(["未及时同步风险", "上下游一直等待"], ensure_ascii=False),
                source_key="meeting:2026-09-06:pm",
                user_key="ou_subject",
            )
        )
    )

    kept = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                card_action_json=json.dumps(
                    {
                        "action": {"value": {"action": "pn_candidate_keep_0", "batch_id": sent["batch_id"]}},
                        "message_id": sent["message_id"],
                        "operator": {"open_id": "ou_subject"},
                    },
                    ensure_ascii=False,
                ),
                user_key="ou_subject",
            )
        )
    )
    assert kept["ok"] is True
    assert kept["status"] == "pending"
    assert not (Path(os.environ["PSI_APPDATA"]) / "positive-negative-list" / "records.json").exists()

    legacy = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                card_action_json=json.dumps(
                    {
                        "action": {"value": {"action": "pn_candidate_merge_1_0", "batch_id": sent["batch_id"]}},
                        "message_id": sent["message_id"],
                        "operator": {"open_id": "ou_subject"},
                    },
                    ensure_ascii=False,
                ),
                user_key="ou_subject",
            )
        )
    )
    assert legacy["ok"] is False
    assert legacy["status"] == "unsupported_legacy_action"
    batch = asyncio.run(card_tool._load_candidate_batch(sent["batch_id"]))
    assert batch["rows"][1]["status"] == "pending"
    assert batch["rows"][0]["status"] == "kept"
    assert not (Path(os.environ["PSI_APPDATA"]) / "positive-negative-list" / "records.json").exists()


def test_candidate_evidence_action_is_deferred_to_analysis(feishu_network) -> None:
    card_tool = importlib.import_module("positive_negative_candidate_card")
    sent = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                receive_id="ou_subject",
                person_name="王炜博",
                candidates_json=json.dumps(["未及时同步风险"], ensure_ascii=False),
                source_key="meeting:2026-09-06:evidence-form",
                user_key="ou_subject",
            )
        )
    )
    opened = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                card_action_json=json.dumps(
                    {
                        "action": {"value": {"action": "pn_candidate_evidence_0", "batch_id": sent["batch_id"]}},
                        "message_id": sent["message_id"],
                        "operator": {"open_id": "ou_subject"},
                    },
                    ensure_ascii=False,
                ),
                user_key="ou_subject",
            )
        )
    )
    assert opened["ok"] is False
    assert opened["status"] == "unsupported_legacy_action"
    batch = asyncio.run(card_tool._load_candidate_batch(sent["batch_id"]))
    assert batch["status"] == "pending"
    assert batch["rows"][0]["status"] == "pending"


def test_candidate_card_handlers_match_only_organize_actions(feishu_network) -> None:
    card_tool = importlib.import_module("positive_negative_candidate_card")
    sent = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                receive_id="ou_subject",
                person_name="王炜博",
                candidates_json=json.dumps(["未及时同步风险", "上下游一直等待"], ensure_ascii=False),
                source_key="meeting:2026-09-06:handlers",
                user_key="ou_subject",
            )
        )
    )
    send = feishu_network["send"][-1]
    assert send["action_handlers"]
    assert all(value == "positive_negative_candidate_card" for value in send["action_handlers"].values())
    assert all("keep" not in action or "candidate" in action for action in send["action_handlers"])
    assert "positive_negative_case_confirm" not in json.dumps(send["action_handlers"], ensure_ascii=False)
    assert sent["status"] == "sent"


def test_last_candidate_decision_returns_analysis_payload_without_writing(feishu_network) -> None:
    card_tool = importlib.import_module("positive_negative_candidate_card")
    sent = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                receive_id="ou_subject",
                person_name="王炜博",
                candidates_json=json.dumps(["未及时同步风险"], ensure_ascii=False),
                source_key="meeting:2026-09-06:finalize",
                user_key="ou_subject",
            )
        )
    )
    kept = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                card_action_json=json.dumps(
                    {
                        "action": {"value": {"action": "pn_candidate_keep_0", "batch_id": sent["batch_id"]}},
                        "message_id": sent["message_id"],
                        "operator": {"open_id": "ou_subject"},
                    },
                    ensure_ascii=False,
                ),
                user_key="ou_subject",
            )
        )
    )
    assert kept["status"] == "ready_for_analysis"
    assert kept["candidates"][0]["source_candidates"] == ["未及时同步风险"]
    assert not (Path(os.environ["PSI_APPDATA"]) / "positive-negative-list" / "records.json").exists()


def test_evaluative_candidate_can_be_kept_and_deferred_to_analysis(feishu_network) -> None:
    card_tool = importlib.import_module("positive_negative_candidate_card")
    sent = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                receive_id="ou_subject",
                person_name="王炜博",
                candidates_json=json.dumps(["XXX学习能力强"], ensure_ascii=False),
                source_key="meeting:2026-09-06:evaluative",
                user_key="ou_subject",
            )
        )
    )

    result = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                card_action_json=json.dumps(
                    {
                        "action": {"value": {"action": "pn_candidate_keep_0", "batch_id": sent["batch_id"]}},
                        "message_id": sent["message_id"],
                        "operator": {"open_id": "ou_subject"},
                    },
                    ensure_ascii=False,
                ),
                user_key="ou_subject",
            )
        )
    )
    assert result["ok"] is True
    assert result["status"] == "ready_for_analysis"
    batch = asyncio.run(card_tool._load_candidate_batch(sent["batch_id"]))
    assert batch["rows"][0]["status"] == "kept"
    assert batch["status"] == "ready_for_analysis"


def test_last_candidate_decision_exposes_one_event_package_per_kept_candidate(feishu_network) -> None:
    card_tool = importlib.import_module("positive_negative_candidate_card")
    sent = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(
                receive_id="ou_subject",
                person_name="王炜博",
                meeting_date="2026-09-06",
                candidates_json=json.dumps(
                    [
                        {"text": "方案确定后直接开工，没有倒排节点", "context": "项目启动"},
                        {"text": "做到中途才发现上下游都在等", "context": "项目启动"},
                    ],
                    ensure_ascii=False,
                ),
                source_key="meeting:2026-09-06:event-package",
                user_key="ou_subject",
            )
        )
    )
    for action in ("pn_candidate_keep_0", "pn_candidate_keep_1"):
        result = json.loads(
            asyncio.run(
                card_tool.positive_negative_candidate_card(
                    card_action_json=json.dumps(
                        {
                            "action": {"value": {"action": action, "batch_id": sent["batch_id"]}},
                            "message_id": sent["message_id"],
                            "operator": {"open_id": "ou_subject"},
                        },
                        ensure_ascii=False,
                    ),
                    user_key="ou_subject",
                )
            )
        )
        assert result["ok"] is True
    assert result["status"] == "ready_for_analysis"
    assert len(result["analysis_candidates"]) == 2
    assert all(package["person_name"] == "王炜博" for package in result["analysis_candidates"])
    assert all(package["meeting_date"] == "2026-09-06" for package in result["analysis_candidates"])
    assert all(package["requires_case_analysis"] is True for package in result["analysis_candidates"])


def test_candidate_analysis_allows_missing_evidence_when_event_complete(monkeypatch) -> None:
    # 产品口径 (2026-09-07): 写入不强求证据, 事件描述完整即可进入确认卡。
    tool = importlib.import_module("positive_negative_candidate_analyze")
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    batch = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[{"text": "方案确定后直接开工，没有倒排节点", "context": "项目启动"}],
        source_key="meeting:2026-09-06:analyze-no-evidence",
    )
    batch["rows"][0]["status"] = "kept"
    batch["status"] = "analysis_started"
    asyncio.run(batches.save_batch(batch))
    captured: dict[str, Any] = {}

    async def fake_prepare(case_json: str, **kwargs):
        captured["case"] = json.loads(case_json)
        captured["kwargs"] = kwargs
        return json.dumps({"ok": True, "status": "待写入者确认", "case_id": "case_from_event"})

    monkeypatch.setattr(tool, "positive_negative_case_prepare", fake_prepare)
    result = json.loads(
        asyncio.run(
            tool.positive_negative_candidate_analyze(
                batch_id=batch["batch_id"],
                analysis_json=json.dumps(
                    {
                        "observed_behavior": "方案确定后直接开工，没有倒排节点",
                        "context": "项目启动",
                        "impact": "上下游等待",
                        "nature": "negative",
                        "category": "工作方式方法",
                        "primary_rule_id": "pn-test-negative",
                        "correct_behavior": "先倒排节点并明确交付物",
                        "immediate_remedy": "补齐节点并同步上下游",
                        "prevention": "在开工前检查倒排表",
                    },
                    ensure_ascii=False,
                ),
                user_key="ou_subject",
            )
        )
    )
    assert result["ok"] is True
    assert result["status"] == "待写入者确认"
    assert captured["case"]["evidence_sources"] == []


def test_candidate_analysis_delegates_one_complete_event_to_existing_prepare(monkeypatch) -> None:
    tool = importlib.import_module("positive_negative_candidate_analyze")
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    batch = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[{"text": "方案确定后直接开工，没有倒排节点", "context": "项目启动"}],
        source_key="meeting:2026-09-06:analyze-ready",
    )
    batch["rows"][0]["status"] = "kept"
    batch["status"] = "analysis_started"
    asyncio.run(batches.save_batch(batch))
    captured: dict[str, Any] = {}

    async def fake_prepare(case_json: str, **kwargs):
        captured["case"] = json.loads(case_json)
        captured["kwargs"] = kwargs
        return json.dumps({"ok": True, "status": "待写入者确认", "case_id": "case_from_event"})

    monkeypatch.setattr(tool, "positive_negative_case_prepare", fake_prepare)
    result = json.loads(
        asyncio.run(
            tool.positive_negative_candidate_analyze(
                batch_id=batch["batch_id"],
                analysis_json=json.dumps(
                    {
                        "observed_behavior": "方案确定后直接开工，没有倒排节点",
                        "context": "项目启动",
                        "impact": "上下游等待",
                        "evidence_sources": ["聊天记录"],
                        "nature": "negative",
                        "category": "工作方式方法",
                        "primary_rule_id": "pn-test-negative",
                        "correct_behavior": "先倒排节点并明确交付物",
                        "immediate_remedy": "补齐节点并同步上下游",
                        "prevention": "在开工前检查倒排表",
                    },
                    ensure_ascii=False,
                ),
                user_key="ou_subject",
            )
        )
    )
    assert result["ok"] is True
    assert result["status"] == "待写入者确认"
    assert captured["case"]["subject_user_key"] == "ou_subject"
    assert captured["case"]["reporter_user_key"] == "ou_subject"
    assert captured["kwargs"]["source_event_id"] == "meeting:2026-09-06:analyze-ready:event:0"
    assert captured["kwargs"]["user_key"] == "ou_subject"


def test_candidate_analysis_handles_multiple_kept_events_individually(monkeypatch) -> None:
    tool = importlib.import_module("positive_negative_candidate_analyze")
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    batch = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[
            {"text": "方案确定后直接开工，没有倒排节点", "context": "项目启动"},
            {"text": "风险出现后没有及时同步上下游", "context": "项目执行"},
        ],
        source_key="meeting:2026-09-06:multi-analyze",
    )
    for row in batch["rows"]:
        row["status"] = "kept"
    batch["status"] = "ready_for_analysis"
    asyncio.run(batches.save_batch(batch))
    source_events: list[str] = []

    async def fake_prepare(case_json: str, **kwargs):
        source_events.append(kwargs["source_event_id"])
        return json.dumps({"ok": True, "status": "待写入者确认", "case_id": f"case_{len(source_events)}"})

    monkeypatch.setattr(tool, "positive_negative_case_prepare", fake_prepare)
    base_analysis = {
        "impact": "上下游等待",
        "evidence_sources": ["会议纪要"],
        "nature": "negative",
        "category": "工作方式方法",
        "primary_rule_id": "pn-test-negative",
        "correct_behavior": "先明确计划并及时同步风险",
        "immediate_remedy": "补齐计划并同步上下游",
        "prevention": "设置执行和反馈检查点",
    }
    for event_index, row in enumerate(batch["rows"]):
        result = json.loads(
            asyncio.run(
                tool.positive_negative_candidate_analyze(
                    batch_id=batch["batch_id"],
                    event_index=event_index,
                    analysis_json=json.dumps(
                        base_analysis
                        | {
                            "observed_behavior": row["text"],
                            "context": row["context"],
                        },
                        ensure_ascii=False,
                    ),
                    user_key="ou_subject",
                )
            )
        )
        assert result["ok"] is True

    saved = asyncio.run(batches.load_batch(batch["batch_id"]))
    assert source_events == [
        "meeting:2026-09-06:multi-analyze:event:0",
        "meeting:2026-09-06:multi-analyze:event:1",
    ]
    assert set(saved["analysis_results"]) == {"0", "1"}


def test_confirmation_writes_only_test_adapter_then_sends_notice_card_without_auto_review(
    monkeypatch, tmp_path
) -> None:
    positive_negative = importlib.import_module("positive_negative_list")
    confirm = importlib.import_module("positive_negative_list_confirm")
    notifications = importlib.import_module("_positive_negative_list.notifications")
    reviews = importlib.import_module("_positive_negative_list.reviews")

    class FakeAdapter:
        creates = 0

        async def preflight(self, user_key):
            return SimpleNamespace(ok=True, errors=(), schema=object())

        async def create_public_record(self, case, user_key):
            self.creates += 1
            return {"record_id": "rec_test_only"}

    adapter = FakeAdapter()
    sent_cards: list[tuple[str, dict[str, Any]]] = []

    async def fake_send_card(receive_id, card_json, *args, **kwargs):
        sent_cards.append((receive_id, json.loads(card_json)))
        return {"ok": True, "message_id": "msg_notice"}

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        names = {"ou_subject": "王炜博", "ou_reporter": "罗霖"}
        return {"ok": True, "users": [{"open_id": item, "name": names[item]} for item in user_ids.split(",")]}

    async def fake_root():
        return tmp_path

    monkeypatch.setattr(positive_negative._f, "send_card_impl", fake_send_card)
    monkeypatch.setattr(positive_negative, "_get_session_id", lambda: "session_test")
    monkeypatch.setattr(confirm, "get_session_id", lambda: "session_test")
    monkeypatch.setattr(positive_negative, "_resolve_appdata_root", fake_root)
    monkeypatch.setattr(confirm, "resolve_appdata_root", fake_root)
    monkeypatch.setattr(confirm, "TABLE_ADAPTER", adapter)
    monkeypatch.setattr(confirm, "table_adapter", None)
    monkeypatch.setattr(notifications, "send_card_impl", fake_send_card)
    monkeypatch.setattr(notifications._feishu_impl, "get_users_batch_impl", fake_get_users_batch)
    case = _negative_case().to_mapping() | {"writer_user_key": "ou_reporter", "reporter_user_key": "ou_reporter"}
    prepared = json.loads(
        asyncio.run(
            positive_negative.positive_negative_case_prepare(
                json.dumps(case, ensure_ascii=False),
                source_event_id="evt_full_chain",
                user_key="ou_reporter",
            )
        )
    )
    callback = json.dumps(
        {
            "action": {"value": {"action": "positive_negative_case_confirm"}},
            "business_context": {"case_id": prepared["case_id"], "preview_digest": prepared["preview_digest"]},
        },
        ensure_ascii=False,
    )
    result = json.loads(asyncio.run(confirm.positive_negative_case_confirm(callback, user_key="ou_reporter")))

    assert result["ok"] is True
    assert result["public_record_id"] == "rec_test_only"
    assert adapter.creates == 1
    notice_cards = [card for target, card in sent_cards if target == "ou_subject"]
    assert notice_cards
    assert notice_cards[0]["schema"] == "2.0"
    assert "正确做法" in json.dumps(notice_cards[0], ensure_ascii=False)
    active = reviews.find_active_reviews(tmp_path, "ou_subject")
    assert active == ()
    assert result["private_review_status"] == "not_started"


# ---------------------------------------------------------------------------
# Reliability hardening: candidate chain (atomic writes / terminal render /
# idempotent clicks / identity) and prepare field-surface enforcement.
# ---------------------------------------------------------------------------


def test_candidate_kept_row_renders_terminal_state_without_buttons() -> None:
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    card_tool = importlib.import_module("positive_negative_candidate_card")
    batch = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[{"text": "未同步风险"}],
        source_key="meeting:2026-09-06:terminal",
    )
    batch["rows"][0]["status"] = "kept"
    rendered = json.dumps(card_tool.render_candidate_card(batch), ensure_ascii=False)
    assert "已纳入候选" in rendered
    assert "pn_candidate_keep_0" not in rendered
    assert "pn_candidate_ignore_0" not in rendered


def test_candidate_click_without_trusted_user_key_is_rejected(tmp_path, monkeypatch) -> None:
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    card_tool = importlib.import_module("positive_negative_candidate_card")
    monkeypatch.setattr(batches, "_resolve_appdata_root", lambda: tmp_path)
    batch = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[{"text": "未同步风险"}],
        source_key="meeting:2026-09-06:no-identity",
    )
    asyncio.run(batches.save_batch(batch))
    callback = json.dumps(
        {
            "action": {
                "value": {
                    "action": "pn_candidate_keep_0",
                    "batch_id": batch["batch_id"],
                    "person_open_id": "ou_subject",
                }
            },
            "message_id": "",
        }
    )
    payload = json.loads(asyncio.run(card_tool.positive_negative_candidate_card(card_action_json=callback)))
    assert payload["ok"] is False
    assert payload["status"] == "unauthorized"


def test_candidate_click_on_decided_row_returns_already_decided_without_state_change(tmp_path, monkeypatch) -> None:
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    card_tool = importlib.import_module("positive_negative_candidate_card")
    monkeypatch.setattr(batches, "_resolve_appdata_root", lambda: tmp_path)
    batch = batches.build_candidate_batch(
        person_open_id="ou_subject",
        person_name="王炜博",
        source_label="会议纪要",
        meeting_date="2026-09-06",
        candidates=[{"text": "未同步风险"}],
        source_key="meeting:2026-09-06:decided",
    )
    asyncio.run(batches.save_batch(batch))
    base_value = {
        "batch_id": batch["batch_id"],
        "person_open_id": "ou_subject",
    }
    keep_callback = json.dumps(
        {"action": {"value": {**base_value, "action": "pn_candidate_keep_0"}}, "message_id": ""}
    )
    first = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(card_action_json=keep_callback, user_key="ou_subject")
        )
    )
    assert first["ok"] is True
    assert first["status"] == "ready_for_analysis"
    loaded = asyncio.run(batches.load_batch(batch["batch_id"]))
    assert loaded["rows"][0]["status"] == "kept"

    ignore_callback = json.dumps(
        {"action": {"value": {**base_value, "action": "pn_candidate_ignore_0"}}, "message_id": ""}
    )
    second = json.loads(
        asyncio.run(
            card_tool.positive_negative_candidate_card(card_action_json=ignore_callback, user_key="ou_subject")
        )
    )
    assert second["ok"] is True
    assert second["status"] == "already_decided"
    assert second["row_status"] == "kept"
    after = asyncio.run(batches.load_batch(batch["batch_id"]))
    assert after["rows"][0]["status"] == "kept"
    assert after["status"] == "ready_for_analysis"


def test_candidate_batch_corrupt_file_is_quarantined_not_stuck(tmp_path, monkeypatch) -> None:
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    monkeypatch.setattr(batches, "_resolve_appdata_root", lambda: tmp_path)
    directory = tmp_path / "positive-negative-list" / "candidate-batches"
    directory.mkdir(parents=True)
    broken = directory / "cand_broken.json"
    broken.write_text('{"truncated": ', encoding="utf-8")

    assert asyncio.run(batches.load_batch("cand_broken")) is None
    assert not broken.exists()
    assert any(path.name.endswith(".corrupt") for path in directory.iterdir())
    assert asyncio.run(batches.find_batch_by_source_key("anything")) is None


def test_candidate_card_derives_source_key_when_omitted(tmp_path, monkeypatch) -> None:
    feishu = importlib.import_module("_feishu_impl")
    batches = importlib.import_module("_positive_negative_list.candidate_batches")
    card_tool = importlib.import_module("positive_negative_candidate_card")
    monkeypatch.setattr(batches, "_resolve_appdata_root", lambda: tmp_path)
    calls: list[dict[str, Any]] = []

    async def fake_send_card(
        receive_id, card_json, receive_id_type, user_key=None, business_context_json="{}",
        action_handlers_json="{}", multi_use=False, **_kwargs,
    ):
        calls.append({"receive_id": receive_id, "handlers": json.loads(action_handlers_json or "{}")})
        return {"ok": True, "message_id": f"om_derived_{len(calls)}"}

    monkeypatch.setattr(feishu, "send_card_impl", fake_send_card)
    kwargs = {
        "candidates_json": json.dumps(["未同步风险"], ensure_ascii=False),
        "person_name": "王炜博",
        "source_label": "会议纪要",
        "meeting_date": "2026-09-06",
        "source_key": "",
        "receive_id": "ou_subject",
        "user_key": "ou_subject",
    }
    first = json.loads(asyncio.run(card_tool.positive_negative_candidate_card(**kwargs)))
    assert first["ok"] is True
    assert first["status"] == "sent"
    second = json.loads(asyncio.run(card_tool.positive_negative_candidate_card(**kwargs)))
    assert second["ok"] is True
    assert second["status"] == "already_sent"
    assert second["batch_id"] == first["batch_id"]
    assert len(calls) == 1


def test_prepare_rejects_red_line_flag_from_the_model() -> None:
    positive_negative = importlib.import_module("positive_negative_list")
    mapping = _negative_case().to_mapping() | {"red_line_candidate": True}
    payload = json.loads(
        asyncio.run(
            positive_negative.positive_negative_case_prepare(
                json.dumps(mapping, ensure_ascii=False),
                source_event_id="evt_red_line_injected",
                user_key="ou_reporter",
            )
        )
    )
    assert payload["ok"] is False
    assert payload["status"] == "red_line_state_rejected"
    assert payload["allowed_case_fields"]
    assert len(payload["allowed_case_fields"]) == 18


# ---------------------------------------------------------------------------
# R2: crash-window recovery (confirm row recovery, orphan reservation
# takeover) and six-column alias contains-based deduplication.
# ---------------------------------------------------------------------------


def test_confirm_after_crash_between_create_and_receipt_recovers_existing_row(monkeypatch, tmp_path) -> None:
    positive_negative = importlib.import_module("positive_negative_list")
    confirm = importlib.import_module("positive_negative_list_confirm")
    notifications = importlib.import_module("_positive_negative_list.notifications")
    feishu = importlib.import_module("_feishu_impl")

    class FakeAdapter:
        creates = 0

        def __init__(self) -> None:
            self.lookup = {"rec_recovered"}

        async def preflight(self, user_key):
            return SimpleNamespace(ok=True, errors=(), schema=object())

        async def find_by_source_key(self, source_key, user_key):
            if "rec_recovered" in self.lookup:
                return {"record_id": "rec_recovered"}
            return None

        async def create_public_record(self, case, user_key):
            self.creates += 1
            return {"record_id": "rec_new"}

        def public_record_link(self, record_id):
            return f"https://feishu.cn/base/app?table=tbl&record={record_id}"

    adapter = FakeAdapter()
    sent_cards: list[dict[str, Any]] = []

    async def fake_send_card(receive_id, card_json, *args, **kwargs):
        sent_cards.append({"receive_id": receive_id, "card": json.loads(card_json)})
        return {"ok": True, "message_id": "msg_notice"}

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        names = {"ou_subject": "王炜博", "ou_reporter": "罗霖"}
        return {"ok": True, "users": [{"open_id": item, "name": names[item]} for item in user_ids.split(",")]}

    async def fake_root():
        return tmp_path

    monkeypatch.setattr(feishu, "send_card_impl", fake_send_card)
    monkeypatch.setattr(feishu, "get_users_batch_impl", fake_get_users_batch)
    monkeypatch.setattr(positive_negative, "_get_session_id", lambda: "session_test")
    monkeypatch.setattr(positive_negative, "_resolve_appdata_root", fake_root)
    monkeypatch.setattr(confirm, "get_session_id", lambda: "session_test")
    monkeypatch.setattr(confirm, "resolve_appdata_root", fake_root)
    monkeypatch.setattr(confirm, "TABLE_ADAPTER", adapter)
    monkeypatch.setattr(confirm, "table_adapter", None)
    monkeypatch.setattr(notifications, "send_card_impl", fake_send_card)

    case = _negative_case().to_mapping() | {"writer_user_key": "ou_reporter", "reporter_user_key": "ou_reporter"}
    prepared = json.loads(
        asyncio.run(
            positive_negative.positive_negative_case_prepare(
                json.dumps(case, ensure_ascii=False),
                source_event_id="evt_crash_recover",
                user_key="ou_reporter",
            )
        )
    )
    assert prepared["ok"] is True
    case_id = prepared["case_id"]

    # Simulate the crash window: the draft was persisted as ``writing`` but no
    # receipt ever landed (process died right after the table row was created).
    drafts_base = tmp_path / "positive-negative-list" / "drafts"
    draft_path = next(path for path in drafts_base.glob(f"*/{case_id}.json"))
    payload = json.loads(draft_path.read_text(encoding="utf-8"))
    payload["case"]["workflow"] = "writing"
    draft_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    callback = json.dumps(
        {
            "action": {"value": {"action": "positive_negative_case_confirm"}},
            "business_context": {"case_id": case_id, "preview_digest": prepared["preview_digest"]},
        },
        ensure_ascii=False,
    )
    result = json.loads(asyncio.run(confirm.positive_negative_case_confirm(callback, user_key="ou_reporter")))
    assert result["ok"] is True
    assert result["public_record_id"] == "rec_recovered"
    assert result["notification_status"] == "notification_sent"
    assert adapter.creates == 0
    assert (tmp_path / "positive-negative-list" / "receipts" / f"{case_id}.json").is_file()

    # A second click on the same card must not create or resend anything.
    again = json.loads(asyncio.run(confirm.positive_negative_case_confirm(callback, user_key="ou_reporter")))
    assert again["status"] == "already_written"
    assert adapter.creates == 0


def test_prepare_reclaims_orphaned_same_source_reservation(monkeypatch, tmp_path) -> None:
    positive_negative = importlib.import_module("positive_negative_list")
    dedupe = importlib.import_module("_positive_negative_list.dedupe")
    feishu = importlib.import_module("_feishu_impl")

    async def fake_send_card(receive_id, card_json, *args, **kwargs):
        return {"ok": True, "message_id": "msg_prep"}

    async def fake_get_users_batch(user_ids: str, user_id_type: str = "open_id"):
        names = {"ou_subject": "王炜博", "ou_reporter": "罗霖"}
        return {"ok": True, "users": [{"open_id": item, "name": names[item]} for item in user_ids.split(",")]}

    async def fake_root():
        return tmp_path

    monkeypatch.setattr(feishu, "send_card_impl", fake_send_card)
    monkeypatch.setattr(feishu, "get_users_batch_impl", fake_get_users_batch)
    monkeypatch.setattr(positive_negative, "_get_session_id", lambda: "session_test")
    monkeypatch.setattr(positive_negative, "_resolve_appdata_root", fake_root)

    source_key = dedupe.make_source_key("feishu_private_chat", "evt_orphan_prep")
    # Simulate a crash after reserve but before the card was sent: the orphaned
    # reservation references a case that has no draft and no receipt.
    dedupe.reserve_source_key(str(tmp_path), source_key, "case_orphan_stale")

    case = _negative_case().to_mapping() | {"writer_user_key": "ou_reporter", "reporter_user_key": "ou_reporter"}
    prepared = json.loads(
        asyncio.run(
            positive_negative.positive_negative_case_prepare(
                json.dumps(case, ensure_ascii=False),
                source_event_id="evt_orphan_prep",
                user_key="ou_reporter",
            )
        )
    )
    assert prepared["ok"] is True
    reservation_path = tmp_path / "positive-negative-list" / "dedupe" / f"{source_key}.json"
    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    assert reservation["case_id"] == prepared["case_id"]
    assert reservation["case_id"] != "case_orphan_stale"

    # A second attempt for the same source is a real duplicate now (draft
    # exists), so it must keep refusing instead of reclaiming the live case.
    second = json.loads(
        asyncio.run(
            positive_negative.positive_negative_case_prepare(
                json.dumps(case, ensure_ascii=False),
                source_event_id="evt_orphan_prep",
                user_key="ou_reporter",
            )
        )
    )
    assert second["ok"] is False
    assert second["status"] == "exact_duplicate"


def test_six_column_dedupe_search_uses_contains_operator() -> None:
    table = importlib.import_module("_positive_negative_list.table")

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        async def search(self, field_id: str, value: str, user_key: str, operator: str = "is"):
            self.calls.append((field_id, value, operator))
            return [{"record_id": "rec_note_hit"}] if operator == "contains" else []

    aliased = SimpleNamespace(
        deduplication_field_ids={
            "source_key": "f_note",
            "canonical_incident_id": "f_note",
            "cross_source_fingerprint": "f_note",
        }
    )
    client = FakeClient()
    adapter = table.TableAdapter(client)
    adapter._schema = aliased
    asyncio.run(adapter.find_by_source_key("sk-123", "ou_writer"))
    assert client.calls and client.calls[0][2] == "contains"
    assert client.calls[0][1] == "sk-123"

    dedicated = SimpleNamespace(
        deduplication_field_ids={
            "source_key": "f_sk",
            "canonical_incident_id": "f_ci",
            "cross_source_fingerprint": "f_fp",
        }
    )
    client2 = FakeClient()
    adapter2 = table.TableAdapter(client2)
    adapter2._schema = dedicated
    asyncio.run(adapter2.find_by_source_key("sk-456", "ou_writer"))
    assert client2.calls and client2.calls[0][2] == "is"


# ---------------------------------------------------------------------------
# R3: notification retry fidelity (cards persisted before send, per-target
# receipts prevent duplicate delivery) and record-id link hygiene.
# ---------------------------------------------------------------------------


def test_notification_retry_reuses_stored_card_after_interrupted_send(monkeypatch, tmp_path) -> None:
    notifications = importlib.import_module("_positive_negative_list.notifications")
    sent_cards: list[str] = []

    async def fake_users(user_ids: str, user_id_type: str = "open_id"):
        return {"ok": True, "users": [{"open_id": "ou_subject", "name": "王炜博"}]}

    async def fail_send(receive_id, card_json, *args, **kwargs):
        raise TimeoutError("transport down")

    async def ok_send(receive_id, card_json, *args, **kwargs):
        sent_cards.append(str(card_json))
        return {"ok": True, "message_id": "msg_retry"}

    monkeypatch.setattr(notifications._feishu_impl, "get_users_batch_impl", fake_users)
    monkeypatch.setattr(notifications, "send_card_impl", fail_send)
    sender = notifications.NotificationSender(tmp_path)
    result = asyncio.run(sender.send_subject_notice(_negative_case(), "rec_pub_1"))
    assert result.ok is False

    # The case receipt already carries the card and the per-recipient receipt
    # keeps the exact card JSON for the retry.
    case_receipt = json.loads(
        (tmp_path / "positive-negative-list" / "receipts" / "case_test.json").read_text(encoding="utf-8")
    )
    assert case_receipt["notification_cards"]["ou_subject"]
    digest = hashlib.sha256(b"rec_pub_1\nou_subject").hexdigest()
    record_receipt = json.loads(
        (tmp_path / "positive-negative-list" / "notification-receipts" / f"{digest}.json").read_text(encoding="utf-8")
    )
    stored_card = str(record_receipt["card_json"])
    assert stored_card

    monkeypatch.setattr(notifications, "send_card_impl", ok_send)
    retry = asyncio.run(sender.retry_notification("case_test"))
    assert retry.ok is True
    assert retry.status == "notification_sent"
    assert sent_cards and sent_cards[0] == stored_card


def test_notification_retry_skips_target_already_sent_before_crash(monkeypatch, tmp_path) -> None:
    notifications = importlib.import_module("_positive_negative_list.notifications")
    sent_cards: list[str] = []

    async def fake_users(user_ids: str, user_id_type: str = "open_id"):
        return {"ok": True, "users": [{"open_id": "ou_subject", "name": "王炜博"}]}

    async def ok_send(receive_id, card_json, *args, **kwargs):
        sent_cards.append(str(card_json))
        return {"ok": True, "message_id": "msg_once"}

    monkeypatch.setattr(notifications._feishu_impl, "get_users_batch_impl", fake_users)
    monkeypatch.setattr(notifications, "send_card_impl", ok_send)
    sender = notifications.NotificationSender(tmp_path)
    first = asyncio.run(sender.send_subject_notice(_negative_case(), "rec_pub_2"))
    assert first.ok is True
    assert len(sent_cards) == 1

    # Simulate a crash before the case receipt was updated: the case receipt is
    # still pending, but the per-recipient receipt already says "sent".  The
    # retry must not deliver a second copy.
    retry = asyncio.run(sender.retry_notification("case_test"))
    assert retry.ok is True
    assert len(sent_cards) == 1


def test_notice_text_never_leaks_raw_record_id_as_link() -> None:
    notifications = importlib.import_module("_positive_negative_list.notifications")

    text = notifications._notice_text(_negative_case(), "rec_raw_123")
    assert "rec_raw_123" not in text
    assert "记录链接" not in text
    linked = notifications._notice_text(_negative_case(), "https://feishu.cn/base/x?table=t&record=rec_raw_123")
    assert "记录链接" in linked

    def record_with(record_link: str):
        return notifications.LedgerRecord(
            record_id="rec_x",
            case_id="",
            reporter_user_key="ou_reporter",
            subject_user_key="ou_subject",
            occurred_at="2026-09-01",
            nature="positive",
            category="分类",
            fact_summary="行为事实",
            evidence_sources=(),
            correct_behavior="",
            immediate_remedy="",
            prevention="",
            review_status="",
            source_key="",
            canonical_incident_id="",
            cross_source_fingerprint="",
            fields={},
            record_link=record_link,
        )

    record_text = notifications._record_notice_text(record_with("rec_x"))
    assert "rec_x" not in record_text
    assert "记录链接" not in record_text
    record_text_linked = notifications._record_notice_text(record_with("https://feishu.cn/base/x?record=rec_x"))
    assert "记录链接" in record_text_linked


# ---------------------------------------------------------------------------
# R5: read path never reports a silent empty table for unusable filters and
# the single-page tool no longer invites impossible pagination loops.
# ---------------------------------------------------------------------------


def test_read_rejects_category_filter_when_ledger_has_no_category_column(monkeypatch) -> None:
    read_tool = importlib.import_module("positive_negative_case_read")
    reader = importlib.import_module("_positive_negative_list.reader")

    async def fake_read_records(client, query, user_key):
        raise AssertionError("guard must reject before any read")

    async def fake_names(*args):
        return frozenset({"事件描述", "正负面归属", "员工姓名", "记录日期", "备注", "填写人", "记录ID"})

    monkeypatch.setattr(reader, "read_records", fake_read_records)
    monkeypatch.setattr(reader, "list_table_field_names", fake_names)
    payload = json.loads(
        asyncio.run(read_tool.positive_negative_case_read(query_json='{"category": "迅速行动、及时反馈"}'))
    )
    assert payload["ok"] is False
    assert payload["状态"] == "读取失败"
    assert "分类" in payload["说明"]
    assert "汇总分析" in payload["说明"]


def test_read_rejects_person_name_filter_without_identity(monkeypatch) -> None:
    read_tool = importlib.import_module("positive_negative_case_read")
    reader = importlib.import_module("_positive_negative_list.reader")

    async def fake_read_records(client, query, user_key):
        raise AssertionError("guard must reject before any read")

    monkeypatch.setattr(reader, "read_records", fake_read_records)
    payload = json.loads(
        asyncio.run(
            read_tool.positive_negative_case_read(
                query_json='{"subject_user_key": "王炜博"}', user_key="ou_writer"
            )
        )
    )
    assert payload["ok"] is False
    assert "姓名" in payload["说明"]
    assert "涉事人" in payload["说明"]


def test_read_accepts_trusted_identity_filter_and_reads(monkeypatch) -> None:
    read_tool = importlib.import_module("positive_negative_case_read")
    reader = importlib.import_module("_positive_negative_list.reader")
    runtime = importlib.import_module("_positive_negative_list.runtime")

    async def fake_read_records(client, query, user_key):
        assert query.subject_user_key == "ou_subject"
        assert query.page_size == 100
        return {"ok": True, "records": [], "has_more": False, "page_token": ""}

    async def fake_public(result):
        return {"ok": True, "记录": [], "本页记录数": 0, "读取状态": "已读完全部记录"}

    async def fake_names(*args):
        return frozenset({"事件描述", "正负面归属", "员工姓名", "记录日期", "备注", "填写人", "记录ID"})

    monkeypatch.setattr(runtime, "configured_read_table_adapter", lambda: SimpleNamespace(_client=object()))
    monkeypatch.setattr(reader, "read_records", fake_read_records)
    monkeypatch.setattr(reader, "public_result_with_names", fake_public)
    monkeypatch.setattr(reader, "list_table_field_names", fake_names)
    payload = json.loads(
        asyncio.run(
            read_tool.positive_negative_case_read(
                query_json='{"subject_user_key": "ou_subject"}', user_key="ou_writer"
            )
        )
    )
    assert payload["ok"] is True
    assert payload["读取状态"] == "已读完全部记录"


def test_read_bad_query_returns_unified_chinese_failure() -> None:
    read_tool = importlib.import_module("positive_negative_case_read")
    payload = json.loads(asyncio.run(read_tool.positive_negative_case_read(query_json="not-json")))
    assert payload["ok"] is False
    assert payload["状态"] == "读取失败"
    assert "解析" in payload["说明"]


def test_single_page_read_text_points_to_analyze_tool_not_manual_paging() -> None:
    reader = importlib.import_module("_positive_negative_list.reader")
    projection = reader._public_result({"ok": True, "records": [], "has_more": True})
    assert "汇总分析" in projection["读取状态"]
    assert "下一页" not in projection["读取状态"]
