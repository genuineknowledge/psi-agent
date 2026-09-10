"""Contract tests for the meeting record export tool (read-only packaging)."""

# ruff: noqa: RUF002, RUF003

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

export = importlib.import_module("meeting_record_export")  # ty: ignore[unresolved-import]

#: 转写 record_file_id（管道 manifest 里的那个）
RECORD_ID = "2097519504906416129"
#: 云录制文件 record_file_id（腾讯录制列表里的那个 —— 与转写 id 不同，实测）
RECORDING_ID = "2097519494798155777"
MEETING = "weekday-alignment-1100"
ADDRESS = "https://meeting.tencent.com/crw/abc"


def _seed_store(tmp_path: Path, *, archived: bool = False) -> Path:
    """造一份管道产物（含不该导出的内部文件）。"""
    root = tmp_path / "meeting-session" / MEETING
    target = root / "archive" / RECORD_ID if archived else root
    target.mkdir(parents=True, exist_ok=True)
    (target / "transcript.md").write_text("# 转写\n[00:01] 高博: 测试发言\n", encoding="utf-8")
    (target / "transcript_paragraphs.json").write_text("[]", encoding="utf-8")
    (target / "smart_minutes.json").write_text("{}", encoding="utf-8")
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "meeting_name": MEETING,
                "meeting_code": "42654699903",
                "record_file_id": RECORD_ID,
                "transcript_chars": 20,
                "status": "ready",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (target / "analysis.md").write_text("## 本场 SOP 判定\n", encoding="utf-8")
    (target / "analysis.json").write_text("{}", encoding="utf-8")
    (target / "pipeline_state.json").write_text('{"status": "completed"}', encoding="utf-8")
    (target / "notification_receipts.json").write_text('{"x": {"recipient": "ou_x"}}', encoding="utf-8")
    return root


def _envelope(tool_result: dict[str, Any]) -> dict[str, Any]:
    """真实响应形状: jsonrpc 双层 + content[0].text 里再套一层 JSON 字符串。"""
    inner = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}]},
    }
    return {"jsonrpc": "2.0", "id": 1, "result": inner}


def _records_payload(*, include_transcript_id: bool = False, with_address: bool = True) -> dict[str, Any]:
    record: dict[str, Any] = {
        "meeting_record_id": "2097519494798155776",
        "record_file_id": RECORD_ID if include_transcript_id else RECORDING_ID,
        "state": "转码完成",
        "record_start_time": "2026-09-09 10:56:32",
        "record_end_time": "2026-09-09 13:08:23",
    }
    if with_address:
        record["view_address"] = ADDRESS
    return _envelope({"record_meetings": [{"record_files": [record]}]})


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    records_payload: dict[str, Any] | None,
    *,
    addresses_payload: dict[str, Any] | None = None,
    raise_error: bool = False,
) -> list[tuple[str, dict[str, Any], str]]:
    async def fake_root(appdata_root: str = "") -> str:
        return str(tmp_path)

    monkeypatch.setattr(export, "resolve_appdata_root", fake_root)
    calls: list[tuple[str, dict[str, Any], str]] = []
    if records_payload is None and addresses_payload is None:
        return calls

    async def fake_call(name: str, arguments: dict[str, Any], *, token_env: str = "") -> Any:
        calls.append((name, arguments, token_env))
        if raise_error:
            raise RuntimeError("Tencent Meeting RPC error: upstream down")
        if name == "get_records_list":
            return records_payload
        if name == "get_record_addresses":
            return addresses_payload or _envelope({"download_address": ADDRESS})
        raise AssertionError(f"unexpected tool {name}")

    monkeypatch.setattr(export, "_tencent_call", fake_call)
    return calls


