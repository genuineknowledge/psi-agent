from __future__ import annotations

# Local meeting tools intentionally load from the agent package rather than an installed package.
# ruff: noqa: E402
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import meeting_pipeline_run as pipeline  # ty: ignore[unresolved-import]
import meeting_session_notify as notify  # ty: ignore[unresolved-import]
import meeting_transcript_prepare as transcript_prepare  # ty: ignore[unresolved-import]
import tencent_meeting  # ty: ignore[unresolved-import]
from _meeting_automation import (  # ty: ignore[unresolved-import]
    MEETING_JOBS,
    MEETING_SESSION_ID,
    _json_payload,
    chunk_text,
    ensure_meeting_scheduler,
    extract_latest_transcript_record,
    meeting_credential_env,
    meeting_workspace,
    provision_meeting_workspace,
    read_meeting_manifest,
    render_transcript_paragraphs,
    should_process_recording,
)
from meeting_session_read import meeting_session_read  # ty: ignore[unresolved-import]
from meeting_session_write import meeting_session_write  # ty: ignore[unresolved-import]
from meeting_transcript_prepare import meeting_transcript_prepare  # ty: ignore[unresolved-import]

from psi_agent.session.agent import _CURRENT_TOOL_AI_SOCKET
from psi_agent.session.protocol import AiDelta


def test_daily_meeting_uses_its_fixed_credential_only_for_its_fixed_meeting() -> None:
    assert meeting_credential_env("weekday-alignment", "57152787045") == "TENCENT_MEETING_TOKEN"
    assert meeting_credential_env("weekday-alignment-1100", "42654699903") == "TENCENT_MEETING_TOKEN_42654699903"
    with pytest.raises(ValueError, match="does not match"):
        meeting_credential_env("weekday-alignment", "91786308915")


@pytest.mark.anyio
async def test_private_tencent_call_injects_selected_environment_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_run_process(args, *, check: bool, env: dict[str, str]):
        captured.update(env)
        return SimpleNamespace(returncode=0, stdout=b'{"ok":true}', stderr=b"")

    monkeypatch.setenv("TENCENT_MEETING_TOKEN", "daily-secret")
    monkeypatch.setattr(tencent_meeting.anyio, "run_process", fake_run_process)

    result = await tencent_meeting._tencent_meeting_call_with_token_env("tools/list", token_env="TENCENT_MEETING_TOKEN")

    assert result == '{"ok":true}'
    assert captured["TENCENT_MEETING_TOKEN"] == "daily-secret"


def test_meeting_jobs_use_fixed_post_meeting_crons() -> None:
    jobs = {job.name: job for job in MEETING_JOBS}
    assert jobs["weekday-alignment"].meeting_code == "57152787045"
    assert jobs["weekday-alignment"].cron == "0 12 * * 1,3,5"
    assert jobs["weekday-alignment"].summary_recipients == ("程秀秀",)
    assert jobs["weekday-alignment"].overview_recipients == ("罗霖",)
    assert jobs["weekday-alignment-1100"].meeting_code == "42654699903"
    assert jobs["weekday-alignment-1100"].cron == "0 12 * * 1,3,5"
    assert jobs["weekday-alignment-1100"].recipients == ("HaiTun Agent主战场", "罗霖")
    assert jobs["weekday-alignment-1100"].token_env == "TENCENT_MEETING_TOKEN_42654699903"
    assert jobs["weekday-alignment-1100"].summary_recipients == ("HaiTun Agent主战场",)
    assert jobs["weekday-alignment-1100"].overview_recipients == ("罗霖",)
    assert len(jobs) == 2
    assert jobs["weekday-alignment"].fire == "tool"
    assert jobs["weekday-alignment"].tool_name == "meeting_pipeline_run"


def test_meeting_workspace_is_separate_and_stable(tmp_path: Path) -> None:
    first = meeting_workspace(tmp_path)
    second = meeting_workspace(str(tmp_path))
    assert first == second
    assert first != tmp_path
    assert first.name == ".meeting-session"
    assert MEETING_SESSION_ID == "meeting-session"


def test_chunk_text_preserves_full_text_and_bounds_each_chunk() -> None:
    source = "一" * 25_001
    chunks = chunk_text(source, max_chars=8_000)
    assert len(chunks) == 4
    assert all(len(chunk) <= 8_000 for chunk in chunks)
    assert "".join(chunks) == source


def test_recording_dedup_uses_record_file_id_and_state() -> None:
    assert should_process_recording({"record_file_id": "r1", "state_int": 3}, set())
    assert not should_process_recording({"record_file_id": "r1", "state_int": 3}, {"r1"})
    assert not should_process_recording({"record_file_id": "r2", "state_int": 2}, set())
    assert not should_process_recording({"record_file_id": "", "state_int": 3}, set())


