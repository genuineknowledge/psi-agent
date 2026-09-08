from __future__ import annotations

# Local meeting tools intentionally load from the agent package rather than an installed package.
# ruff: noqa: E402
import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import meeting_pipeline_run as pipeline  # ty: ignore[unresolved-import]
import meeting_session_read as session_read  # ty: ignore[unresolved-import]
from _meeting_archive import archive_meeting_record  # ty: ignore[unresolved-import]
from _meeting_automation import meeting_artifact_root  # ty: ignore[unresolved-import]


def _seed_artifacts(root: Path, meeting_name: str = "weekday-alignment", record: str = "record-1") -> None:
    artifact = meeting_artifact_root(str(root), meeting_name)
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "transcript.md").write_text("转写原文", encoding="utf-8")
    (artifact / "smart_minutes.json").write_text('{"minutes": "纪要"}', encoding="utf-8")
    (artifact / "analysis.md").write_text("分析结果", encoding="utf-8")
    (artifact / "manifest.json").write_text(
        json.dumps({"record_file_id": record, "processed_record_file_ids": [record]}), encoding="utf-8"
    )


@pytest.mark.anyio
async def test_archive_snapshots_all_artifacts_and_is_idempotent(tmp_path: Path) -> None:
    _seed_artifacts(tmp_path, record="record-1")
    result = await archive_meeting_record(str(tmp_path), "weekday-alignment", "record-1")
    assert result["ok"], result
    archive_dir = Path(result["archive_dir"])
    assert archive_dir == meeting_artifact_root(str(tmp_path), "weekday-alignment") / "archive" / "record-1"
    assert (archive_dir / "transcript.md").read_text(encoding="utf-8") == "转写原文"
    assert json.loads((archive_dir / "smart_minutes.json").read_text(encoding="utf-8"))["minutes"] == "纪要"
    assert (archive_dir / "analysis.md").read_text(encoding="utf-8") == "分析结果"
    assert json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))["record_file_id"] == "record-1"
    # 幂等: 再跑一次不炸、文件仍一致。
    again = await archive_meeting_record(str(tmp_path), "weekday-alignment", "record-1")
    assert again["ok"]
    assert (archive_dir / "transcript.md").read_text(encoding="utf-8") == "转写原文"


@pytest.mark.anyio
async def test_archive_rejects_unsafe_record_id(tmp_path: Path) -> None:
    _seed_artifacts(tmp_path)
    result = await archive_meeting_record(str(tmp_path), "weekday-alignment", "../../etc")
    assert not result["ok"]
    assert "unsafe record_file_id" in result["error"]


@pytest.mark.anyio
async def test_pipeline_completion_writes_record_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = meeting_artifact_root(str(tmp_path), "weekday-alignment")
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "manifest.json").write_text(
        json.dumps({"record_file_id": "record-arch", "chunk_count": 1, "transcript_chars": 4}), encoding="utf-8"
    )
    (artifact / "pipeline_state.json").write_text(
        json.dumps(
            {
                "record_file_id": "record-arch",
                "status": "notifications_pending",
                "analysis_text": "已保存分析",
                "meeting_summary": "会议纪要",
                "positive_negative_overview": "正负面总览",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (artifact / "transcript.md").write_text("原文", encoding="utf-8")
    (artifact / "analysis.md").write_text("已保存分析", encoding="utf-8")

    async def fake_prepare(**_kwargs: object) -> str:
        return '{"ok":true,"status":"already_processed"}'

    async def fake_write(**_kwargs: object) -> str:
        return '{"ok":true,"status":"ok"}'

    def fake_render(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": "会议总结"}},
                "body": {"elements": []},
            },
            "handlers": {},
        }

    async def fake_notify(**kwargs: object) -> str:
        return json.dumps({"ok": True, "status": "sent", "recipient": kwargs["recipient"]})

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fake_prepare)
    monkeypatch.setattr(pipeline, "render_meeting_summary_card", fake_render)
    monkeypatch.setattr(pipeline, "notify_meeting_card", fake_notify)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)
    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )
    assert result["status"] == "completed"
    archive_dir = artifact / "archive" / "record-arch"
    assert (archive_dir / "transcript.md").read_text(encoding="utf-8") == "原文"
    assert (archive_dir / "analysis.md").read_text(encoding="utf-8") == "已保存分析"
    assert json.loads((archive_dir / "manifest.json").read_text(encoding="utf-8"))["record_file_id"] == "record-arch"
    assert json.loads((archive_dir / "pipeline_state.json").read_text(encoding="utf-8"))["status"] == "completed"


@pytest.mark.anyio
async def test_session_read_can_target_archive_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_artifacts(tmp_path, record="record-hist")
    await archive_meeting_record(str(tmp_path), "weekday-alignment", "record-hist")
    # 覆盖最新副本, 让"历史档可读"与"最新被覆盖"形成对比。
    (meeting_artifact_root(str(tmp_path), "weekday-alignment") / "transcript.md").write_text(
        "新场原文", encoding="utf-8"
    )

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(session_read, "resolve_appdata_root", resolve_root)
    result = json.loads(
        await session_read.meeting_session_read(
            meeting_name="weekday-alignment",
            artifact="transcript",
            record_file_id="record-hist",
            appdata_root=str(tmp_path),
        )
    )
    assert result["ok"] and result["content"] == "转写原文"
    # 默认(不带 record_file_id)仍读最新副本。
    latest = json.loads(
        await session_read.meeting_session_read(
            meeting_name="weekday-alignment", artifact="transcript", appdata_root=str(tmp_path)
        )
    )
    assert latest["ok"] and latest["content"] == "新场原文"