def test_export_latest_packages_content_and_omits_internal_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """最新一场: 内容齐、内部状态不导出、云端信息进信息页并标注近似匹配。"""
    _seed_store(tmp_path)
    calls = _patch(monkeypatch, tmp_path, _records_payload())

    result = json.loads(asyncio.run(export.meeting_record_export(MEETING, appdata_root=str(tmp_path))))

    assert result["ok"] is True and result["status"] == "exported"
    assert result["record_file_id"] == RECORD_ID
    assert result["meeting_date"] == "2026-09-09"
    assert calls and calls[0][0] == "get_records_list"
    assert calls[0][2] == "TENCENT_MEETING_TOKEN_42654699903"
    dest = Path(result["output_dir"])
    names = {item.name for item in dest.iterdir()}
    assert {"transcript.md", "analysis.md", "manifest.json", "录制与转写信息.md"} <= names
    assert "notification_receipts.json" not in names
    assert "pipeline_state.json" not in names
    md = (dest / "录制与转写信息.md").read_text(encoding="utf-8")
    assert "42654699903" in md and RECORD_ID in md
    assert ADDRESS in md
    assert "TENCENT_MEETING_TOKEN_42654699903" in md
    assert "近似匹配" in md  # 转写 id 与录制 id 不同值, 必须如实标注
    assert result["record_links"]["view_address"] == ADDRESS


def test_export_fetches_address_by_recording_id_when_list_has_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """列表不带地址时按云录制文件 id 现取一次地址(短时效链接)。"""
    _seed_store(tmp_path)
    calls = _patch(monkeypatch, tmp_path, _records_payload(include_transcript_id=True, with_address=False))

    result = json.loads(asyncio.run(export.meeting_record_export(MEETING, appdata_root=str(tmp_path))))

    assert result["ok"] is True
    assert [name for name, _, _ in calls] == ["get_records_list", "get_record_addresses"]
    # 腾讯要求 meeting_record_id 必填(只给 record_file_id 会报缺参数), 两个都带上
    assert calls[1][1]["meeting_record_id"] == "2097519494798155776"
    assert calls[1][1]["record_file_id"] == RECORD_ID
    assert result["record_links"]["view_address"] == ADDRESS
    md = (Path(result["output_dir"]) / "录制与转写信息.md").read_text(encoding="utf-8")
    assert ADDRESS in md and "近似匹配" not in md


def test_export_reports_tencent_failure_without_blocking_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """腾讯接口失败时资料包照常导出, 但明确标出录制链接缺失。"""
    _seed_store(tmp_path)
    _patch(monkeypatch, tmp_path, _records_payload(), raise_error=True)

    result = json.loads(asyncio.run(export.meeting_record_export(MEETING, appdata_root=str(tmp_path))))

    assert result["ok"] is True and result["status"] == "exported"
    assert "RuntimeError" in result["record_error"]
    md = (Path(result["output_dir"]) / "录制与转写信息.md").read_text(encoding="utf-8")
    assert "录制元信息未取到" in md


def test_export_named_archive_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """给 record_file_id 时读永久归档, 而不是共享存储的最新文件。"""
    root = _seed_store(tmp_path, archived=True)
    (root / "transcript.md").write_text("最新一场的转写", encoding="utf-8")
    _patch(monkeypatch, tmp_path, None)

    result = json.loads(
        asyncio.run(
            export.meeting_record_export(
                MEETING, record_file_id=RECORD_ID, include_record_links=False, appdata_root=str(tmp_path)
            )
        )
    )

    assert result["ok"] is True
    exported = (Path(result["output_dir"]) / "transcript.md").read_text(encoding="utf-8")
    assert "测试发言" in exported and "最新一场的转写" not in exported
    assert "record_error" not in result


def test_export_rejects_unknown_meeting_and_missing_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """白名单外的会议名显式拒绝并给出可选值; 不存在的场次也显式失败。"""
    _patch(monkeypatch, tmp_path, None)

    unknown = json.loads(asyncio.run(export.meeting_record_export("algorithm-group", appdata_root=str(tmp_path))))
    assert unknown["ok"] is False and unknown["status"] == "unknown_meeting"
    assert MEETING in unknown["allowed_meetings"]

    _seed_store(tmp_path)
    missing = json.loads(
        asyncio.run(export.meeting_record_export(MEETING, record_file_id="9999", appdata_root=str(tmp_path)))
    )
    assert missing["ok"] is False and missing["status"] == "record_not_found"


def test_export_refuses_output_dir_inside_pipeline_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """output_dir 落在会议产物目录内必须拒绝(只读边界)。"""
    root = _seed_store(tmp_path)
    _patch(monkeypatch, tmp_path, None)

    result = json.loads(
        asyncio.run(
            export.meeting_record_export(
                MEETING, include_record_links=False, output_dir=str(root / "export"), appdata_root=str(tmp_path)
            )
        )
    )

    assert result["ok"] is False and result["status"] == "unsafe_output_dir"