@pytest.mark.anyio
async def test_provision_writes_only_schedule_files(tmp_path: Path) -> None:
    workspace = await provision_meeting_workspace(tmp_path)
    assert workspace == meeting_workspace(tmp_path)
    for job in MEETING_JOBS:
        task = workspace / "schedules" / job.name / "TASK.md"
        body = task.read_text(encoding="utf-8")
        assert f'cron: "{job.cron}"' in body
        assert job.meeting_code in body
        assert "原始全文转写" in body


@pytest.mark.anyio
async def test_provision_removes_stale_meeting_schedule_tasks(tmp_path: Path) -> None:
    stale = tmp_path / ".meeting-session" / "schedules" / "weekly-review"
    stale.mkdir(parents=True)
    (stale / "TASK.md").write_text('---\nname: weekly-review\ncron: "0 17 * * 6"\n---\n', encoding="utf-8")

    await provision_meeting_workspace(tmp_path)

    assert not (stale / "TASK.md").exists()


def test_extract_completed_text_record_selects_latest_transcript() -> None:
    payload = {
        "records": [
            {
                "record_file_id": "video-old",
                "record_file_type": "视频",
                "state_int": 3,
                "start_time": "2026-09-05T15:00:00+08:00",
            },
            {
                "record_file_id": "text-pending",
                "record_file_type": "文字转写",
                "state_int": 2,
                "start_time": "2026-09-05T15:02:00+08:00",
            },
            {
                "record_file_id": "text-new",
                "record_file_type": "文字转写",
                "state_int": 3,
                "start_time": "2026-09-05T15:03:00+08:00",
            },
        ]
    }
    selected = extract_latest_transcript_record(payload, set())
    assert selected is not None
    assert selected["record_file_id"] == "text-new"


def test_json_payload_unwraps_tencent_http_envelope() -> None:
    payload = _json_payload(
        json.dumps(
            {
                "status_code": 200,
                "headers": {"X-Tc-Trace": "trace-1", "rpcUuid": "rpc-1"},
                "body": json.dumps({"has_more": False, "record_meetings": []}),
            }
        )
    )

    assert payload["has_more"] is False
    assert payload["record_meetings"] == []
    assert payload["_transport"]["x_tc_trace"] == "trace-1"
    assert payload["_transport"]["rpc_uuid"] == "rpc-1"


def test_extract_record_flattens_meeting_metadata_and_skips_active_occurrence() -> None:
    payload = {
        "record_meetings": [
            {
                "sub_meeting_id": "today",
                "record_type": "文字转写",
                "state_int": 1,
                "media_start_time": "2026-09-05T14:55:47+08:00",
                "record_files": [{"record_file_id": "today-active"}],
            },
            {
                "sub_meeting_id": "today",
                "record_type": "文字转写",
                "state_int": 3,
                "media_start_time": "2026-09-05T14:27:48+08:00",
                "record_files": [
                    {
                        "record_file_id": "today-short",
                        "record_start_time": "2026-09-05T14:27:52+08:00",
                    }
                ],
            },
            {
                "sub_meeting_id": "last-week",
                "record_type": "文字转写",
                "state_int": 3,
                "media_start_time": "2026-08-29T14:56:03+08:00",
                "record_files": [
                    {
                        "record_file_id": "last-week-full",
                        "record_start_time": "2026-08-29T14:56:07+08:00",
                    }
                ],
            },
        ]
    }

    selected = extract_latest_transcript_record(payload, set())

    assert selected is not None
    assert selected["record_file_id"] == "last-week-full"
    assert selected["record_file_type"] == "文字转写"
    assert selected["state_int"] == 3


def test_render_transcript_preserves_all_paragraphs_and_speakers() -> None:
    paragraphs = [
        {"pid": "0", "speaker": "甲", "start_time": "00:01", "sentences": [{"text": "先说"}]},
        {"pid": "1", "speaker_name": "乙", "timestamp": "00:02", "content": "我补充"},
    ]
    text = render_transcript_paragraphs(paragraphs)
    assert "[00:01] 甲: 先说" in text
    assert "[00:02] 乙: 我补充" in text
    assert text.count("\n") == 1


def test_render_transcript_reads_tencent_words_from_raw_sentence() -> None:
    paragraphs = [
        {
            "pid": "0",
            "speaker": "甲",
            "start_time": 0,
            "sentences": [
                {"sid": "0", "words": [{"text": "腾讯"}, {"text": "原始转写"}]},
            ],
        },
    ]

    assert render_transcript_paragraphs(paragraphs) == "甲: 腾讯原始转写"


