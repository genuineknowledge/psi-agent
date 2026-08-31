"""Logic-level smoke tests: signing round-trip, token map rules, workspace
separation, rate limiting, gateway body translation, attachments (P2-1).
No Gateway or network needed."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import openpyxl
import pytest
from bff.config import BffConfig, BffConfigError, load_token_map
from bff.export import (
    _estimate_lines,
    _inline,
    _pair_rounds,
    _pdf_story_from_markdown,
    _plain_text,
    build_excel,
    build_pdf,
)
from bff.identity import SessionSigner, compare_digest, workspace_for
from bff.main import MAX_ATTACHMENT_BYTES, _attachments, _gateway_chat_body
from bff.ratelimit import SlidingWindowLimiter
from bff.report import (
    WeeklySummaryData,
    _first_group_metric,
    _rows,
    build_summary_document,
    build_summary_document_text,
)
from docx import Document
from fastapi import HTTPException
from reportlab.platypus import Paragraph, Table


def _config(**overrides: object) -> object:
    base = {
        "gateway_base_url": "http://127.0.0.1:8766",
        "session_secret": "test-secret",
        "listen_host": "127.0.0.1",
        "listen_port": 8780,
    }
    return BffConfig(**{**base, **overrides})


def test_session_roundtrip() -> None:
    signer = SessionSigner("secret-a")
    token = signer.issue("alice")
    assert signer.verify(token) == "alice"
    assert signer.verify("forged.token") is None
    assert signer.verify("") is None


def test_sessions_signed_by_another_secret_rejected() -> None:
    token = SessionSigner("secret-a").issue("alice")
    assert SessionSigner("secret-b").verify(token) is None


def test_workspaces_are_per_user() -> None:
    config = _config(workspace_root="D:/ws")
    assert workspace_for("alice", config).endswith("alice")
    assert workspace_for("bob", config).endswith("bob")
    assert workspace_for("alice", config) != workspace_for("bob", config)


def test_workspace_username_sanitized() -> None:
    config = _config(workspace_root="D:/ws")
    # The username segment itself must carry no path separators or dots —
    # the root's own separators are not what this guards against.
    assert Path(workspace_for("a/../b", config)).name == "ab"
    assert Path(workspace_for("a\\b", config)).name == "ab"


def test_token_map_rejects_shared_token(tmp_path: Path) -> None:
    map_file = tmp_path / "tokens.json"
    map_file.write_text('{"a": {"token": "t1"}, "b": {"token": "t1"}}', encoding="utf-8")
    with pytest.raises(BffConfigError):
        load_token_map(str(map_file))


def test_token_map_loads(tmp_path: Path) -> None:
    map_file = tmp_path / "tokens.json"
    map_file.write_text('{"a": {"token": "t1", "workspace_id": "w1"}}', encoding="utf-8")
    result = load_token_map(str(map_file))
    assert result["a"]["token"] == "t1"


def test_rate_limit_window() -> None:
    limiter = SlidingWindowLimiter(per_minute=3)
    assert limiter.allow("alice") and limiter.allow("alice") and limiter.allow("alice")
    assert not limiter.allow("alice")
    assert limiter.allow("bob")  # other users unaffected


def test_compare_digest() -> None:
    assert compare_digest("demo", "demo")
    assert not compare_digest("demo", "nope")


def test_workspace_defaults_to_temp_dir() -> None:
    config = _config(workspace_root="")
    path = workspace_for("alice", config)
    assert path.endswith("alice")
    assert "guoshu-weekly-workspaces" in path


def test_gateway_body_translates_messages_to_chunks() -> None:

    body = _gateway_chat_body({"messages": [{"role": "user", "content": "你好"}]})
    assert body == {"chunks": [{"type": "text", "text": "你好"}]}


def test_gateway_body_injects_identity_instruction() -> None:

    body = _gateway_chat_body(
        {"messages": [{"role": "user", "content": "本周进展?"}], "identity": "领导", "preference": "结论优先"}
    )
    text = body["chunks"][0]["text"]
    assert "领导视角" in text and "先给结论" in text and text.endswith("本周进展?")


def test_gateway_body_ignores_unknown_identity() -> None:

    body = _gateway_chat_body({"messages": [{"role": "user", "content": "你好"}], "identity": "局长"})
    assert body["chunks"][0]["text"] == "你好"


def test_gateway_body_passes_through_non_messages() -> None:

    raw = {"chunks": [{"type": "text", "text": "direct"}]}
    assert _gateway_chat_body(raw) is raw


# ── P2-1 attachments ────────────────────────────────────────────────────────


def test_gateway_body_appends_blob_chunks_for_attachments() -> None:

    body = _gateway_chat_body(
        {"messages": [{"role": "user", "content": "分析这个表"}], "files": [{"name": "a.xlsx", "data": "QUJD"}]}
    )
    assert body["chunks"] == [
        {"type": "text", "text": "分析这个表"},
        {"type": "blob", "name": "a.xlsx", "data": "QUJD"},
    ]


def test_gateway_body_omits_text_chunk_when_question_empty() -> None:

    body = _gateway_chat_body(
        {"messages": [{"role": "user", "content": ""}], "files": [{"name": "a.pdf", "data": "QUJD"}]}
    )
    assert body["chunks"] == [{"type": "blob", "name": "a.pdf", "data": "QUJD"}]


def test_attachments_reject_invalid_base64() -> None:

    with pytest.raises(HTTPException, match="not valid base64"):
        _attachments([{"name": "a.txt", "data": "!!!"}])


def test_attachments_reject_oversized_file() -> None:

    payload = [{"name": "big.bin", "data": base64.b64encode(b"x" * (MAX_ATTACHMENT_BYTES + 1)).decode()}]
    with pytest.raises(HTTPException, match="exceeds"):
        _attachments(payload)


def test_attachments_reject_too_many() -> None:

    with pytest.raises(HTTPException, match="at most"):
        _attachments([{"name": f"f{i}", "data": "QUJD"} for i in range(6)])


def test_attachments_strip_directory_from_name() -> None:

    result = _attachments([{"name": "../../etc/passwd", "data": "QUJD"}])
    assert result == [{"name": "passwd", "data": "QUJD"}]


def test_summary_document_builds_docx_bytes() -> None:

    data = WeeklySummaryData(
        snapshot_note="演示数据快照 2026-08-15",
        caliber="is_deleted = 0 AND workflow_status = 'published'",
        status_rows=[
            {"status_label": "未开始", "cnt": 14},
            {"status_label": "进行中", "cnt": 78},
            {"status_label": "已完成", "cnt": 31},
            {"status_label": "已停用", "cnt": 5},
        ],
        board_rows=[{"group_name": "技术组重点任务进展", "cnt": 82}, {"group_name": "集团重点任务调度", "cnt": 46}],
        freshness_rows=[{"board_name": "技术组", "latest_progress": "2026-08-09 22:30"}],
        stale_rows=[
            {"freshness_bucket": "30 天内", "task_count": 100},
            {"freshness_bucket": "30-90 天", "task_count": 19},
        ],
        group_rows=[
            {"project_group": "标准安全组", "cnt": 19, "finish_rate_pct": "5.3"},
            {"project_group": "平台研发组", "cnt": 8, "finish_rate_pct": "37.5"},
        ],
        group_stale_rows=[
            {"bucket": "标准安全组", "stale_pct": "52.6"},
            {"bucket": "数据基础设施组", "stale_pct": "66.7"},
        ],
        never_reported_rows=[
            {"task_id": 7, "task_name": "行业大模型底座建设", "status": 1},
        ],
        milestone_row={"total": 474, "finished": 242, "finish_rate_pct": "51.1"},
        year_goal_rows=[{"year": 2025, "goal_count": 105, "task_count": 105}],
    )
    document = build_summary_document(data)
    assert document.startswith(b"PK")  # docx is a zip container
    assert b"word/document.xml" in document

    text = build_summary_document_text(data)
    assert "周报总结" in text and "演示库" in text


def test_summary_document_covers_all_sections() -> None:
    """The richer report must mention every section, including the derived
    prose (completion rate, stale-top group, never-reported count)."""

    data = WeeklySummaryData(
        snapshot_note="演示数据快照 2026-08-15",
        status_rows=[
            {"status_label": "未开始", "cnt": 14},
            {"status_label": "进行中", "cnt": 78},
            {"status_label": "已完成", "cnt": 31},
            {"status_label": "已停用", "cnt": 5},
        ],
        group_rows=[
            {"project_group": "标准安全组", "finish_rate_pct": "5.3"},
            {"project_group": "平台研发组", "finish_rate_pct": "37.5"},
        ],
        group_stale_rows=[{"bucket": "数据基础设施组", "stale_pct": "66.7"}],
        never_reported_rows=[
            {"task_id": 7, "task_name": "行业大模型底座建设", "status": 1, "has_group_history": 0},
        ],
        milestone_row={"total": 474, "finished": 242, "finish_rate_pct": "51.1"},
        year_goal_rows=[{"year": 2025, "goal_count": 105, "task_count": 105}],
    )
    document = Document(__import__("io").BytesIO(build_summary_document(data)))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "总体概况" in text
    assert "24.2%" in text  # 31 / 128
    assert "标准安全组" in text and "5.3" in text  # lowest completion-rate group
    assert "66.7" in text  # highest stale share (scanned, not assumed order)
    assert "从未上报" in text
    assert "里程碑" in text and "年度目标" in text


def test_group_metric_scans_instead_of_trusting_order() -> None:

    rows = [
        {"bucket": "算力网络组", "stale_pct": "35.7"},
        {"bucket": "数据基础设施组", "stale_pct": "66.7"},
        {"bucket": "标准安全组", "stale_pct": "52.6"},
    ]
    name, value = _first_group_metric(rows, "stale_pct", lowest=False)
    assert name == "数据基础设施组" and value == "66.7"
    name, value = _first_group_metric(rows, "stale_pct", lowest=True)
    assert name == "算力网络组" and value == "35.7"


def test_summary_rows_filter_non_dict() -> None:

    payload = {"ok": True, "rows": [{"a": 1}, "not-a-dict", None]}
    assert _rows(payload) == [{"a": 1}]


def test_export_strips_injection_prefix_from_questions() -> None:

    messages = [
        {"role": "user", "text": "【本次回答要求】领导视角:结论先行。\n\n问题:你叫什么"},
        {"role": "assistant", "text": "我是周报智能体。"},
    ]
    rounds = _pair_rounds(messages)
    assert rounds == [("你叫什么", "我是周报智能体。")]


def test_export_keeps_plain_questions_untouched() -> None:

    rounds = _pair_rounds([{"role": "user", "text": "技术组多少任务?"}])
    assert rounds == [("技术组多少任务?", "")]


def test_plain_text_strips_markdown_decorators() -> None:

    markdown = "## 结论\n\n**标准安全组**完成率最低\n| 组 | 完成率 |\n| --- | --- |\n| 甲 | 5.3% |"
    text = _plain_text(markdown)
    assert "**" not in text and "#" not in text
    assert "| 甲 | 5.3% |" in text  # table pipes survive as column separators


def test_estimate_lines_counts_wrapped_lines() -> None:

    assert _estimate_lines("", 26) == 1  # one blank line at minimum
    assert _estimate_lines("abc", 26) == 1
    assert _estimate_lines("a" * 27, 26) == 2
    assert _estimate_lines("a\nbb", 26) == 2  # explicit newline adds a line


def test_excel_rows_are_sized_from_content() -> None:

    messages = [
        {"role": "user", "text": "技术组多少任务?"},
        {"role": "assistant", "text": "短回答。"},
        {"role": "user", "text": "第二问"},
        {"role": "assistant", "text": "长回答。" * 300},
    ]
    workbook_bytes = build_excel(messages)
    workbook = openpyxl.load_workbook(io.BytesIO(workbook_bytes))
    sheet = workbook.active
    assert sheet.cell(row=2, column=2).value == "技术组多少任务?"
    # The long-answer round must get a taller row than the short one.
    assert sheet.row_dimensions[3].height > sheet.row_dimensions[2].height


def test_pdf_story_renders_markdown_table_natively() -> None:

    markdown = "## 结论\n\n**标准安全组**完成率最低\n\n| 专项组 | 完成率 |\n| --- | --- |\n| 标准安全组 | 5.3% |"
    story = _pdf_story_from_markdown(markdown)
    assert any(isinstance(block, Table) for block in story)
    # No raw pipe rows survive as paragraphs.
    paragraphs = [block for block in story if isinstance(block, Paragraph)]
    assert not any("|" in str(block.text) for block in paragraphs)


def test_pdf_inline_escapes_before_marking_up() -> None:

    expected = '<b>粗体</b> <font face="Courier" size="9">代码</font> &lt;x&gt; &amp;'
    assert _inline("**粗体** `代码` <x> &") == expected


def test_pdf_builds_with_markdown_answer() -> None:

    messages = [
        {"role": "user", "text": "各专项组完成率?"},
        {
            "role": "assistant",
            "text": "| 专项组 | 完成率 |\n| --- | --- |\n| 标准安全组 | 5.3% |\n| 平台研发组 | 37.5% |",
        },
    ]
    pdf_bytes = build_pdf(messages)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 2000
