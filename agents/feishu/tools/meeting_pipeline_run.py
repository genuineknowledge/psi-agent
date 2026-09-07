"""Run the deterministic daily-meeting analysis pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import anyio
from _meeting_automation import meeting_artifact_root, meeting_job_for, read_meeting_manifest
from meeting_session_notify import meeting_session_notify
from meeting_session_read import meeting_session_read
from meeting_session_write import meeting_session_write
from meeting_transcript_prepare import meeting_transcript_prepare

from psi_agent._appdata import resolve_appdata_root
from psi_agent._session_context import get_session_id
from psi_agent.session.agent import current_tool_ai_socket
from psi_agent.session.ai_client import AiClient

DAILY_MEETING_NAME = "weekday-alignment"
DAILY_MEETING_CODE = "57152787045"
ANALYSIS_CHUNK_CHARS = 8_000
POSITIVE_NEGATIVE_SKILL_PATH = Path(__file__).resolve().parent.parent / "skills" / "positive-negative-list" / "SKILL.md"


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


async def _write_json(path: Path, value: dict[str, Any]) -> None:
    await anyio.Path(str(path.parent)).mkdir(parents=True, exist_ok=True)
    await anyio.Path(str(path)).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.S).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return {"analysis_text": text.strip()}
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return {"analysis_text": text.strip()}
    return value if isinstance(value, dict) else {"analysis_text": text.strip()}


def _normalize_analysis(value: dict[str, Any]) -> dict[str, str]:
    analysis = str(value.get("analysis_text") or value.get("analysis") or "").strip()
    summary = str(value.get("meeting_summary") or value.get("minutes") or "").strip()
    overview = str(value.get("positive_negative_overview") or value.get("ledger_overview") or "").strip()
    if not analysis:
        analysis = "\n\n".join(part for part in (summary, overview) if part).strip()
    if not summary:
        summary = analysis
    if not overview:
        overview = analysis
    return {
        "analysis_text": analysis or "未生成分析结果。",
        "meeting_summary": summary,
        "positive_negative_overview": overview,
    }


async def _stream_ai_json(
    ai_client: AiClient,
    *,
    system_prompt: str,
    user_content: str,
) -> dict[str, Any]:
    request = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": True,
        "temperature": 0,
        # Route analysis turns into the scheduler Session that fired this tool
        # (any fixed org session id), never into a personal conversation.
        "routing": {"session_id": get_session_id()},
    }
    chunks: list[str] = []
    async for delta in ai_client.stream(request):
        if delta.content:
            chunks.append(delta.content)
    return _extract_json("".join(chunks))


def _analysis_system_prompt() -> str:
    return (
        "你是 HaiTun 的日会分析器。只分析周中对齐会, 不写入正式正负面总表, 不计分, 不进入绩效。"
        "必须以原始转写为主要证据, 智能纪要只能辅助。输出严格 JSON, 键为:"
        "analysis_text、meeting_summary、positive_negative_overview。"
        "正负面判断必须区分事实、证据缺口和推断; 证据不足写待补充证据, 不要臆测。"
        "负面候选必须给出正确做法、立即补救和预防措施; 同时判断会议是否符合周中对齐会 SOP。"
    )


async def _analyze_meeting_transcript(transcript: str, smart_minutes: str = "") -> dict[str, str]:
    """Analyze every transcript chunk, then synthesize the chunk analyses.

    The transcript is never shortened by taking a prefix. Each bounded chunk is
    analyzed independently, and the final call receives all chunk conclusions.
    """
    ai_socket = current_tool_ai_socket()
    if not ai_socket:
        raise RuntimeError("meeting pipeline must run inside a Gateway Session with an AI backend")
    skill_path = anyio.Path(str(POSITIVE_NEGATIVE_SKILL_PATH))
    skill_text = await skill_path.read_text(encoding="utf-8") if await skill_path.is_file() else ""
    ai_client = AiClient(ai_socket)
    transcript_chunks = [
        transcript[index : index + ANALYSIS_CHUNK_CHARS] for index in range(0, len(transcript), ANALYSIS_CHUNK_CHARS)
    ] or [""]
    partials: list[dict[str, Any]] = []
    for index, transcript_chunk in enumerate(transcript_chunks, start=1):
        partials.append(
            await _stream_ai_json(
                ai_client,
                system_prompt=_analysis_system_prompt(),
                user_content=(
                    f"这是原始转写的第 {index}/{len(transcript_chunks)} 段。"
                    "只依据本段中明确出现的内容记录事实; 跨段无法确认的内容标记待补充证据。\n\n"
                    f"正负面清单技能约束:\n{skill_text}\n\n"
                    f"日会原始转写片段:\n{transcript_chunk}"
                ),
            )
        )
    if len(partials) == 1:
        return _normalize_analysis(partials[0])
    synthesis = await _stream_ai_json(
        ai_client,
        system_prompt=_analysis_system_prompt(),
        user_content=(
            "下面是同一场日会原始转写各段的独立分析。请合并去重并只保留有证据的结论, "
            "不能遗漏任何片段中的行为事实; 无法互相印证的内容明确标记待补充证据。"
            "输出完整的日会分析 JSON。\n\n"
            f"智能纪要(仅供参考):\n{smart_minutes}\n\n"
            f"各段分析:\n{json.dumps(partials, ensure_ascii=False)}"
        ),
    )
    return _normalize_analysis(synthesis)


async def _read_full_transcript(base: str, meeting_name: str, chunk_count: int) -> tuple[str, list[int]]:
    chunks: list[str] = []
    indexes: list[int] = []
    index = 0
    visited: set[int] = set()
    # The manifest is only a starting estimate. Follow the reader's cursor so
    # a stale count cannot silently drop the tail of a long transcript.
    while index not in visited:
        visited.add(index)
        result = json.loads(
            await meeting_session_read(
                meeting_name=meeting_name,
                artifact="transcript",
                chunk_index=index,
                appdata_root=base,
            )
        )
        if not result.get("ok"):
            raise RuntimeError(f"transcript chunk {index} unavailable: {result.get('status', 'unknown')}")
        chunks.append(str(result.get("content") or ""))
        indexes.append(index)
        if not result.get("has_more"):
            break
        next_index = result.get("next_chunk_index")
        try:
            candidate = int(next_index) if next_index is not None else index + 1
        except TypeError, ValueError:
            candidate = index + 1
        if candidate <= index:
            candidate = index + 1
        index = candidate
    return "".join(chunks), indexes


async def meeting_pipeline_run(
    meeting_name: str = DAILY_MEETING_NAME,
    meeting_code: str = DAILY_MEETING_CODE,
    appdata_root: str = "",
) -> str:
    """获取日会完整转写, 分析并向固定收件人发送结果; 不写正式清单。"""
    try:
        meeting_job = meeting_job_for(meeting_name, meeting_code)
    except ValueError:
        return json.dumps({"ok": False, "status": "daily_meeting_only"}, ensure_ascii=False)
    try:
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        state_path = artifact / "pipeline_state.json"
        state: dict[str, Any] = _json_file(state_path)
        prepare_result = json.loads(
            await meeting_transcript_prepare(
                meeting_code=meeting_code,
                meeting_name=meeting_name,
                appdata_root=base,
            )
        )
        if not prepare_result.get("ok"):
            await _write_json(state_path, {"status": "transcript_prepare_failed", "prepare": prepare_result})
            return json.dumps(
                {
                    "ok": False,
                    "status": "transcript_prepare_failed",
                    "error": prepare_result.get("error", "Tencent transcript preparation failed"),
                },
                ensure_ascii=False,
            )
        manifest = read_meeting_manifest(base, meeting_name)
        record_file_id = str(
            prepare_result.get("record_file_id") or manifest.get("record_file_id") or state.get("record_file_id") or ""
        )
        if not record_file_id:
            await _write_json(state_path, {"status": "transcript_pending", "prepare": prepare_result})
            return json.dumps({"ok": True, "status": "transcript_pending"}, ensure_ascii=False)

        analysis_new = False
        if state.get("record_file_id") == record_file_id and state.get("analysis_text"):
            analysis = _normalize_analysis(state)
        else:
            transcript, source_chunks = await _read_full_transcript(
                base, meeting_name, int(manifest.get("chunk_count") or 1)
            )
            smart_minutes = ""
            smart_path = artifact / "smart_minutes.json"
            smart_path_anyio = anyio.Path(str(smart_path))
            if await smart_path_anyio.is_file():
                smart_minutes = await smart_path_anyio.read_text(encoding="utf-8")
            analysis = await _analyze_meeting_transcript(transcript, smart_minutes)
            analysis_new = True
            state = {
                "record_file_id": record_file_id,
                "status": "notifications_pending",
                "source_chunks": source_chunks,
                **analysis,
            }
            await _write_json(state_path, state)

        source_chunks = [int(value) for value in state.get("source_chunks", []) if str(value).isdigit()]
        if analysis_new:
            await meeting_session_write(
                meeting_name=meeting_name,
                meeting_code=meeting_code,
                record_file_id=record_file_id,
                analysis_text=analysis["analysis_text"],
                source_chunks=",".join(str(value) for value in source_chunks),
                recipient_receipts_json=json.dumps(state.get("recipient_receipts", {}), ensure_ascii=False),
                status="notifications_pending",
                appdata_root=base,
            )
        raw_receipts = state.get("recipient_receipts")
        receipts: dict[str, Any] = dict(raw_receipts) if isinstance(raw_receipts, dict) else {}
        notifications_ok = True
        notification_targets = [
            (recipient, analysis["meeting_summary"]) for recipient in meeting_job.summary_recipients
        ] + [(recipient, analysis["positive_negative_overview"]) for recipient in meeting_job.overview_recipients]
        for recipient, text in notification_targets:
            if not text.strip():
                text = analysis["analysis_text"]
            receipt = json.loads(
                await meeting_session_notify(
                    meeting_name=meeting_name,
                    recipient=recipient,
                    text=text,
                    record_file_id=record_file_id,
                    appdata_root=base,
                )
            )
            receipts[recipient] = receipt
            notifications_ok = notifications_ok and bool(receipt.get("ok"))
        final_status = "completed" if notifications_ok else "notifications_pending"
        state.update({"status": final_status, "recipient_receipts": receipts})
        await _write_json(state_path, state)
        await meeting_session_write(
            meeting_name=meeting_name,
            meeting_code=meeting_code,
            record_file_id=record_file_id,
            analysis_text=analysis["analysis_text"],
            source_chunks=",".join(str(value) for value in source_chunks),
            recipient_receipts_json=json.dumps(receipts, ensure_ascii=False),
            status=final_status,
            appdata_root=base,
        )
        return json.dumps(
            {"ok": True, "status": final_status, "meeting_name": meeting_name, "record_file_id": record_file_id},
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        return json.dumps(
            {"ok": False, "status": "meeting_pipeline_failed", "error": f"{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )


__all__ = ["meeting_pipeline_run"]
