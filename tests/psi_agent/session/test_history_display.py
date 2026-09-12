from __future__ import annotations

from psi_agent.session.history_display import (
    CREATED_AT_KEY,
    ELISION_HANDLE_TEMPLATE,
    KIND_CHAT,
    KIND_SCHEDULE_DISPLAY,
    KIND_SCHEDULE_SILENT,
    THINKING_MS_KEY,
    VisibleMarkerFilter,
    extract_send_paths,
    is_displayable_chat_message,
    message_kind,
    project_history_for_wire,
    render_sent_files_note,
    strip_transfer_markers,
    with_chat_type,
    with_created_at,
    with_kind,
)


def test_visible_marker_filter_drops_complete_and_split_handles() -> None:
    """出站过滤: 完整句柄直接剥掉; 被流式切开的句柄一个字都不许漏。"""
    handle = ELISION_HANDLE_TEMPLATE.format(chars=1334, label="", sent="", handle="assistant#425952")
    complete = VisibleMarkerFilter()
    assert complete.feed(f"已完成。{handle}") == "已完成。"

    split = VisibleMarkerFilter()
    assert split.feed(f"结论 A。{handle[:3]}") == "结论 A。"
    assert split.feed(handle[3:]) == ""  # 剩下的半边被识别成同一个标记, 不落屏
    assert split.pending == ""

    # [SEND:]/[RECV:] 同理(文件本身走 FileChunk, 标记只给机器看)
    transfer = VisibleMarkerFilter()
    assert transfer.feed("见文件[SEND:") == "见文件"
    assert transfer.feed("方案.pdf]\n请查收") == "\n请查收"


def test_visible_marker_filter_releases_non_marker_brackets_and_drops_dangling_prefix() -> None:
    """普通方括号只是延迟一拍, 不会被吞; 回合结束时半截标记按碎片丢弃。"""
    square = VisibleMarkerFilter()
    assert square.feed("见 [") == "见 "  # 可能是标记开头, "[" 先扣住
    assert square.feed("附件] 说明") == "[附件] 说明"  # 证明不是标记, 原样放行
    assert square.pending == ""

    dangling = VisibleMarkerFilter()
    assert dangling.feed("正常文本[已省") == "正常文本"
    assert dangling.pending == "[已省"
    assert dangling.flush() == "[已省"
    assert dangling.pending == ""


def test_with_kind_and_project_history_for_wire() -> None:
    msg = with_kind({"role": "user", "content": "hi"}, KIND_SCHEDULE_SILENT)
    assert msg["kind"] == KIND_SCHEDULE_SILENT
    assert "chat_type" not in msg
    projected = project_history_for_wire([msg, {"role": "assistant", "content": "ok", "kind": KIND_CHAT}])
    assert projected == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]


def test_legacy_chat_type_maps_to_kind() -> None:
    assert message_kind({"role": "user", "content": "x", "chat_type": "common"}) == KIND_CHAT
    assert message_kind({"role": "user", "content": "x", "chat_type": "schedule"}) == KIND_SCHEDULE_SILENT
    legacy = with_chat_type({"role": "user", "content": "cron"}, "schedule")
    assert legacy["kind"] == KIND_SCHEDULE_SILENT


def test_project_history_for_wire_rewrites_legacy_schedule_roles() -> None:
    projected = project_history_for_wire(
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


def test_project_history_for_wire_skips_reasoning_only_assistant_rows() -> None:
    projected = project_history_for_wire(
        [
            {"role": "user", "content": "confirm"},
            {"role": "assistant", "reasoning": "internal only", "kind": KIND_CHAT},
            {
                "role": "assistant",
                "content": "",
                "reasoning": "legacy internal only",
                "tool_calls": [],
                "kind": KIND_CHAT,
            },
            {"role": "user", "content": "continue"},
        ]
    )

    assert projected == [
        {"role": "user", "content": "confirm"},
        {"role": "user", "content": "continue"},
    ]


def test_project_history_for_wire_keeps_assistant_rows_with_content_or_tool_calls() -> None:
    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }
    ]

    # ``reasoning`` goes out as ``reasoning_content`` — the only name providers
    # read.  See ``test_assistant_reasoning_goes_out_as_reasoning_content``.
    assert project_history_for_wire(
        [
            {"role": "assistant", "content": "answer", "reasoning": "internal"},
            {"role": "assistant", "reasoning": "internal", "tool_calls": tool_calls},
        ]
    ) == [
        {"role": "assistant", "content": "answer", "reasoning_content": "internal"},
        {"role": "assistant", "reasoning_content": "internal", "tool_calls": tool_calls},
    ]