@pytest.mark.anyio
async def test_prepare_saves_full_transcript_and_returns_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_call(method: str, params_json: str = "") -> str:
        params = json.loads(params_json)
        name = params["name"]
        calls.append((method, name))
        if name == "get_records_list":
            return json.dumps({"records": [{"record_file_id": "r1", "record_file_type": "文字转写", "state_int": 3}]})
        if name == "get_transcripts_paragraphs":
            return json.dumps({"paragraphs": [{"pid": "0"}, {"pid": "1"}]})
        if name == "get_transcripts_details":
            args = params["arguments"]
            return json.dumps(
                {"paragraphs": [{"pid": args["pid"], "speaker": "甲", "timestamp": args["pid"], "content": "x" * 6000}]}
            )
        if name == "get_smart_minutes":
            return json.dumps({"summary": "参考纪要"})
        raise AssertionError(name)

    monkeypatch.setattr("meeting_transcript_prepare.tencent_meeting_call", fake_call)
    result = json.loads(
        await meeting_transcript_prepare(
            meeting_code="57152787045", meeting_name="weekday-alignment", appdata_root=str(tmp_path)
        )
    )
    assert result["ok"] is True
    assert result["chunk_count"] >= 2
    saved_text = (tmp_path / "meeting-session" / "weekday-alignment" / "transcript.md").read_text()
    assert result["transcript_chars"] == len(saved_text)
    assert result["record_file_id"] == "r1"
    assert calls.count(("tools/call", "get_transcripts_details")) == 2
    manifest = read_meeting_manifest(tmp_path, "weekday-alignment")
    assert manifest["transcript_chars"] == len(saved_text)
    assert "x" * 6000 in saved_text


@pytest.mark.anyio
async def test_collect_paragraphs_follows_index_and_detail_cursors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call(name: str, arguments: dict[str, object], *, token_env: str) -> object:
        calls.append((name, dict(arguments)))
        if name == "get_transcripts_paragraphs":
            if arguments.get("page_token") == "index-2":
                return {"paragraphs": [{"pid": "1"}]}
            return {"paragraphs": [{"pid": "0"}], "has_more": True, "next_page_token": "index-2"}
        if name == "get_transcripts_details":
            if arguments.get("pid") == "0":
                return {
                    "paragraphs": [{"pid": "0", "content": "第一段"}],
                    "has_more": True,
                    "next_page_token": "detail-2",
                }
            if arguments.get("page_token") == "detail-2":
                return {"paragraphs": [{"pid": "2", "content": "第2段"}]}
            if arguments.get("pid") == "1":
                return {"paragraphs": [{"pid": "1", "content": "第1段"}]}
            raise AssertionError(arguments)
        raise AssertionError(name)

    monkeypatch.setattr(transcript_prepare, "_call", fake_call)
    paragraphs = await transcript_prepare._collect_paragraphs("record-1", token_env="TENCENT_MEETING_TOKEN")

    assert [item["pid"] for item in paragraphs] == ["0", "2", "1"]
    assert ("get_transcripts_paragraphs", {"record_file_id": "record-1", "page_token": "index-2"}) in calls
    assert (
        "get_transcripts_details",
        {"record_file_id": "record-1", "page_token": "detail-2", "limit": 100},
    ) in calls


@pytest.mark.anyio
async def test_collect_paragraphs_fallback_preserves_detail_token_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call(name: str, arguments: dict[str, object], *, token_env: str) -> object:
        calls.append((name, dict(arguments)))
        if name == "get_transcripts_paragraphs":
            return {"paragraphs": []}
        if name == "get_transcripts_details":
            if arguments.get("pid") == "0":
                return {"paragraphs": [{"pid": "0", "content": "第一段"}], "has_more": True, "next_token": "tail"}
            if arguments.get("page_token") == "tail":
                return {"paragraphs": [{"pid": "1", "content": "第二段"}]}
            raise AssertionError(arguments)
        raise AssertionError(name)

    monkeypatch.setattr(transcript_prepare, "_call", fake_call)
    paragraphs = await transcript_prepare._collect_paragraphs("record-2", token_env="TENCENT_MEETING_TOKEN")

    assert [item["pid"] for item in paragraphs] == ["0", "1"]
    assert (
        "get_transcripts_details",
        {"record_file_id": "record-2", "page_token": "tail", "limit": 100},
    ) in calls


