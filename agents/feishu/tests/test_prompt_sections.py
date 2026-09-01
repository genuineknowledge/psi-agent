"""Regression tests for high-risk workspace prompt guidance."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

SYSTEMS_DIR = Path(__file__).resolve().parents[1] / "systems"
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

TOOLS_MD = Path(__file__).resolve().parents[1] / "TOOLS.md"

sections = importlib.import_module("prompt_sections")


def test_runtime_prompt_keeps_personal_and_organization_memory_separate() -> None:
    guidance = sections.FUSION_MEMORY_SECTION

    assert "`memory_add`: store stable personal" in guidance
    assert "`organization_memory_add`" in guidance
    assert 'visibility="organization"' in guidance
    assert "organization-memory" in guidance
    assert "memory_add`: store stable user preferences, project facts" not in guidance


def test_document_guidance_uses_existing_tools_without_runtime_install() -> None:
    combined = sections.SEND_FILES_SECTION + sections.DELIVERABLES_AS_FILES_SECTION

    assert "Do not run pip install" in combined
    assert "call `write_word`" in combined
    assert "install a library" not in combined


def test_long_structured_deliverables_are_file_first() -> None:
    assert "do not draft the full artifact in chat first" in sections.DELIVERABLES_AS_FILES_SECTION


def test_delivery_forbids_using_feishu_tools_as_the_transport() -> None:
    """File delivery is ``[SEND:]`` on every channel, never a ``feishu_*`` call.

    Regression (web session ``426a743c``): asked only to convert a document, the
    agent called ``feishu_chat_find(name="Haitun团队")`` to deliver it to a Feishu
    group the user never mentioned. On the web console that reaches nobody.
    """
    section = sections.SEND_FILES_SECTION

    assert "Do NOT use `feishu_*` tools" in section
    # ``<feishu_context>`` is the only block that exists, so the rule keys off
    # its *absence* rather than naming every non-Feishu channel's own block.
    assert "<feishu_context>" in section
    assert "Assume not-Feishu unless that block is present" in section
    # Explicitly carves out the legitimate case, so the rule is not over-read.
    assert "explicitly asks" in section


def test_delivery_section_does_not_claim_a_specific_channel() -> None:
    """``[SEND:]`` is channel-agnostic; the prompt must not imply otherwise."""
    section = sections.SEND_FILES_SECTION

    assert "you do not choose a channel" in section
    assert "Feishu chat window" not in section
    assert "飞书聊天窗口" not in section


def test_tools_md_delivery_item_is_channel_neutral() -> None:
    """TOOLS.md item 14 must not describe ``[SEND:]`` as a Feishu-only mechanism.

    It used to read "上传发送到用户当前的飞书聊天窗口" unconditionally, which is
    where the Feishu framing came from — the same text is loaded on the web
    console, where Feishu is the wrong destination.
    """
    text = TOOLS_MD.read_text(encoding="utf-8")

    assert "上传发送到用户当前所在的聊天窗口" in text
    assert "上传发送到用户当前的飞书聊天窗口" not in text
    assert "绝不要拿 `feishu_*` 工具当交付手段" in text


def test_work_assignment_routing_covers_colloquial_delivery_requests() -> None:
    guidance = sections.WORK_ASSIGNMENT_ROUTING_SECTION

    assert "work-assignment-delegation" in guidance
    assert "让/叫/安排/请" in guidance
    assert "写/做/处理/整理/实现/准备/提交/跟进" in guidance
    assert "Short requests that ask a named colleague" in guidance
    assert "帮我转达" in guidance
    assert "看一看/看下/检查/验证/反馈/排查/催一下" in guidance
    assert "明确接收人" in guidance
    assert "必须调用 `assignment_upsert`" in guidance
    assert "不要改用 `feishu_message_send`" in guidance
    assert "转达一句/带句话/发一句" in guidance


def test_assignment_accept_callback_does_not_reread_skills_or_emit_duplicate_text() -> None:
    guidance = sections.WORK_ASSIGNMENT_ROUTING_SECTION

    assert "assignment_accept" in guidance
    assert "do not reread the skill" in guidance.lower()
    assert "assignment_send_card" in guidance
    assert "zero assistant content" in guidance


def test_work_assignment_feedback_requires_recipient_confirmation_after_reply() -> None:
    guidance = sections.WORK_ASSIGNMENT_ROUTING_SECTION

    assert "assignment_feedback" in guidance
    assert "updated_waiting_recipient_confirmation" in guidance
    assert "must not resume execution" in guidance
    assert "recipient result card" in guidance
    assert "same feedback thread" in guidance
    assert "assignment_transition" in guidance


def test_work_assignment_feedback_card_callback_uses_direct_tool_contract() -> None:
    guidance = sections.WORK_ASSIGNMENT_ROUTING_SECTION

    assert "pass the entire current card-action JSON as `card_action_json`" in guidance
    assert "do not call `tool_describe`, `tool_search_code`, `read`, or `bash`" in guidance
    assert "finish with zero assistant content" in guidance
    assert "do not send a separate Feishu message" in guidance


def test_work_assignment_feedback_limits_blocking_to_irreducible_gaps() -> None:
    guidance = sections.WORK_ASSIGNMENT_ROUTING_SECTION

    assert "only the assigner can provide" in guidance
    assert "irreversible" in guidance
    assert "permission, resource, or requirement conflict" in guidance
    assert "significant rework or unauthorized action" in guidance
    assert "reversible, explicit, recorded assumption" in guidance


def test_recipient_task_questions_use_feedback_instead_of_relay() -> None:
    guidance = sections.WORK_ASSIGNMENT_ROUTING_SECTION

    assert "截止时间" in guidance
    assert "任务范围" in guidance
    assert "验收标准" in guidance
    assert "assignment_feedback" in guidance
    assert "不要调用 `feishu_user_get`" in guidance
    assert "不要调用 `feishu_message_send`" in guidance
    assert "不要等待安排者" in guidance
    assert "已提交反馈" in guidance
    assert '"notification_strategy": "blocking"' in guidance


def test_assignment_feedback_tool_contract_documents_actions_and_payload() -> None:
    source = (
        sections.WORK_ASSIGNMENT_ROUTING_SECTION
        + "\n"
        + (Path(__file__).resolve().parents[1] / "tools" / "assignment_feedback.py").read_text(encoding="utf-8")
    )

    assert 'action="create"' in source
    assert 'action="append"' in source
    assert 'action="assigner_reply"' in source
    assert 'action="recipient_confirm"' in source
    assert '"notification_strategy": "blocking"' in source
    assert '"attempts": ["已核查内容"]' in source
    assert '"options": [{"label": "选项 A", "value": "option_a", "recommended": true}]' in source
