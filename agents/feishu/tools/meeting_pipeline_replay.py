"""Replay one archived meeting recording: (re)analyse it and deliver what never went out.

管道的选择器只认「最新一条已完成录制」, 且单槽状态 (``pipeline_state.json``) 只跟
最近一场 —— 某场如果当时分析失败、或投递失败后又被新场次挤掉, 就再也不会被自动
补上。本工具按 ``record_file_id`` 显式补跑:

1. 取该场原文: 优先 live/archive 的 ``transcript.md``; 都没有就按 record 去腾讯
   重新拉转写 (复用 prepare 的分页实现), 结果只写进 archive, 不动共享单槽文件;
2. 分析: 有现成分析就直接复用; ``force_reanalyze=True`` 才重算;
3. 投递 (``redeliver=True``, 默认): 复用管道的卡片/文本投递, 回执键仍是
   ``record_file_id + 真实身份`` —— 已成功投递过的收件人自动 ``already_sent``,
   没投过的才补发。产品口径: 补发允许 (用户已确认)。

产物落在 ``archive/<record_file_id>/replay_analysis.md|json`` + ``replay_metrics.jsonl``,
不覆盖管道的规范产物。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import meeting_pipeline_run as pipeline  # noqa: E402
from _meeting_archive import _safe_record_dir_name, record_archive_dir  # noqa: E402
from _meeting_automation import (  # noqa: E402
    MEETING_JOBS,
    atomic_write_text,
    meeting_artifact_root,
    meeting_credential_env,
    read_meeting_manifest,
    render_transcript_paragraphs,
)
from _meeting_card import notify_meeting_card, render_meeting_summary_card  # noqa: E402
from meeting_session_notify import meeting_session_notify  # noqa: E402
from meeting_transcript_prepare import _call as transcript_call  # noqa: E402
from meeting_transcript_prepare import _collect_paragraphs  # noqa: E402

from psi_agent._appdata import resolve_appdata_root  # noqa: E402

_ANALYSIS_KEYS = ("analysis_text", "meeting_summary", "positive_negative_overview")


def _job_for_name(meeting_name: str):
    for job in MEETING_JOBS:
        if job.name == meeting_name:
            return job
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, json.JSONDecodeError, OSError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError, OSError:
        return ""


def _analysis_from_state(state: dict[str, Any]) -> dict[str, str] | None:
    if not all(str(state.get(key) or "").strip() for key in _ANALYSIS_KEYS):
        return None
    return {key: str(state.get(key) or "") for key in _ANALYSIS_KEYS}


async def _transcript_for_record(
    *,
    base: str,
    job: Any,
    record_file_id: str,
    live_is_record: bool,
) -> tuple[str, str]:
    """Return ``(transcript, source)`` — live → archive → fetch-by-record."""
    if live_is_record:
        text = _read_text(meeting_artifact_root(base, job.name) / "transcript.md")
        if text.strip():
            return text, "live"
    archive_dir = record_archive_dir(base, job.name, record_file_id)
    text = _read_text(archive_dir / "transcript.md")
    if text.strip():
        return text, "archive"
    token_env = meeting_credential_env(job.name, job.meeting_code)
    paragraphs = await _collect_paragraphs(record_file_id, token_env=token_env)
    rendered = render_transcript_paragraphs(paragraphs)
    if not rendered.strip():
        return "", "unavailable"
    await atomic_write_text(str(archive_dir / "transcript.md"), rendered)
    await atomic_write_text(str(archive_dir / "transcript_paragraphs.json"), json.dumps(paragraphs, ensure_ascii=False))
    return rendered, "fetched"


async def _smart_minutes_text(*, base: str, job: Any, record_file_id: str, live_is_record: bool) -> str:
    candidates = [record_archive_dir(base, job.name, record_file_id) / "smart_minutes.json"]
    if live_is_record:
        candidates.insert(0, meeting_artifact_root(base, job.name) / "smart_minutes.json")
    for path in candidates:
        text = _read_text(path)
        if text.strip():
            return text
    try:
        token_env = meeting_credential_env(job.name, job.meeting_code)
        payload = await transcript_call("get_smart_minutes", {"record_file_id": record_file_id}, token_env=token_env)
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:  # 智能纪要只是辅助, 失败不阻断
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


async def _deliver(job: Any, base: str, record_file_id: str, analysis: dict[str, str]) -> dict[str, Any]:
    """卡片优先投递, 失败回落文本; 回执按 record+身份幂等(已投过的自动 already_sent)。"""
    receipts: dict[str, Any] = {}
    card_render = render_meeting_summary_card(
        meeting_title=job.title,
        meeting_code=job.meeting_code,
        meeting_date=datetime.now().astimezone().date().isoformat(),
        analysis=analysis,
    )
    if card_render.get("ok") and isinstance(card_render.get("card"), dict):
        card_json = json.dumps(card_render["card"], ensure_ascii=False)
        targets: list[str] = []
        for candidate in (*job.summary_recipients, *job.overview_recipients):
            if candidate not in targets:
                targets.append(candidate)
        for recipient in targets:
            receipt = json.loads(
                await notify_meeting_card(
                    meeting_name=job.name,
                    recipient=recipient,
                    card_json=card_json,
                    record_file_id=record_file_id,
                    appdata_root=base,
                )
            )
            receipts[recipient] = {"ok": bool(receipt.get("ok")), "status": str(receipt.get("status") or "")}
        return receipts
    targets = [(recipient, analysis["meeting_summary"]) for recipient in job.summary_recipients] + [
        (recipient, analysis["positive_negative_overview"]) for recipient in job.overview_recipients
    ]
    for recipient, text in targets:
        receipt = json.loads(
            await meeting_session_notify(
                meeting_name=job.name,
                recipient=recipient,
                text=text or analysis["analysis_text"],
                record_file_id=record_file_id,
                appdata_root=base,
            )
        )
        receipts[recipient] = {"ok": bool(receipt.get("ok")), "status": str(receipt.get("status") or "")}
    return receipts


async def meeting_pipeline_replay(
    meeting_name: str,
    record_file_id: str,
    force_reanalyze: bool = False,
    redeliver: bool = True,
    appdata_root: str = "",
) -> str:
    """按 record_file_id 补跑一场会议: 复用/重算分析, 并补发未投递的收件人。

    典型场景: 某场当时分析失败或投递失败后被新场次挤掉 (单槽状态不再跟踪它),
    现在要"把缺的那半补齐"。已成功投递的收件人不会重复收到 (回执幂等)。
    """
    try:
        name = str(meeting_name or "").strip()
        record = _safe_record_dir_name(record_file_id)
        if not name:
            raise ValueError("meeting_name is required")
        job = _job_for_name(name)
        if job is None:
            return json.dumps(
                {
                    "ok": False,
                    "status": "unknown_meeting",
                    "error": f"未在白名单中的会议: {name}",
                    "allowed_meetings": [item.name for item in MEETING_JOBS],
                },
                ensure_ascii=False,
            )
        base = await resolve_appdata_root(appdata_root)
        manifest = read_meeting_manifest(base, name)
        live_is_record = str(manifest.get("record_file_id") or "") == record
        archive_dir = record_archive_dir(base, name, record)

        transcript, source = await _transcript_for_record(
            base=base, job=job, record_file_id=record, live_is_record=live_is_record
        )
        if not transcript.strip():
            return json.dumps(
                {
                    "ok": False,
                    "status": "transcript_unavailable",
                    "meeting_name": name,
                    "record_file_id": record,
                    "error": "该场没有可用的原始转写(archive 无、按 record 重拉也为空); 请先确认录制已转码完成。",
                },
                ensure_ascii=False,
            )

        state_source = archive_dir / "pipeline_state.json"
        if live_is_record and not state_source.is_file():
            state_source = meeting_artifact_root(base, name) / "pipeline_state.json"
        reused = _analysis_from_state(_read_json(state_source))
        analysis_reused = bool(reused) and not force_reanalyze
        if analysis_reused:
            analysis = reused
        else:
            smart_minutes = await _smart_minutes_text(
                base=base, job=job, record_file_id=record, live_is_record=live_is_record
            )
            sop_rules, positive_rules = await pipeline._load_analysis_rules(job)
            analysis = await pipeline._analyze_meeting_transcript(
                transcript, smart_minutes, job=job, sop_rules=sop_rules, positive_rules=positive_rules
            )

        await atomic_write_text(str(archive_dir / "replay_analysis.md"), analysis["analysis_text"])
        await atomic_write_text(
            str(archive_dir / "replay_analysis.json"),
            json.dumps(
                {
                    "meeting_name": name,
                    "meeting_code": job.meeting_code,
                    "record_file_id": record,
                    "analysis_reused": analysis_reused,
                    "force_reanalyze": bool(force_reanalyze),
                    "transcript_source": source,
                    "updated_at": datetime.now().astimezone().isoformat(),
                    **analysis,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

        receipts: dict[str, Any] = {}
        if redeliver:
            receipts = await _deliver(job, base, record, analysis)

        delivered = all(bool(item.get("ok")) for item in receipts.values()) if receipts else None
        status = "replayed"
        if receipts:
            status = "delivered" if delivered else "delivery_pending"
        metrics_line = json.dumps(
            {
                "ts": datetime.now().astimezone().isoformat(),
                "meeting_name": name,
                "record_file_id": record,
                "triggered_at": "replay",
                "analysis_reused": analysis_reused,
                "force_reanalyze": bool(force_reanalyze),
                "transcript_source": source,
                "recipients": receipts,
            },
            ensure_ascii=False,
        )
        # 指标写失败绝不掩盖主结果(与管道 run_metrics 同一纪律)
        try:
            handle = await anyio.open_file(str(archive_dir / "replay_metrics.jsonl"), "a", encoding="utf-8")
            async with handle:
                await handle.write(metrics_line + "\n")
        except OSError:
            pass
        return json.dumps(
            {
                "ok": True,
                "status": status,
                "meeting_name": name,
                "record_file_id": record,
                "analysis_reused": analysis_reused,
                "transcript_source": source,
                "analysis_chars": len(analysis["analysis_text"]),
                "recipients": receipts,
                "artifacts": ["replay_analysis.md", "replay_analysis.json", "replay_metrics.jsonl"],
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_pipeline_replay_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_pipeline_replay"]