@pytest.mark.anyio
async def test_meeting_session_read_returns_bounded_chunks(tmp_path: Path) -> None:
    root = tmp_path / "meeting-session" / "weekday-alignment"
    root.mkdir(parents=True)
    (root / "transcript.md").write_text("z" * 17_001, encoding="utf-8")
    result = json.loads(
        await meeting_session_read(
            meeting_name="weekday-alignment", artifact="transcript", chunk_index=2, appdata_root=str(tmp_path)
        )
    )
    assert result["ok"] is True
    assert len(result["content"]) == 1_001
    assert result["has_more"] is False


@pytest.mark.anyio
async def test_prepare_skips_already_processed_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "meeting-session" / "weekday-alignment"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({"record_file_id": "done"}), encoding="utf-8")

    async def fake_call(method: str, params_json: str = "") -> str:
        params = json.loads(params_json)
        if params["name"] == "get_records_list":
            return json.dumps({"records": [{"record_file_id": "done", "record_file_type": "文字转写", "state_int": 3}]})
        raise AssertionError("a processed recording must not fetch transcript details")

    async def fake_call_with_token_env(method: str, params_json: str = "", *, token_env: str) -> str:
        assert token_env == "TENCENT_MEETING_TOKEN"
        return await fake_call(method, params_json)

    monkeypatch.setattr("meeting_transcript_prepare.tencent_meeting_call", fake_call)
    monkeypatch.setattr(tencent_meeting, "_tencent_meeting_call_with_token_env", fake_call_with_token_env)
    result = json.loads(
        await meeting_transcript_prepare(
            meeting_code="57152787045", meeting_name="weekday-alignment", appdata_root=str(tmp_path)
        )
    )
    assert result["status"] == "already_processed"


@pytest.mark.anyio
async def test_meeting_scheduler_is_provisioned_once_for_fixed_workspace(tmp_path: Path) -> None:
    class FakeScheduler:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, str]] = []

        async def ensure(self, workspace: str, *, ai_id: str = "", agent: str = "", session_id: str = "") -> str:
            self.calls.append((workspace, ai_id, agent, session_id))
            return "scheduler-meeting"

    scheduler = FakeScheduler()
    first = await ensure_meeting_scheduler(scheduler, str(tmp_path), ai_id="ai-1", agent="agents/feishu")
    second = await ensure_meeting_scheduler(scheduler, str(tmp_path), ai_id="ai-1", agent="agents/feishu")
    assert first == second == "scheduler-meeting"
    assert scheduler.calls == [
        (str(tmp_path / ".meeting-session"), "ai-1", "agents/feishu", "meeting-session"),
        (str(tmp_path / ".meeting-session"), "ai-1", "agents/feishu", "meeting-session"),
    ]


@pytest.mark.anyio
async def test_meeting_session_write_persists_analysis_without_formal_ledger(tmp_path: Path) -> None:
    result = json.loads(
        await meeting_session_write(
            meeting_name="weekday-alignment",
            meeting_code="57152787045",
            record_file_id="record-1",
            analysis_text="正负面候选:待补充证据。\n周中对齐会 SOP:部分符合。",
            source_chunks="0, 1, 2",
            recipient_receipts_json='{"hr":{"status":"sent"}}',
            appdata_root=str(tmp_path),
        )
    )

    assert result == {
        "ok": True,
        "status": "analyzed",
        "meeting_name": "weekday-alignment",
        "formal_ledger_written": False,
    }
    artifact = tmp_path / "meeting-session" / "weekday-alignment"
    assert (artifact / "analysis.md").read_text(encoding="utf-8").startswith("正负面候选")
    payload = json.loads((artifact / "analysis.json").read_text(encoding="utf-8"))
    assert payload["meeting_code"] == "57152787045"


