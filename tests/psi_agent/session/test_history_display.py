from __future__ import annotations

from psi_agent.session.history_display import (
    KIND_CHAT,
    KIND_SCHEDULE_DISPLAY,
    KIND_SCHEDULE_SILENT,
    is_displayable_chat_message,
    message_kind,
    messages_for_ai,
    strip_transfer_markers,
    with_chat_type,
    with_kind,
)


def test_with_kind_and_messages_for_ai() -> None:
    msg = with_kind({"role": "user", "content": "hi"}, KIND_SCHEDULE_SILENT)
    assert msg["kind"] == KIND_SCHEDULE_SILENT
    assert "chat_type" not in msg
    projected = messages_for_ai([msg, {"role": "assistant", "content": "ok", "kind": KIND_CHAT}])
    assert projected == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]


def test_legacy_chat_type_maps_to_kind() -> None:
    assert message_kind({"role": "user", "content": "x", "chat_type": "common"}) == KIND_CHAT
    assert message_kind({"role": "user", "content": "x", "chat_type": "schedule"}) == KIND_SCHEDULE_SILENT
    legacy = with_chat_type({"role": "user", "content": "cron"}, "schedule")
    assert legacy["kind"] == KIND_SCHEDULE_SILENT


def test_messages_for_ai_rewrites_legacy_schedule_roles() -> None:
    projected = messages_for_ai(
        [
            {"role": "user_schedule", "content": "heartbeat"},
            {"role": "assistant_schedule", "content": "HEARTBEAT_OK"},
            {"role": "user", "content": "hi", "kind": KIND_CHAT},
        ]
    )
    assert projected == [
        {"role": "user", "content": "heartbeat"},
        {"role": "assistant", "content": "HEARTBEAT_OK"},
        {"role": "user", "content": "hi"},
    ]
    assert not is_displayable_chat_message({"role": "user_schedule", "content": "heartbeat"})


def test_is_displayable_filters_by_kind_whitelist() -> None:
    assert is_displayable_chat_message({"role": "user", "content": "hi", "kind": KIND_CHAT})
    assert is_displayable_chat_message({"role": "assistant", "content": "hey"})  # omit → chat
    assert not is_displayable_chat_message(
        {"role": "user", "content": "cron", "kind": KIND_SCHEDULE_SILENT}
    )
    assert not is_displayable_chat_message(
        {"role": "assistant", "content": "HEARTBEAT_OK", "kind": KIND_SCHEDULE_SILENT}
    )
    assert is_displayable_chat_message(
        {"role": "assistant", "content": "日报已生成", "kind": KIND_SCHEDULE_DISPLAY}
    )
    assert not is_displayable_chat_message(
        {"role": "user", "content": "trigger", "kind": KIND_SCHEDULE_DISPLAY}
    )
    assert not is_displayable_chat_message({"role": "assistant", "content": "HEARTBEAT_OK"})
    assert not is_displayable_chat_message({"role": "tool", "content": "x", "kind": KIND_CHAT})
    assert not is_displayable_chat_message({"role": "assistant", "content": ""})


def test_strip_transfer_markers() -> None:
    assert strip_transfer_markers("见附件\n[SEND:/tmp/a.html]\n\n") == "见附件"
    assert (
        strip_transfer_markers("分析\n[RECV:C:\\Users\\Z\\a.png]") == "分析"
    )
    assert strip_transfer_markers("[RECV:/only.png]") == ""
