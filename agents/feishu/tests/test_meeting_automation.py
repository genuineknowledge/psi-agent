from __future__ import annotations

# Local meeting tools intentionally load from the agent package rather than an installed package.
# ruff: noqa: E402
import inspect
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest
import yaml

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _meeting_automation as ma  # ty: ignore[unresolved-import]
import meeting_pipeline_run as pipeline  # ty: ignore[unresolved-import]
import meeting_session_notify as notify  # ty: ignore[unresolved-import]
import meeting_transcript_prepare as transcript_prepare  # ty: ignore[unresolved-import]
import tencent_meeting  # ty: ignore[unresolved-import]
from _meeting_automation import (  # ty: ignore[unresolved-import]
    MEETING_JOBS,
    MeetingJob,
    _json_payload,
    atomic_write_text,
    chunk_text,
    extract_latest_transcript_record,
    meeting_credential_env,
    meeting_schedule_files,
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
    # 默认单次调用超时 60s (防上游挂起永久阻塞); 超时由 anyio.fail_after 实现
    assert tencent_meeting.DEFAULT_CALL_TIMEOUT == 60.0


def test_meeting_jobs_use_fixed_post_meeting_crons() -> None:
    jobs = {job.name: job for job in MEETING_JOBS}
    assert jobs["weekday-alignment"].meeting_code == "57152787045"
    assert jobs["weekday-alignment"].cron == "0 12 * * 1,3,5"
    assert jobs["weekday-alignment"].retry_crons == ("30 17 * * 1,3,5",)
    assert jobs["weekday-alignment"].summary_recipients == ("HaiTun Agent主战场",)
    assert jobs["weekday-alignment"].overview_recipients == ("罗霖",)
    assert jobs["weekday-alignment-1100"].meeting_code == "42654699903"
    assert jobs["weekday-alignment-1100"].cron == "0 12 * * 1,3,5"
    assert jobs["weekday-alignment-1100"].retry_crons == ("30 17 * * 1,3,5",)
    assert jobs["weekday-alignment-1100"].recipients == ("张浩", "王金旺", "罗霖")
    assert jobs["weekday-alignment-1100"].token_env == "TENCENT_MEETING_TOKEN_42654699903"
    assert jobs["weekday-alignment-1100"].summary_recipients == ("张浩", "王金旺")
    assert jobs["weekday-alignment-1100"].overview_recipients == ("罗霖",)
    assert len(jobs) == 2
    assert jobs["weekday-alignment"].fire == "tool"
    assert jobs["weekday-alignment"].tool_name == "meeting_pipeline_run"


def test_meeting_schedule_files_cover_every_job_and_retry() -> None:
    files = meeting_schedule_files()
    assert set(files) == {
        "weekday-alignment",
        "weekday-alignment-1100",
        "weekday-alignment-1100-retry-1730",
        "weekday-alignment-retry-1730",
    }
    for job in MEETING_JOBS:
        body = files[job.name]
        assert f"name: {job.name}" in body
        assert f'cron: "{job.cron}"' in body
        assert job.meeting_code in body
        assert "原始全文转写" in body
    retry = files["weekday-alignment-1100-retry-1730"]
    assert 'cron: "30 17 * * 1,3,5"' in retry
    assert '"meeting_name":"weekday-alignment-1100"' in retry
    assert '"meeting_code":"42654699903"' in retry
    retry = files["weekday-alignment-retry-1730"]
    assert 'cron: "30 17 * * 1,3,5"' in retry
    assert '"meeting_name":"weekday-alignment"' in retry
    assert '"meeting_code":"57152787045"' in retry


def test_committed_meeting_schedule_files_match_projection() -> None:
    """``agents/feishu/schedules`` 下的静态 TASK.md 必须与 ``MEETING_JOBS`` 投影一致。

    调度器把 agent 包 ``schedules/*/TASK.md`` 原样 seed 进公司 workspace (只在缺失
    时复制), 改 ``MEETING_JOBS`` 的 cron/retry/参数必须同步改静态文件 —— 这条红绿
    判据把两边钉在同一个事实源上, 防止改一处漏一处。

    静态 TASK.md 文件由独立 PR (#856) 提供; 本分支不含这些文件时判据自动跳过,
    两侧都合并到 main 后恢复强制。
    """
    schedules_root = Path(__file__).resolve().parents[1] / "schedules"
    files = meeting_schedule_files()
    if any(not (schedules_root / name / "TASK.md").is_file() for name in files):
        pytest.skip("静态 meeting TASK.md 由独立 PR 提供, 合并前跳过一致性判据")
    for name, expected in files.items():
        committed = (schedules_root / name / "TASK.md").read_text(encoding="utf-8")
        assert committed == expected, f"agents/feishu/schedules/{name}/TASK.md 与 MEETING_JOBS 投影不一致"


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


def test_extract_record_waits_when_latest_occurrence_is_active() -> None:
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

    assert selected is None


def test_extract_record_selects_latest_occurrence_after_it_completes() -> None:
    payload = {
        "record_meetings": [
            {
                "sub_meeting_id": "today",
                "record_type": "文字转写",
                "state_int": 3,
                "media_start_time": "2026-09-07T10:00:00+08:00",
                "record_files": [
                    {
                        "record_file_id": "today-completed",
                        "record_start_time": "2026-09-07T10:00:02+08:00",
                    }
                ],
            },
            {
                "sub_meeting_id": "last-week",
                "record_type": "文字转写",
                "state_int": 3,
                "media_start_time": "2026-09-04T10:00:00+08:00",
                "record_files": [{"record_file_id": "last-week-full"}],
            },
        ]
    }

    selected = extract_latest_transcript_record(payload, set())

    assert selected is not None
    assert selected["record_file_id"] == "today-completed"


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
    saved_text = (tmp_path / "meeting-session" / "weekday-alignment" / "transcript.md").read_text(encoding="utf-8")
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

    def fake_render_card(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": "会议总结"}},
                "body": {"elements": []},
            },
            "handlers": {},
        }

    monkeypatch.setattr(pipeline, "render_meeting_summary_card", fake_render_card)
    monkeypatch.setattr(pipeline, "notify_meeting_card", fake_notify)

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
    assert {call[1]["recipient"] for call in calls if call[0] == "notify"} == {"罗霖", "HaiTun Agent主战场"}


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

    def fake_render_card(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": "会议总结"}},
                "body": {"elements": []},
            },
            "handlers": {},
        }

    monkeypatch.setattr(pipeline, "render_meeting_summary_card", fake_render_card)
    monkeypatch.setattr(pipeline, "notify_meeting_card", fake_notify)
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
    assert calls == ["prepare", "notify:HaiTun Agent主战场", "notify:罗霖", "write"]


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
    monkeypatch.setattr(pipeline, "notify_meeting_card", fail_if_not_stopped)
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
    requests: list[dict[str, Any]] = []

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

    def fake_render_card(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": "会议总结"}},
                "body": {"elements": []},
            },
            "handlers": {},
        }

    monkeypatch.setattr(pipeline, "render_meeting_summary_card", fake_render_card)
    monkeypatch.setattr(pipeline, "notify_meeting_card", fake_notify)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)
    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )

    assert result["status"] == "notifications_pending"
    # 通知部分失败 → 收尾后向 alert_recipients (张浩/王金旺) 各发一条失败告警 (P3)。
    assert calls == [
        "HaiTun Agent主战场",
        "罗霖",
        "write:notifications_pending",
        "张浩",
        "王金旺",
    ]


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

    def fake_render_card(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": "会议总结"}},
                "body": {"elements": []},
            },
            "handlers": {},
        }

    monkeypatch.setattr(pipeline, "render_meeting_summary_card", fake_render_card)
    monkeypatch.setattr(pipeline, "notify_meeting_card", fake_notify)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)
    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment-1100", meeting_code="42654699903", appdata_root=str(tmp_path)
        )
    )

    assert result["status"] == "completed"
    assert recipients == ["张浩", "王金旺", "罗霖"]


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
@pytest.mark.parametrize("recipient", ["张浩", "王金旺"])
async def test_meeting_session_notify_resolves_fixed_summary_recipient(
    recipient: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_resolve(name: str) -> tuple[str, str]:
        assert name == recipient
        return f"ou_{recipient}", recipient

    monkeypatch.setattr(notify, "_resolve_with_bot", fake_resolve)

    identity, display_name = await notify._resolve_recipient(recipient, "")

    assert (identity, display_name) == (f"ou_{recipient}", recipient)


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


# ── P1: 运行指标 run_metrics.jsonl ───────────────────────────────────────────


@pytest.mark.anyio
async def test_pipeline_appends_run_metrics_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每次运行 (成功与失败) 都向 run_metrics.jsonl 追加一行结构化指标。"""
    artifact = tmp_path / "meeting-session" / "weekday-alignment"
    artifact.mkdir(parents=True)
    (artifact / "manifest.json").write_text(
        json.dumps({"record_file_id": "record-m1", "chunk_count": 1, "transcript_chars": 4, "paragraph_count": 1}),
        encoding="utf-8",
    )

    async def fake_prepare_ok(**_kwargs: object) -> str:
        return '{"ok":true,"status":"ready","record_file_id":"record-m1","chunk_count":1}'

    async def fake_prepare_fail(**_kwargs: object) -> str:
        return json.dumps({"ok": False, "status": "transcript_prepare_failed", "error": "provider unavailable"})

    async def fake_read(**kwargs: object) -> str:
        return json.dumps(
            {"ok": True, "content": "原文", "has_more": False, "chunk_index": kwargs["chunk_index"]},
            ensure_ascii=False,
        )

    async def fake_analyze(transcript: str, smart_minutes: str = "", **_kwargs: object) -> dict[str, str]:
        return {
            "analysis_text": f"分析:{transcript}",
            "meeting_summary": "纪要",
            "positive_negative_overview": "总览",
        }

    async def fake_notify(**kwargs: object) -> str:
        return json.dumps({"ok": True, "status": "sent", "recipient": kwargs["recipient"]})

    async def fake_write(**kwargs: object) -> str:
        return json.dumps({"ok": True, "status": kwargs["status"]})

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(pipeline, "meeting_session_read", fake_read)
    monkeypatch.setattr(pipeline, "_analyze_meeting_transcript", fake_analyze)
    monkeypatch.setattr(pipeline, "meeting_session_notify", fake_notify)

    def fake_render_card(**_kwargs: object) -> dict[str, object]:
        return {
            "ok": True,
            "card": {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": "会议总结"}},
                "body": {"elements": []},
            },
            "handlers": {},
        }

    monkeypatch.setattr(pipeline, "render_meeting_summary_card", fake_render_card)
    monkeypatch.setattr(pipeline, "notify_meeting_card", fake_notify)
    monkeypatch.setattr(pipeline, "meeting_session_write", fake_write)
    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fake_prepare_ok)
    first = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )
    assert first["status"] == "completed"

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fake_prepare_fail)
    second = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )
    assert second["status"] == "transcript_prepare_failed"

    metrics_path = artifact / "run_metrics.jsonl"
    lines = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").strip().splitlines() if line.strip()]
    assert [row["status"] for row in lines] == ["completed", "transcript_prepare_failed"]
    ok_row, fail_row = lines
    assert ok_row["meeting_name"] == "weekday-alignment"
    assert ok_row["record_file_id"] == "record-m1"
    assert ok_row["transcript_chars"] == 4
    assert isinstance(ok_row["stages_ms"]["total"], int)
    assert set(ok_row["stages_ms"]) == {"prepare", "read", "analyze", "notify", "total"}
    assert ok_row["analysis"] == {
        "ai_calls": 0,
        "ai_input_chars": 0,
        "ai_content_chars": 0,
        "ai_reasoning_chars": 0,
        "ai_empty_calls": 0,
    }
    assert set(ok_row["notifications"]) == {"HaiTun Agent主战场", "罗霖"}
    assert fail_row["record_file_id"] == ""
    assert "provider unavailable" in fail_row["error"]


# ── P2: 会议元数据 + SOP/规则快照注入 ───────────────────────────────────────


@pytest.mark.anyio
async def test_analysis_prompt_carries_meeting_metadata_and_rule_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分块分析请求携带 会议名/标题/会议号/日期/块号 + 版本化 SOP 与正负面快照文本。"""
    requests: list[dict[str, Any]] = []

    class FakeAiClient:
        def __init__(self, socket: str) -> None:
            assert socket == "ai://meeting"

        async def stream(self, request: dict[str, object]):
            requests.append(request)
            yield AiDelta(content='{"analysis_text":"a","meeting_summary":"s","positive_negative_overview":"o"}')

    monkeypatch.setattr(pipeline, "AiClient", FakeAiClient)
    job = MeetingJob(
        name="weekday-alignment",
        meeting_code="57152787045",
        cron="0 12 * * 1,3,5",
        title="周中对齐会",
        recipients=("罗霖",),
    )
    stats: dict[str, int] = {}
    token = _CURRENT_TOOL_AI_SOCKET.set("ai://meeting")
    try:
        await pipeline._analyze_meeting_transcript(
            "短转写",
            job=job,
            sop_rules="会议SOP-测试规则正文",
            positive_rules="正负面-测试规则正文",
            stats=stats,
        )
    finally:
        _CURRENT_TOOL_AI_SOCKET.reset(token)

    user_content = str(requests[0]["messages"][1]["content"])
    system_content = str(requests[0]["messages"][0]["content"])
    assert "会议元数据" in user_content
    assert "weekday-alignment" in user_content and "57152787045" in user_content
    assert "周中对齐会" in user_content
    assert "会议SOP-测试规则正文" in user_content
    assert "正负面-测试规则正文" in user_content
    assert system_content.startswith("你是 HaiTun 的 周中对齐会 分析器")
    assert stats["ai_calls"] == 1
    assert stats["ai_input_chars"] > 0


@pytest.mark.anyio
async def test_analysis_requires_committed_rule_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实 job 必须能读到 引擎 SKILL + meeting-sop.yaml 口径 + 正负面规则快照;

    缺失或契约损坏即显式报错 (与 todo-sop.yaml 同一口径即配置模式)。
    """
    jobs = {job.name: job for job in MEETING_JOBS}
    job = jobs["weekday-alignment"]
    sop_text, positive_text = await pipeline._load_analysis_rules(job)
    assert "会议 SOP" in sop_text  # 引擎 SKILL 已注入
    assert "config/meeting-sop.yaml" in sop_text  # 判定口径 (YAML) 已注入
    assert "judgment_states" in sop_text and "msop.core.01" in sop_text
    assert "正负面分析规则" in positive_text
    assert "不得监听或自动分析" not in positive_text  # 注入的是快照, 不是私聊边界全文
    assert "负面候选三元组" in positive_text

    broken = replace(job, analysis_sop_skills=("meeting-sop/not-shipped",))
    with pytest.raises(RuntimeError, match="会议 SOP skill 缺失"):
        await pipeline._load_analysis_rules(broken)


@pytest.mark.anyio
async def test_analysis_fails_when_meeting_sop_config_missing_or_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """口径 YAML 缺失或不符合契约 → 显式失败, 绝不静默用旧口径/空口径分析。"""
    jobs = {job.name: job for job in MEETING_JOBS}
    job = jobs["weekday-alignment"]
    missing = tmp_path / "missing.yaml"
    monkeypatch.setattr(pipeline, "MEETING_SOP_CONFIG_PATH", missing)
    with pytest.raises(RuntimeError, match="会议 SOP 配置缺失"):
        await pipeline._load_analysis_rules(job)

    broken = tmp_path / "broken.yaml"
    broken.write_text("meta:\n  version: v1\n", encoding="utf-8")  # 缺 rules 段
    monkeypatch.setattr(pipeline, "MEETING_SOP_CONFIG_PATH", broken)
    with pytest.raises(RuntimeError, match="不符合契约"):
        await pipeline._load_analysis_rules(job)


# ── 周中对齐会 SOP v1.1(docx): 核心原则 1/3/4/5 定稿生效 (口径 v1.2) ───────────


def test_meeting_sop_core_principles_1_3_4_5_are_active_with_criteria() -> None:
    """meeting-sop.yaml 契约值: 口径 v1.2 (依据 docx「核心原则」编号 1/3/4/5) 已将四条
    (必有产出 / 会中控制时间 / 会后纪要与闭环执行 / 杜绝流水账) 定稿为 active
    且判定标准非空; 编号 2 (会前准备落实到责任人, msop.core.02) 未纳入本判定
    引擎, 必须保持 active: false。"""
    path = Path(pipeline.MEETING_SOP_CONFIG_PATH)
    assert path.is_file(), "meeting-sop.yaml 缺失, 无法定稿"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config["meta"]["version"] == "v1.2"
    by_id = {rule["id"]: rule for rule in config["rules"]}
    assert set(by_id) == {
        "msop.core.01",
        "msop.core.02",
        "msop.core.03",
        "msop.core.04",
        "msop.core.05",
    }
    active_ids = [rule_id for rule_id, rule in by_id.items() if rule.get("active") is True]
    assert active_ids == [
        "msop.core.01",
        "msop.core.03",
        "msop.core.04",
        "msop.core.05",
    ]
    assert by_id["msop.core.02"]["active"] is False
    for rule_id in active_ids:
        rule = by_id[rule_id]
        for field in ("criteria", "compliant_example", "violation_example", "exception"):
            assert str(rule.get(field) or "").strip(), f"{rule_id}.{field} 必须已定稿非空"


@pytest.mark.anyio
async def test_analysis_injects_active_rule_checklist() -> None:
    """管道注入的规则文本 = 引擎 SKILL + 口径 YAML + 机器渲染的「生效判定清单」:
    口径 v1.2、生效 4 条逐一列明、未生效条目单独声明 (模型不得判其符合/不符合)。"""
    jobs = {job.name: job for job in MEETING_JOBS}
    sop_text, _ = await pipeline._load_analysis_rules(jobs["weekday-alignment"])
    assert "口径 v1.2" in sop_text
    assert "生效判定清单" in sop_text
    assert "4 条生效规则" in sop_text
    assert "未生效" in sop_text and "msop.core.02" in sop_text
    for rule_id in (
        "msop.core.01",
        "msop.core.03",
        "msop.core.04",
        "msop.core.05",
    ):
        assert rule_id in sop_text


def test_meeting_sop_core03_host_speech_exempt_from_3min_cap() -> None:
    """口径澄清 (v1.2): 「每人 ≤3 分钟」只适用于议程三成员个人汇报发言;

    主持人/小组负责人/Mentor/领导 承担主持职能 (开场/议程推进/逐人点评/追问/打断
    分流/总结) 时的发言不受该上限约束 (一人或多人主持均可); 引擎不得以主持人累计/
    单次发言超 3 分钟判不符合。该豁免不回退议程时间盒 (议程一/二 ≤5 分钟、整场 ≤30
    分钟) 与成员发言 3 分钟上限本身。"""
    path = Path(pipeline.MEETING_SOP_CONFIG_PATH)
    assert path.is_file()
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    rule = next(item for item in config["rules"] if item["id"] == "msop.core.03")
    criteria = str(rule.get("criteria") or "")
    exception = str(rule.get("exception") or "")
    # 3 分钟上限被限定在议程三成员汇报, 主持人职能发言豁免
    assert "议程三" in criteria and "成员个人汇报发言" in criteria
    assert "主持职能发言豁免每人 ≤3 分钟上限" in criteria
    assert "主持人/小组负责人/Mentor/领导" in criteria
    assert "不受" in criteria and "≤3 分钟" in criteria
    # 不得以主持人超 3 分钟判不符合 (反例示例也明确主持人点评不算成员超时)
    assert "不得以" in criteria and "判定不符合" in criteria
    assert "主持人正常点评/追问/总结不被计为成员超时发言" in str(rule.get("violation_example") or "")
    # 识别不清时不判违规, 记证据不足
    assert "识别不清" in exception and "证据不足" in exception


@pytest.mark.anyio
async def test_analysis_fails_when_active_rule_has_no_criteria(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定稿后的防线: active: true 的规则必须携带非空判定标准; 空标准 = 契约损坏,
    本次运行显式失败, 不允许把无口径的规则当已生效去判。"""
    cfg = tmp_path / "active-empty.yaml"
    cfg.write_text(
        "meta:\n"
        "  version: v1.1\n"
        "  sop_name: 周中对齐会会议 SOP\n"
        "observation_axes:\n"
        "  - {id: prep, title: 会前准备}\n"
        "  - {id: session, title: 会中议题与决议}\n"
        "judgment_states: [符合, 部分符合, 不符合, 证据不足]\n"
        "rules:\n"
        "  - id: msop.core.01\n"
        "    active: true\n"
        "    title: 必有产出\n"
        "    axis: session\n"
        '    criteria: ""\n'
        '    compliant_example: ""\n'
        '    violation_example: ""\n'
        '    exception: ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "MEETING_SOP_CONFIG_PATH", cfg)
    jobs = {job.name: job for job in MEETING_JOBS}
    with pytest.raises(RuntimeError, match="判定标准为空"):
        await pipeline._load_analysis_rules(jobs["weekday-alignment"])


@pytest.mark.anyio
async def test_analysis_fails_on_duplicate_rule_id_or_unknown_axis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """口径契约: rules[].id 全仓唯一、axis 必须落在 observation_axes 内;
    重复 id 或未知 axis 都属契约损坏 → 显式失败。"""
    jobs = {job.name: job for job in MEETING_JOBS}
    job = jobs["weekday-alignment"]
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "meta:\n"
        "  version: v1.1\n"
        "  sop_name: 周中对齐会会议 SOP\n"
        "observation_axes:\n"
        "  - {id: prep, title: 会前准备}\n"
        "judgment_states: [符合, 部分符合, 不符合, 证据不足]\n"
        "rules:\n"
        "  - {id: msop.prep.01, active: true, title: a, axis: prep, criteria: c1,"
        " compliant_example: e, violation_example: e, exception: e}\n"
        "  - {id: msop.prep.01, active: true, title: b, axis: prep, criteria: c2,"
        " compliant_example: e, violation_example: e, exception: e}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "MEETING_SOP_CONFIG_PATH", duplicate)
    with pytest.raises(RuntimeError, match="重复"):
        await pipeline._load_analysis_rules(job)

    unknown_axis = tmp_path / "unknown-axis.yaml"
    unknown_axis.write_text(
        "meta:\n"
        "  version: v1.1\n"
        "  sop_name: 周中对齐会会议 SOP\n"
        "observation_axes:\n"
        "  - {id: prep, title: 会前准备}\n"
        "judgment_states: [符合, 部分符合, 不符合, 证据不足]\n"
        "rules:\n"
        "  - {id: msop.prep.01, active: true, title: a, axis: other, criteria: c,"
        " compliant_example: e, violation_example: e, exception: e}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "MEETING_SOP_CONFIG_PATH", unknown_axis)
    with pytest.raises(RuntimeError, match="observation_axes"):
        await pipeline._load_analysis_rules(job)


# ── P3: 超时 / 重试 / 协议校验 / 原子写 / 失败告警 ──────────────────────────


@pytest.mark.anyio
async def test_adapter_timeout_returns_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """子进程超时 → 显式 Error 文本 (由 _call 层重试并最终显式失败), 而非永久挂起。"""

    async def hang(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setenv("TENCENT_MEETING_TOKEN", "daily-secret")
    monkeypatch.setattr(tencent_meeting.anyio, "run_process", hang)
    result = await tencent_meeting._tencent_meeting_call_with_token_env("tools/list", token_env="TENCENT_MEETING_TOKEN")
    assert "timed out after 60" in result


@pytest.mark.anyio
async def test_prepare_call_retries_transient_errors_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """瞬时失败 (Error 前缀) 最多重试 _CALL_ATTEMPTS 次, 成功后返回解析结果。"""
    attempts: list[int] = []

    async def flaky(_method: str, _params_json: str = "") -> str:
        attempts.append(1)
        if len(attempts) < 3:
            return "Error: temporary upstream failure"
        return '{"records":[]}'

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(transcript_prepare, "tencent_meeting_call", flaky)
    monkeypatch.setattr(transcript_prepare.anyio, "sleep", no_sleep)
    payload = await transcript_prepare._call("get_records_list", {})
    assert payload == {"records": []}
    assert len(attempts) == 3


@pytest.mark.anyio
async def test_prepare_call_rpc_error_fails_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON-RPC error / result 缺失 → 不再被静默当成"无录制", 重试后显式抛错。"""
    attempts: list[int] = []

    async def always_error(_method: str, _params_json: str = "") -> str:
        attempts.append(1)
        return '{"error":{"code":-1,"message":"permission denied"}}'

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(transcript_prepare, "tencent_meeting_call", always_error)
    monkeypatch.setattr(transcript_prepare.anyio, "sleep", no_sleep)
    monkeypatch.setattr(transcript_prepare, "_CALL_ATTEMPTS", 2)
    monkeypatch.setattr(transcript_prepare, "_CALL_BACKOFF_SECONDS", (0.0,))
    with pytest.raises(RuntimeError, match="RPC error"):
        await transcript_prepare._call("get_records_list", {})
    assert len(attempts) == 2


def test_atomic_write_text_replaces_whole_file(tmp_path: Path) -> None:
    """原子写: 内容完整替换且不留 *.tmp 残片 (并发读者看不到半截文件)。"""

    target = tmp_path / "sub" / "artifact.json"
    anyio.run(atomic_write_text, target, '{"v":1}')
    assert target.read_text(encoding="utf-8") == '{"v":1}'
    anyio.run(atomic_write_text, target, '{"v":2,"x":"更大内容"}')
    assert json.loads(target.read_text(encoding="utf-8"))["v"] == 2
    assert list(tmp_path.rglob("*.tmp")) == []


@pytest.mark.anyio
async def test_failure_alert_sent_to_every_alert_recipient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """采集失败时向 job.alert_recipients 每人发一条带标记的告警文本。"""
    sent: list[tuple[str, str]] = []

    async def fake_prepare_fail(**_kwargs: object) -> str:
        return json.dumps({"ok": False, "status": "transcript_prepare_failed", "error": "token 无效"})

    async def capture_notify(**kwargs: object) -> str:
        sent.append((str(kwargs["recipient"]), str(kwargs["text"])))
        return json.dumps({"ok": True, "status": "sent", "recipient": kwargs["recipient"]})

    async def resolve_root(_root: str = "") -> str:
        return _root or str(tmp_path)

    monkeypatch.setattr(pipeline, "meeting_transcript_prepare", fake_prepare_fail)
    monkeypatch.setattr(pipeline, "meeting_session_notify", capture_notify)
    monkeypatch.setattr(pipeline, "resolve_appdata_root", resolve_root)

    result = json.loads(
        await pipeline.meeting_pipeline_run(
            meeting_name="weekday-alignment", meeting_code="57152787045", appdata_root=str(tmp_path)
        )
    )
    assert result["status"] == "transcript_prepare_failed"
    assert [recipient for recipient, _text in sent] == ["张浩", "王金旺"]
    assert all(text.startswith("[会议自动化告警]") and "transcript_prepare_failed" in text for _r, text in sent)
    assert all("token 无效" in text for _r, text in sent)


# ── meeting-automation.yaml 配置化 ( 白名单留代码, 其余进 yaml) ──────────────


def test_meeting_jobs_overlay_matches_config_yaml() -> None:
    """meetings 覆盖层 (投递/SOP/告警口径) 必须与代码白名单精确对应。

    - 每个代码白名单会议都有 yaml 条目;
    - 覆盖键只允许 title/summary/overview/sop skills/alert recipients;
    - 白名单配对 (name/meeting_code/token_env/cron) 不被 yaml 触碰 —— 两者同值断言。
    """

    config_path = ma.MEETING_AUTOMATION_CONFIG_PATH
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    meetings: dict[str, dict] = data["meetings"]

    jobs = {job.name: job for job in MEETING_JOBS}
    assert set(meetings) == set(jobs)
    for name, overlay in meetings.items():
        job = jobs[name]
        assert set(overlay) <= {
            "title",
            "summary_recipients",
            "overview_recipients",
            "analysis_sop_skills",
            "alert_recipients",
        }
        assert job.title == overlay["title"]
        assert job.summary_recipients == tuple(overlay["summary_recipients"])
        assert job.overview_recipients == tuple(overlay["overview_recipients"])
        assert job.analysis_sop_skills == tuple(overlay["analysis_sop_skills"])
        assert job.alert_recipients == tuple(overlay["alert_recipients"])
        # 授权配对仍以代码白名单为准 (yaml 结构上不可能覆盖这些键)
        whitelist_job = {j.name: j for j in ma._MEETING_JOBS_WHITELIST}[name]
        assert job.meeting_code == whitelist_job.meeting_code
        assert job.token_env == whitelist_job.token_env
        assert job.cron == whitelist_job.cron


def test_automation_config_loader_rejects_overreach_and_bad_contract(tmp_path: Path) -> None:
    """越权键 (白名单/token_env/未知字段) 与契约损坏 → 显式报错。"""

    overreach = tmp_path / "overreach.yaml"
    overreach.write_text(
        "meetings:\n  weekday-alignment:\n    token_env: TENCENT_MEETING_TOKEN_OTHER\nruntime: {}\nresources: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="不可经配置文件修改"):
        ma.load_meeting_automation_config(overreach)

    unknown = tmp_path / "unknown-key.yaml"
    unknown.write_text(
        "meetings:\n  weekday-alignment:\n    made_up: 1\nruntime: {}\nresources: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="未知字段"):
        ma.load_meeting_automation_config(unknown)

    broken = tmp_path / "broken.yaml"
    broken.write_text("meetings: 5\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="不符合契约"):
        ma.load_meeting_automation_config(broken)


def test_automation_config_forbids_whitelist_extension(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """yaml 不能新增白名单外会议: 覆盖合并阶段显式失败。"""

    rogue = tmp_path / "rogue.yaml"
    rogue.write_text(
        "meetings:\n  weekday-alignment:\n    title: 周中对齐会\n  rogue-meeting:\n    title: 越权会议\n"
        "runtime: {}\nresources: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ma, "MEETING_AUTOMATION_CONFIG", ma.load_meeting_automation_config(rogue))
    with pytest.raises(RuntimeError, match="白名单外会议"):
        ma._meeting_jobs_with_overlay()


def test_runtime_constants_come_from_config_yaml() -> None:
    """引擎常量 (分块/温度/超时/重试/告警前缀) 与 meeting-automation.yaml runtime 段一致。"""

    data = yaml.safe_load(ma.MEETING_AUTOMATION_CONFIG_PATH.read_text(encoding="utf-8"))
    runtime = data["runtime"]
    assert runtime["analysis"]["chunk_chars"] == pipeline.ANALYSIS_CHUNK_CHARS
    assert runtime["analysis"]["temperature"] == pipeline.ANALYSIS_TEMPERATURE
    assert runtime["notify"]["chunk_chars"] == notify.MAX_NOTIFICATION_CHARS
    assert runtime["tencent"]["call_timeout_seconds"] == tencent_meeting.DEFAULT_CALL_TIMEOUT
    assert runtime["tencent"]["retry_attempts"] == transcript_prepare._CALL_ATTEMPTS
    assert list(transcript_prepare._CALL_BACKOFF_SECONDS) == runtime["tencent"]["retry_backoff_seconds"]
    assert runtime["alerts"]["message_prefix"] == pipeline.ALERT_MESSAGE_PREFIX
    assert runtime["alerts"]["error_truncate_chars"] == pipeline.ALERT_ERROR_TRUNCATE_CHARS
    # 资源路径引用 (相对 agent 包根) 与 yaml 一致
    resources = data["resources"]
    assert pipeline.AGENT_ROOT / resources["meeting_sop_config_file"] == pipeline.MEETING_SOP_CONFIG_PATH
    assert pipeline.AGENT_ROOT / resources["positive_rules_file"] == pipeline.POSITIVE_NEGATIVE_RULES_PATH