@pytest.mark.anyio
async def test_daily_meeting_pipeline_runs_each_stage_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_prepare(**kwargs: object) -> str:
        calls.append(("prepare", dict(kwargs)))
        artifact = tmp_path / "meeting-session" / "weekday-alignment"
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "manifest.json").write_text(
            json.dumps({"record_file_id": "record-1", "chunk_count": 2, "transcript_chars": 13}),
            encoding="utf-8",
        )
        return json.dumps({"ok": True, "status": "ready", "record_file_id": "record-1", "chunk_count": 2})

    async def fake_read(**kwargs: object) -> str:
        calls.append(("read", dict(kwargs)))
        return json.dumps(
            {
                "ok": True,
                "content": "原始片段" + str(kwargs["chunk_index"]),
                "has_more": kwargs["chunk_index"] == 0,
                "chunk_index": kwargs["chunk_index"],
            },
            ensure_ascii=False,
        )

    async def fake_analyze(transcript: str, smart_minutes: str = "", **kwargs: object) -> dict[str, str]:
        calls.append(("analyze", {"transcript": transcript, **kwargs}))
        return {
            "analysis_text": "完整分析",
            "meeting_summary": "会议纪要",
            "positive_negative_overview": "正负面总览",
        }

    async def fake_write(**kwargs: object) -> str:
        calls.append(("write", dict(kwargs)))
        return '{"ok":true,"status":"analyzed"}'

    async def fake_notify(**kwargs: object) -> str:
        calls.append(("notify", dict(kwargs)))
        return json.dumps({"ok": True, "status": "sent", "recipient": kwargs["recipient"]})

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fake_prepare)
    monkeypatch.setattr(pipeline, "meeting_session_read", fake_read)
    monkeypatch.setattr(pipeline, "_analyze_meeting_transcript", fake_analyze)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)
    monkeypatch.setattr(pipeline, "meeting_session_notify", fake_notify)

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )
    assert result["status"] == "completed"
    assert [name for name, _ in calls] == ["prepare", "read", "read", "analyze", "write", "notify", "notify", "write"]
    assert calls[2][1]["chunk_index"] == 1
    assert calls[3][1]["transcript"] == "原始片段0原始片段1"
    assert {call[1]["recipient"] for call in calls if call[0] == "notify"} == {"罗霖", "程秀秀"}


