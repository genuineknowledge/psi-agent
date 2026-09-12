"""Contract tests for the meeting history tools (records list + replay)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

records_list = importlib.import_module("meeting_records_list")
replay = importlib.import_module("meeting_pipeline_replay")

_ANALYSIS = {
    "analysis_text": "完整分析: 会中控制时间部分符合。",
    "meeting_summary": "本场 SOP 判定\n- msop.core.03: 部分符合\n会议评价: 推进一般。\n后续建议: 行动项待指定。",
    "positive_negative_overview": "候选: 正向 1 条。",
}


def _archive(tmp_path: Path, meeting: str, record: str, *, with_state: bool = True, with_analysis: bool = True) -> Path:
    d = tmp_path / "meeting-session" / meeting / "archive" / record
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "meeting_name": meeting,
                "record_file_id": record,
                "transcript_chars": 40288,
                "paragraph_count": 529,
                "chunk_count": 6,
                "chunk_chars": 8000,
                "status": "ready",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (d / "transcript.md").write_text("[00:00:01] 孙逊: 开始对齐。", encoding="utf-8")
    (d / "notification_receipts.json").write_text(
        json.dumps({"abc": {"ok": True, "status": "sent", "recipient": "ou_gaobo"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    if with_analysis:
        (d / "analysis.md").write_text(_ANALYSIS["analysis_text"], encoding="utf-8")
    if with_state:
        (d / "pipeline_state.json").write_text(
            json.dumps({"record_file_id": record, "status": "notifications_pending", **_ANALYSIS}, ensure_ascii=False),
            encoding="utf-8",
        )
    return d


def _live_manifest(tmp_path: Path, meeting: str, record: str) -> None:
    root = tmp_path / "meeting-session" / meeting
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(
        json.dumps(
            {"meeting_name": meeting, "record_file_id": record, "processed_record_file_ids": [record]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.mark.anyio
async def test_meeting_records_list_lists_archive_with_delivery_state(tmp_path, monkeypatch):
    _archive(tmp_path, "weekday-alignment", "rec_1")
    _live_manifest(tmp_path, "weekday-alignment", "rec_1")

    async def root(_appdata_root: str = ""):
        return str(tmp_path)

    monkeypatch.setattr(records_list, "resolve_appdata_root", root)
    result = json.loads(await records_list.meeting_records_list("weekday-alignment"))

    assert result["ok"] is True
    assert result["latest_record_file_id"] == "rec_1"
    assert result["count"] == 1
    entry = result["records"][0]
    assert entry["record_file_id"] == "rec_1"
    assert entry["transcript_chars"] == 40288
    assert entry["analysis_chars"] == len(_ANALYSIS["analysis_text"])
    assert entry["delivered"] == {"ou_gaobo": "sent"}
    assert "transcript.md" in entry["artifacts"]

    unknown = json.loads(await records_list.meeting_records_list("not-a-meeting"))
    assert unknown["ok"] is False and unknown["status"] == "unknown_meeting"


@pytest.mark.anyio
async def test_meeting_pipeline_replay_reuses_analysis_and_delivers_to_missing_recipients(tmp_path, monkeypatch):
    archive_dir = _archive(tmp_path, "weekday-alignment", "rec_2")
    _live_manifest(tmp_path, "weekday-alignment", "rec_9")  # 最新已是别的场次: 单槽状态不再跟踪 rec_2

    async def root(_appdata_root: str = ""):
        return str(tmp_path)

    sent: dict[str, int] = {}
    calls: list[tuple[str, str]] = []

    async def fake_notify(**kwargs):
        calls.append((kwargs["recipient"], kwargs["record_file_id"]))
        sent[kwargs["recipient"]] = sent.get(kwargs["recipient"], 0) + 1
        status = "sent" if sent[kwargs["recipient"]] == 1 else "already_sent"
        return json.dumps({"ok": True, "status": status, "recipient": kwargs["recipient"]}, ensure_ascii=False)

    def no_card(**_kwargs):  # render_meeting_summary_card 是同步函数
        return {"ok": False, "error": "no card in test"}

    monkeypatch.setattr(replay, "resolve_appdata_root", root)
    monkeypatch.setattr(replay, "meeting_session_notify", fake_notify)
    monkeypatch.setattr(replay, "render_meeting_summary_card", no_card)

    result = json.loads(await replay.meeting_pipeline_replay("weekday-alignment", "rec_2"))
    assert result["ok"] is True
    assert result["status"] == "delivered"
    assert result["analysis_reused"] is True  # archive 里的 pipeline_state 已带分析 → 不重算
    assert result["transcript_source"] == "archive"
    assert (archive_dir / "replay_analysis.md").read_text(encoding="utf-8") == _ANALYSIS["analysis_text"]
    assert (archive_dir / "replay_metrics.jsonl").read_text(encoding="utf-8").strip()
    # 总结 + 正负面总览两条路由各投一次
    assert len(calls) == 2 and all(record == "rec_2" for _, record in calls)

    # 再来一次: 回执已存在 → already_sent, 不重复产生内容
    again = json.loads(await replay.meeting_pipeline_replay("weekday-alignment", "rec_2"))
    assert again["ok"] is True
    assert any(item["status"] == "already_sent" for item in again["recipients"].values())


@pytest.mark.anyio
async def test_meeting_pipeline_replay_force_reanalyzes_and_reports_missing_transcript(tmp_path, monkeypatch):
    _archive(tmp_path, "weekday-alignment", "rec_3")
    _live_manifest(tmp_path, "weekday-alignment", "rec_3")

    async def root(_appdata_root: str = ""):
        return str(tmp_path)

    async def fake_rules(_job):
        return ("SOP 口径快照", "正负面规则快照")

    ran: list[str] = []

    async def fake_analyze(transcript, smart_minutes="", **_kwargs):
        ran.append(transcript)
        return {
            "analysis_text": "重算后的分析",
            "meeting_summary": "重算摘要",
            "positive_negative_overview": "重算总览",
        }

    monkeypatch.setattr(replay, "resolve_appdata_root", root)
    monkeypatch.setattr(replay.pipeline, "_load_analysis_rules", fake_rules)
    monkeypatch.setattr(replay.pipeline, "_analyze_meeting_transcript", fake_analyze)

    result = json.loads(
        await replay.meeting_pipeline_replay("weekday-alignment", "rec_3", force_reanalyze=True, redeliver=False)
    )
    assert result["ok"] is True
    assert result["analysis_reused"] is False
    assert ran, "force_reanalyze 必须真的重算"
    assert result["status"] == "replayed"
    assert (tmp_path / "meeting-session" / "weekday-alignment" / "archive" / "rec_3" / "replay_analysis.md").read_text(
        encoding="utf-8"
    ) == "重算后的分析"

    # 没有原文、按 record 也拉不到 → 明确报 transcript_unavailable, 不静默成功
    async def empty_paragraphs(_record, token_env=""):
        return []

    monkeypatch.setattr(replay, "_collect_paragraphs", empty_paragraphs)
    missing = json.loads(await replay.meeting_pipeline_replay("weekday-alignment", "rec_404"))
    assert missing["ok"] is False
    assert missing["status"] == "transcript_unavailable"