def test_is_displayable_filters_by_kind_whitelist() -> None:
    assert is_displayable_chat_message({"role": "user", "content": "hi", "kind": KIND_CHAT})
    assert is_displayable_chat_message({"role": "assistant", "content": "hey"})  # omit → chat
    assert not is_displayable_chat_message({"role": "user", "content": "cron", "kind": KIND_SCHEDULE_SILENT})
    assert not is_displayable_chat_message(
        {"role": "assistant", "content": "HEARTBEAT_OK", "kind": KIND_SCHEDULE_SILENT}
    )
    assert is_displayable_chat_message({"role": "assistant", "content": "日报已生成", "kind": KIND_SCHEDULE_DISPLAY})
    assert not is_displayable_chat_message({"role": "user", "content": "trigger", "kind": KIND_SCHEDULE_DISPLAY})
    assert not is_displayable_chat_message({"role": "assistant", "content": "HEARTBEAT_OK"})
    assert not is_displayable_chat_message({"role": "tool", "content": "x", "kind": KIND_CHAT})
    assert not is_displayable_chat_message({"role": "assistant", "content": ""})


def test_strip_transfer_markers() -> None:
    assert strip_transfer_markers("见附件\n[SEND:/tmp/a.html]\n\n") == "见附件"
    assert strip_transfer_markers("分析\n[RECV:C:\\Users\\Z\\a.png]") == "分析"
    assert strip_transfer_markers("[RECV:/only.png]") == ""
    assert strip_transfer_markers("see attachment\n[ SEND:/tmp/a.html ]\n\n") == "see attachment"
    assert strip_transfer_markers("see attachment\n[Send:/tmp/a.html]\n\n") == "see attachment"


def test_render_sent_files_note_names_files_without_emitting_a_marker() -> None:
    """The note tells the model what it delivered; it must not re-trigger delivery.

    Base name only — the directory is bytes nobody reads, and stripping it is
    also what disarms a path built to smuggle a marker through the file name.
    """
    assert render_sent_files_note("见附件\n[SEND:/workspace/out/方案.pdf]") == ", 含已送达文件: 方案.pdf"
    assert render_sent_files_note("[SEND:C:\\Users\\Z\\报告.docx]") == ", 含已送达文件: 报告.docx"
    assert render_sent_files_note("[SEND:a.pdf]\n[ send: b.md ]") == ", 含已送达文件: a.pdf, b.md"
    # Same path twice is one delivery, not two.
    assert render_sent_files_note("[SEND:/w/a.pdf][SEND:/w/a.pdf]") == ", 含已送达文件: a.pdf"
    # Nothing delivered, nothing paid.
    assert render_sent_files_note("普通回复") == ""
    assert render_sent_files_note("[RECV:/in.png]") == ""
    assert render_sent_files_note("[SEND:]") == ""
    assert render_sent_files_note("") == ""


def test_sent_files_note_survives_no_scanner_and_no_display() -> None:
    """Two layers must both refuse the note: the Channel scanner and the user.

    The scanner is the dangerous one — a note it recognised would deliver the
    file a second time. The display strip is the fail-open one: the note lives
    inside an elision handle, so if it broke the handle's ``[…]`` shape the user
    would start seeing it as noise (measured in production: four assistant rows
    leaked a bare handle into the visible transcript).
    """
    note = render_sent_files_note("[SEND:/w/[SEND:偷渡.pdf].md][SEND:/w/正常.pdf]")

    assert extract_send_paths(note) == [], note
    handle = ELISION_HANDLE_TEMPLATE.format(kind="assistant", chars=3000, label="", sent=note, handle="a#1")
    assert strip_transfer_markers("文档写好了" + handle) == "文档写好了"