@pytest.mark.anyio
async def test_read_full_transcript_follows_has_more_beyond_manifest_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    async def fake_read(**kwargs: object) -> str:
        index = int(str(kwargs["chunk_index"]))
        seen.append(index)
        return json.dumps(
            {
                "ok": True,
                "content": f"片段{index}",
                "has_more": index < 2,
                "next_chunk_index": index + 1 if index < 2 else None,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(pipeline, "meeting_session_read", fake_read)
    transcript, indexes = await pipeline._read_full_transcript(str(tmp_path), "weekday-alignment", chunk_count=1)

    assert transcript == "片段0片段1片段2"
    assert indexes == [0, 1, 2]
    assert seen == [0, 1, 2]


@pytest.mark.anyio
async def test_daily_meeting_pipeline_retries_notifications_without_reanalyzing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "meeting-session" / "weekday-alignment"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text(
        json.dumps({"record_file_id": "record-1", "chunk_count": 1, "transcript_chars": 4}), encoding="utf-8"
    )
    (artifact / "pipeline_state.json").write_text(
        json.dumps(
            {
                "record_file_id": "record-1",
                "status": "notifications_pending",
                "analysis_text": "已保存分析",
                "meeting_summary": "会议纪要",
                "positive_negative_overview": "正负面总览",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    async def fail_if_prepare(**_kwargs: object) -> str:
        calls.append("prepare")
        return '{"ok":true,"status":"already_processed"}'

    async def fail_if_analyze(*_args: object, **_kwargs: object) -> dict[str, str]:
        calls.append("analyze")
        raise AssertionError("completed analysis must be reused")

    async def fake_notify(**kwargs: object) -> str:
        calls.append(f"notify:{kwargs['recipient']}")
        return json.dumps({"ok": True, "status": "sent", "recipient": kwargs["recipient"]})

    async def fake_write(**_kwargs: object) -> str:
        calls.append("write")
        return '{"ok":true,"status":"completed"}'

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fail_if_prepare)
    monkeypatch.setattr(pipeline, "_analyze_meeting_transcript", fail_if_analyze)
    monkeypatch.setattr(pipeline, "meeting_session_notify", fake_notify)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )
    assert result["status"] == "completed"
    assert calls == ["prepare", "notify:程秀秀", "notify:罗霖", "write"]


@pytest.mark.anyio
async def test_daily_meeting_pipeline_does_not_reuse_stale_artifacts_after_prepare_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "meeting-session" / "weekday-alignment"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text(
        json.dumps({"record_file_id": "old-record", "chunk_count": 1}), encoding="utf-8"
    )
    (artifact / "pipeline_state.json").write_text(
        json.dumps({"record_file_id": "old-record", "analysis_text": "旧分析"}), encoding="utf-8"
    )

    async def failed_prepare(**_kwargs: object) -> str:
        return json.dumps({"ok": False, "status": "transcript_prepare_failed", "error": "provider unavailable"})

    async def fail_if_not_stopped(**_kwargs: object) -> str:
        raise AssertionError("a failed prepare must not analyze or notify stale artifacts")

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", failed_prepare)
    monkeypatch.setattr(pipeline, "meeting_session_notify", fail_if_not_stopped)
    monkeypatch.setattr(pipeline, "meeting_session_write", fail_if_not_stopped)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )

    assert result == {
        "ok": False,
        "status": "transcript_prepare_failed",
        "error": "provider unavailable",
    }


@pytest.mark.anyio
async def test_long_transcript_is_analyzed_in_chunks_then_synthesized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    class FakeAiClient:
        def __init__(self, socket: str) -> None:
            assert socket == "ai://meeting"

        async def stream(self, request: dict[str, object]):
            requests.append(request)
            call_number = len(requests)
            if call_number == 1:
                content = '{"analysis_text":"片段一","meeting_summary":"片段一","positive_negative_overview":"片段一"}'
            elif call_number == 2:
                content = '{"analysis_text":"片段二","meeting_summary":"片段二","positive_negative_overview":"片段二"}'
            else:
                content = (
                    '{"analysis_text":"汇总","meeting_summary":"汇总纪要","positive_negative_overview":"汇总总览"}'
                )
            yield AiDelta(content=content)

    monkeypatch.setattr(pipeline, "AiClient", FakeAiClient)
    token = _CURRENT_TOOL_AI_SOCKET.set("ai://meeting")
    try:
        result = await pipeline._analyze_meeting_transcript("A" * 8_000 + "B" * 8_000, "参考纪要")
    finally:
        _CURRENT_TOOL_AI_SOCKET.reset(token)

    assert result["analysis_text"] == "汇总"
    assert len(requests) == 3
    assert "A" * 8_001 not in str(requests[0])
    assert "A" * 8_000 in str(requests[0])
    assert "B" * 8_000 in str(requests[1])
    assert "片段一" in str(requests[2])
    assert "片段二" in str(requests[2])
    assert "A" * 8_001 not in str(requests[2])


@pytest.mark.anyio
async def test_daily_meeting_pipeline_keeps_pending_when_a_notification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "meeting-session" / "weekday-alignment"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text(
        json.dumps({"record_file_id": "record-2", "chunk_count": 1}), encoding="utf-8"
    )
    (artifact / "pipeline_state.json").write_text(
        json.dumps(
            {
                "record_file_id": "record-2",
                "status": "notifications_pending",
                "analysis_text": "已保存分析",
                "meeting_summary": "会议纪要",
                "positive_negative_overview": "正负面总览",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def fake_prepare(**_kwargs: object) -> str:
        return '{"ok":true,"status":"already_processed"}'

    calls: list[str] = []

    async def fake_notify(**kwargs: object) -> str:
        recipient = str(kwargs["recipient"])
        calls.append(recipient)
        if recipient == "罗霖":
            return json.dumps({"ok": False, "status": "send_failed", "recipient": recipient})
        return json.dumps({"ok": True, "status": "sent", "recipient": recipient})

    async def fake_write(**kwargs: object) -> str:
        calls.append(f"write:{kwargs['status']}")
        return json.dumps({"ok": True, "status": kwargs["status"]})

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fake_prepare)
    monkeypatch.setattr(pipeline, "meeting_session_notify", fake_notify)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)
    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )

    assert result["status"] == "notifications_pending"
    assert calls == ["程秀秀", "罗霖", "write:notifications_pending"]


@pytest.mark.anyio
async def test_second_daily_meeting_routes_summary_to_two_recipients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "meeting-session" / "weekday-alignment-1100"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text(
        json.dumps({"record_file_id": "record-1100", "chunk_count": 1}), encoding="utf-8"
    )
    (artifact / "pipeline_state.json").write_text(
        json.dumps(
            {
                "record_file_id": "record-1100",
                "status": "notifications_pending",
                "analysis_text": "完整分析",
                "meeting_summary": "纪要与 SOP 检查",
                "positive_negative_overview": "正负面总览",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def fake_prepare(**_kwargs: object) -> str:
        return '{"ok":true,"status":"already_processed"}'

    recipients: list[str] = []

    async def fake_notify(**kwargs: object) -> str:
        recipients.append(str(kwargs["recipient"]))
        return json.dumps({"ok": True, "status": "sent", "recipient": kwargs["recipient"]})

    async def fake_write(**kwargs: object) -> str:
        return json.dumps({"ok": True, "status": kwargs["status"]})

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fake_prepare)
    monkeypatch.setattr(pipeline, "meeting_session_notify", fake_notify)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)
    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment-1100", meeting_code="42654699903", appdata_root=str(tmp_path)
        )
    )

    assert result["status"] == "completed"
    assert recipients == ["HaiTun Agent主战场", "罗霖"]


@pytest.mark.anyio
async def test_meeting_session_notify_is_idempotent_for_fixed_recipient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, str]] = []

    async def fake_send(identity: str, text: str, receive_id_type: str) -> dict[str, str | bool]:
        sent.append((identity, text, receive_id_type))
        return {"ok": True, "message_id": "om_1"}

    monkeypatch.setattr(notify, "_configured_hr_identity", lambda: "ou_hr")
    monkeypatch.setattr(notify._f, "send_message_impl", fake_send)

    first = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="hr",
            text="会议分析总览",
            record_file_id="record-1",
            appdata_root=str(tmp_path),
        )
    )
    second = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="罗霖",
            text="同一条会议分析总览",
            record_file_id="record-1",
            appdata_root=str(tmp_path),
        )
    )

    assert first["status"] == "sent"
    assert second["status"] == "already_sent"
    assert second["recipient"] == "罗霖"
    assert sent == [("ou_hr", "会议分析总览", "open_id")]


@pytest.mark.anyio
async def test_meeting_session_notify_resumes_direct_chunks_after_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[str] = []
    attempts = 0

    async def fake_send(_identity: str, text: str, _receive_id_type: str) -> dict[str, str | bool]:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            return {"ok": False, "message": "temporary failure"}
        sent.append(text)
        return {"ok": True, "message_id": f"om_{attempts}"}

    monkeypatch.setattr(notify, "_configured_hr_identity", lambda: "ou_hr")
    monkeypatch.setattr(notify._f, "send_message_impl", fake_send)
    text = "x" * (notify.MAX_NOTIFICATION_CHARS * 2 + 10)

    first = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="hr",
            text=text,
            record_file_id="record-partial",
            appdata_root=str(tmp_path),
        )
    )
    second = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="hr",
            text=text,
            record_file_id="record-partial",
            appdata_root=str(tmp_path),
        )
    )

    assert first["status"] == "send_failed"
    assert second["status"] == "sent"
    assert attempts == 4
    assert len(sent) == 3
    assert "[1/3]" in sent[0]
    assert "[2/3]" in sent[1]
    assert "[3/3]" in sent[2]


@pytest.mark.anyio
async def test_meeting_session_notify_resumes_topic_replies_without_new_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    topics: list[str] = []
    replies: list[str] = []
    reply_attempts = 0

    async def fake_resolve(_name: str) -> tuple[str, str]:
        return "oc_main", "HaiTun Agent主战场"

    async def fake_start_topic(
        _chat_id: str, text: str, _at_ids: object = None, _at_all: bool = False
    ) -> dict[str, str | bool]:
        topics.append(text)
        return {"ok": True, "message_id": "om_root", "thread_id": "omt_root"}

    async def fake_reply(_message_id: str, text: str) -> dict[str, object]:
        nonlocal reply_attempts
        reply_attempts += 1
        if reply_attempts == 1:
            return {"ok": False, "message": "temporary failure"}
        replies.append(text)
        return {"ok": True, "message_id": f"om_reply_{reply_attempts}", "thread_id": "omt_root"}

    monkeypatch.setattr(notify, "_resolve_group_with_bot", fake_resolve)
    monkeypatch.setattr(notify._f, "start_topic_impl", fake_start_topic)
    monkeypatch.setattr(notify, "_reply_in_topic", fake_reply)
    text = "x" * (notify.MAX_NOTIFICATION_CHARS * 2 + 10)

    first = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="HaiTun Agent主战场",
            text=text,
            record_file_id="record-topic-partial",
            appdata_root=str(tmp_path),
        )
    )
    second = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="HaiTun Agent主战场",
            text=text,
            record_file_id="record-topic-partial",
            appdata_root=str(tmp_path),
        )
    )

    assert first["status"] == "send_failed"
    assert second["status"] == "sent"
    assert topics == ["[1/3]\n" + "x" * notify.MAX_NOTIFICATION_CHARS]
    assert len(replies) == 2
    assert "[2/3]" in replies[0]
    assert "[3/3]" in replies[1]


@pytest.mark.anyio
async def test_meeting_session_notify_serializes_same_receipt_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = 0
    maximum_active = 0
    started = anyio.Event()
    release = anyio.Event()
    sent = 0

    async def fake_send(_identity: str, _text: str, _receive_id_type: str) -> dict[str, str | bool]:
        nonlocal active, maximum_active, sent
        active += 1
        maximum_active = max(maximum_active, active)
        sent += 1
        started.set()
        await release.wait()
        active -= 1
        return {"ok": True, "message_id": "om_once"}

    monkeypatch.setattr(notify, "_configured_hr_identity", lambda: "ou_hr")
    monkeypatch.setattr(notify._f, "send_message_impl", fake_send)
    results: list[dict[str, object]] = []

    async def run_one() -> None:
        results.append(
            json.loads(
                await notify.meeting_session_notify(
                    meeting_name="weekday-alignment",
                    recipient="hr",
                    text="一次",
                    record_file_id="record-lock",
                    appdata_root=str(tmp_path),
                )
            )
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_one)
        tg.start_soon(run_one)
        await started.wait()
        release.set()

    assert maximum_active == 1
    assert sent == 1
    assert {result["status"] for result in results} == {"sent", "already_sent"}
    assert "force" not in inspect.signature(notify.meeting_session_notify).parameters