def test_extract_send_paths() -> None:
    assert extract_send_paths("见\n[SEND:/tmp/a.html]\n[SEND: b.md ]") == ["/tmp/a.html", "b.md"]
    assert extract_send_paths("see\n[ SEND:/tmp/a.html ]\n[SEND: b.md ]") == ["/tmp/a.html", "b.md"]
    assert extract_send_paths("see\n[Send:/tmp/a.html]\n[send: b.md ]") == ["/tmp/a.html", "b.md"]
    assert extract_send_paths("[RECV:/x]") == []
    assert extract_send_paths("") == []


def test_extract_recv_paths() -> None:
    from psi_agent.session.history_display import extract_recv_paths

    assert extract_recv_paths("看图\n[RECV:/tmp/a.png]\n[RECV: b.pdf ]") == ["/tmp/a.png", "b.pdf"]
    assert extract_recv_paths("see\n[ RECV:/tmp/a.png ]") == ["/tmp/a.png"]
    assert extract_recv_paths("[SEND:/x]") == []
    assert extract_recv_paths("[RECV:]") == []
    assert extract_recv_paths("") == []


def test_assistant_reasoning_goes_out_as_reasoning_content() -> None:
    """We store ``reasoning``; providers only read ``reasoning_content``.

    Measured against the live endpoint, the key name is the only thing that
    decides acceptance: ``reasoning`` → HTTP 400, ``reasoning_content`` → OK.
    """
    projected = project_history_for_wire(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "答案", "reasoning": "先想清楚再答。", "kind": KIND_CHAT},
        ]
    )
    assert projected == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "答案", "reasoning_content": "先想清楚再答。"},
    ]


def test_reasoning_rename_preserves_tool_call_only_turns() -> None:
    """A tool-call turn carries no ``content``; renaming must not drop it."""
    calls = [{"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]
    projected = project_history_for_wire(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "tool_calls": calls, "reasoning": "该调工具了。"},
            {"role": "tool", "tool_call_id": "call_1", "name": "f", "content": "ok"},
        ]
    )
    assert projected[1] == {"role": "assistant", "tool_calls": calls, "reasoning_content": "该调工具了。"}
    assert projected[2]["role"] == "tool"


def test_reasoning_rename_edge_cases() -> None:
    # An explicit provider-shaped value wins over our own key.
    both = project_history_for_wire(
        [{"role": "assistant", "content": "a", "reasoning": "ours", "reasoning_content": "theirs"}]
    )
    assert both == [{"role": "assistant", "content": "a", "reasoning_content": "theirs"}]

    # any-llm's ``Reasoning`` shape, should it ever reach storage.
    nested = project_history_for_wire([{"role": "assistant", "content": "a", "reasoning": {"content": "深思"}}])
    assert nested == [{"role": "assistant", "content": "a", "reasoning_content": "深思"}]

    # Empty / blank thinking is not worth a wire field.
    for blank in ("", "   ", None):
        assert project_history_for_wire([{"role": "assistant", "content": "a", "reasoning": blank}]) == [
            {"role": "assistant", "content": "a"}
        ]

    # Non-assistant roles keep their keys untouched.
    assert project_history_for_wire([{"role": "user", "content": "a", "reasoning": "x"}]) == [
        {"role": "user", "content": "a", "reasoning": "x"}
    ]


def test_with_created_at_is_idempotent() -> None:
    stamped = with_created_at({"role": "user", "content": "hi"}, when="2026-09-12T01:00:00.000Z")
    assert stamped[CREATED_AT_KEY] == "2026-09-12T01:00:00.000Z"
    again = with_created_at(stamped, when="2026-09-12T99:00:00.000Z")
    assert again[CREATED_AT_KEY] == "2026-09-12T01:00:00.000Z"


def test_project_history_strips_display_timing_keys() -> None:
    projected = project_history_for_wire(
        [
            {
                "role": "user",
                "content": "hi",
                "kind": KIND_CHAT,
                CREATED_AT_KEY: "2026-09-12T01:00:00.000Z",
            },
            {
                "role": "assistant",
                "content": "ok",
                "kind": KIND_CHAT,
                CREATED_AT_KEY: "2026-09-12T01:00:05.000Z",
                THINKING_MS_KEY: 5000,
            },
        ]
    )
    assert projected == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
    ]
    for row in projected:
        assert CREATED_AT_KEY not in row
        assert THINKING_MS_KEY not in row