@pytest.mark.anyio
async def test_meeting_session_notify_fails_closed_when_recipient_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_if_called(*args: object, **kwargs: object) -> dict[str, bool]:
        raise AssertionError("unresolved recipients must never be sent")

    monkeypatch.setattr(notify._f, "send_message_impl", fail_if_called)

    async def _resolve_with_bot(name: str) -> tuple[str, str]:
        return "", "没有唯一匹配"

    monkeypatch.setattr(notify, "_resolve_with_bot", _resolve_with_bot)
    result = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="程秀秀",
            text="会议纪要",
            record_file_id="record-2",
            user_key="",
            appdata_root=str(tmp_path),
        )
    )

    assert result["ok"] is False
    assert result["status"] == "recipient_unresolved"
    assert "唯一" in result["error"]


@pytest.mark.anyio
async def test_meeting_session_notify_resolves_cheng_from_tenant_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    async def fake_members(
        *, department_id: str, department_id_type: str, user_id_type: str, recursive: bool
    ) -> dict[str, object]:
        calls.append((department_id, recursive))
        assert department_id_type == "open_department_id"
        assert user_id_type == "open_id"
        return {
            "ok": True,
            "members": [
                {"name": "程秀秀", "open_id": "ou_cheng"},
                {"name": "其他成员", "open_id": "ou_other"},
            ],
        }

    monkeypatch.setattr(notify._f, "list_department_members_impl", fake_members)

    identity, display_name = await notify._resolve_with_bot("程秀秀")

    assert (identity, display_name) == ("ou_cheng", "程秀秀")
    assert calls == [("0", True)]


@pytest.mark.anyio
async def test_meeting_session_notify_posts_main_meeting_notes_as_topic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    topics: list[tuple[str, str]] = []

    async def fake_resolve(_name: str) -> tuple[str, str]:
        return "oc_main", "HaiTun Agent主战场"

    async def fake_start_topic(
        chat_id: str, text: str, _at_ids: object = None, _at_all: bool = False
    ) -> dict[str, str | bool]:
        topics.append((chat_id, text))
        return {"ok": True, "message_id": "om_topic", "thread_id": "omt_topic", "chat_id": chat_id}

    async def fail_if_dm(*_args: object, **_kwargs: object) -> dict[str, bool]:
        raise AssertionError("main meeting notes must be posted as a topic, not sent as a DM")

    monkeypatch.setattr(notify, "_resolve_group_with_bot", fake_resolve)
    monkeypatch.setattr(notify._f, "start_topic_impl", fake_start_topic)
    monkeypatch.setattr(notify._f, "send_message_impl", fail_if_dm)

    first = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="HaiTun Agent主战场",
            text="本次会议纪要",
            record_file_id="record-topic",
            appdata_root=str(tmp_path),
        )
    )
    second = json.loads(
        await notify.meeting_session_notify(
            meeting_name="weekday-alignment",
            recipient="HaiTun Agent主战场",
            text="重复投递",
            record_file_id="record-topic",
            appdata_root=str(tmp_path),
        )
    )

    assert first["status"] == "sent"
    assert first["thread_id"] == "omt_topic"
    assert second["status"] == "already_sent"
    assert topics == [("oc_main", "本次会议纪要")]


@pytest.mark.anyio
async def test_resolve_main_meeting_group_requires_one_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_api(**kwargs: object) -> dict[str, object]:
        assert kwargs["method"] == "GET"
        assert kwargs["uri"] == "/open-apis/im/v1/chats/search"
        assert json.loads(str(kwargs["query_json"]))["query"] == "HaiTun Agent主战场"
        return {
            "ok": True,
            "items": [
                {"chat_id": "oc_main", "name": "HaiTun Agent主战场"},
                {"chat_id": "oc_other", "name": "HaiTun Agent主战场讨论"},
            ],
        }

    monkeypatch.setattr(notify._api, "call_api_impl", fake_api)
    identity, display_name = await notify._resolve_group_with_bot("HaiTun Agent主战场")

    assert (identity, display_name) == ("oc_main", "HaiTun Agent主战场")
