"""Run the deterministic daily-meeting analysis pipeline.

One run covers: prepare (fetch latest completed transcript + smart minutes) →
read the complete raw transcript chunk by chunk → AI analysis with versioned
SOP / positive-negative rule snapshots → fixed-route delivery with idempotent
receipts.  Every terminal state is appended to ``run_metrics.jsonl``; hard
failures additionally alert the job's ``alert_recipients`` (once per record /
per day), so a silent scheduler never fails silently.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
import yaml
from _meeting_automation import (
    MeetingJob,
    atomic_write_text,
    automation_resources,
    automation_runtime,
    meeting_artifact_root,
    meeting_job_for,
    path_lock,
    read_meeting_manifest,
)
from _meeting_card import notify_meeting_card, render_meeting_summary_card
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
#: 引擎运行参数单一来源: config/meeting-automation.yaml 的 runtime 段。
_MEETING_RUNTIME = automation_runtime()
_MEETING_RESOURCES = automation_resources()
AGENT_ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_CHUNK_CHARS = int(_MEETING_RUNTIME["analysis"]["chunk_chars"])
ANALYSIS_TEMPERATURE = float(_MEETING_RUNTIME["analysis"]["temperature"])
ALERT_MESSAGE_PREFIX = str(_MEETING_RUNTIME["alerts"]["message_prefix"])
ALERT_ERROR_TRUNCATE_CHARS = int(_MEETING_RUNTIME["alerts"]["error_truncate_chars"])
SKILLS_ROOT = AGENT_ROOT / "skills"
#: 会议 SOP 判定口径与正负面规则快照的路径来自 yaml resources (相对 agent 包根);
#: 缺失/契约损坏由 _load_analysis_rules 显式失败。
MEETING_SOP_CONFIG_PATH = AGENT_ROOT / str(_MEETING_RESOURCES["meeting_sop_config_file"])
POSITIVE_NEGATIVE_RULES_PATH = AGENT_ROOT / str(_MEETING_RESOURCES["positive_rules_file"])


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


async def _write_json(path: Path, value: dict[str, Any]) -> None:
    await atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


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
        "temperature": ANALYSIS_TEMPERATURE,
        # Route analysis turns into the scheduler Session that fired this tool
        # (any fixed org session id), never into a personal conversation.
        "routing": {"session_id": get_session_id()},
    }
    chunks: list[str] = []
    async for delta in ai_client.stream(request):
        if delta.content:
            chunks.append(delta.content)
    return _extract_json("".join(chunks))


def _analysis_system_prompt(job: MeetingJob | None = None) -> str:
    """Per-job system prompt.  Rules text is injected in the user content so a
    changed snapshot never goes unnoticed; this side states only identity,
    evidence hierarchy and output contract."""

    if job is not None:
        title = job.title
        name = job.name
    else:
        title = "周中对齐会"
        name = DAILY_MEETING_NAME
    return (
        f"你是 HaiTun 的 {title} 分析器 (任务 {name})。只分析白名单固定会议, "
        "不写入正式正负面总表, 不计分, 不进入绩效。"
        "必须以原始转写为主要证据, 智能纪要只能辅助。输出严格 JSON, 键为:"
        "analysis_text、meeting_summary、positive_negative_overview。"
        "正负面判断必须区分事实、证据缺口和推断; 证据不足写待补充证据, 不要臆测。"
        "负面候选必须给出正确做法、立即补救和预防措施; 同时判断会议是否符合其会议 SOP。"
    )


def _meeting_meta(job: MeetingJob | None, chunk_index: int | None = None, chunk_total: int | None = None) -> str:
    """会议身份元数据: 让每段分析都可归属到具体场次/日期, 不靠模型猜。"""
    now = datetime.now().astimezone()
    part = f" | 分块 {chunk_index}/{chunk_total}" if chunk_index is not None and chunk_total else ""
    if job is None:
        return f"会议元数据: 未知会议 | 日期={now.date().isoformat()} | 时刻={now.strftime('%H:%M:%S %z')}{part}"
    return (
        f"会议元数据: 会议名={job.name} | 会议标题={job.title}"
        f" | 会议号={job.meeting_code} | 日期={now.date().isoformat()}"
        f" | 时刻={now.strftime('%H:%M:%S %z')}{part}"
    )


async def _load_analysis_rules(job: MeetingJob) -> tuple[str, str]:
    """读取会议 SOP 引擎/口径与正负面规则快照; 缺失或契约损坏即显式失败, 不静默降级。

    会议 SOP = SKILL 引擎纪律 (meeting-sop/*/SKILL.md) + 判定口径
    (config/meeting-sop.yaml, 与 todo-sop.yaml 同一契约模式); 口径缺失/字段结构不符
    契约时本次运行直接失败。
    """
    sop_parts: list[str] = []
    for rel in job.analysis_sop_skills:
        skill_md = SKILLS_ROOT / Path(rel) / "SKILL.md"
        if not skill_md.is_file():
            raise RuntimeError(f"会议 SOP skill 缺失: {skill_md} (检查 MeetingJob.analysis_sop_skills)")
        sop_parts.append(f"===== {rel} (引擎) =====\n{await anyio.Path(str(skill_md)).read_text(encoding='utf-8')}")
    if not MEETING_SOP_CONFIG_PATH.is_file():
        raise RuntimeError(f"会议 SOP 配置缺失: {MEETING_SOP_CONFIG_PATH}")
    config_text = await anyio.Path(str(MEETING_SOP_CONFIG_PATH)).read_text(encoding="utf-8")
    try:
        config = yaml.safe_load(config_text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"会议 SOP 配置无法解析: {MEETING_SOP_CONFIG_PATH}: {exc}") from exc
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("meta"), dict)
        or not isinstance(config.get("rules"), list)
        or not config["meta"].get("version")
    ):
        raise RuntimeError(f"会议 SOP 配置不符合契约(需 meta.version + rules): {MEETING_SOP_CONFIG_PATH}")
    sop_parts.append(f"===== config/meeting-sop.yaml (判定口径, 业务条目以 active 为准) =====\n{config_text}")
    if not POSITIVE_NEGATIVE_RULES_PATH.is_file():
        raise RuntimeError(f"正负面规则快照缺失: {POSITIVE_NEGATIVE_RULES_PATH}")
    positive_rules = await anyio.Path(str(POSITIVE_NEGATIVE_RULES_PATH)).read_text(encoding="utf-8")
    return "\n\n".join(sop_parts), positive_rules


async def _analyze_meeting_transcript(
    transcript: str,
    smart_minutes: str = "",
    *,
    job: MeetingJob | None = None,
    sop_rules: str = "",
    positive_rules: str = "",
    stats: dict[str, int] | None = None,
) -> dict[str, str]:
    """Analyze every transcript chunk, then synthesize the chunk analyses.

    The transcript is never shortened by taking a prefix. Each bounded chunk is
    analyzed independently, and the final call receives all chunk conclusions.
    When *job* is given, every request carries meeting identity metadata plus
    the versioned SOP / positive-negative rule snapshots; *stats* (optional)
    collects ``ai_calls`` / ``ai_input_chars`` for run metrics.
    """
    ai_socket = current_tool_ai_socket()
    if not ai_socket:
        raise RuntimeError("meeting pipeline must run inside a Gateway Session with an AI backend")
    ai_client = AiClient(ai_socket)
    transcript_chunks = [
        transcript[index : index + ANALYSIS_CHUNK_CHARS] for index in range(0, len(transcript), ANALYSIS_CHUNK_CHARS)
    ] or [""]

    def _rules_block() -> str:
        parts: list[str] = []
        if sop_rules.strip():
            parts.append(f"会议 SOP 规则(版本化快照):\n{sop_rules}")
        if positive_rules.strip():
            parts.append(f"正负面分析规则(快照):\n{positive_rules}")
        return "\n\n".join(parts)

    def _counted_call(system_prompt: str, user_content: str) -> Any:
        if stats is not None:
            stats["ai_calls"] = stats.get("ai_calls", 0) + 1
            stats["ai_input_chars"] = stats.get("ai_input_chars", 0) + len(user_content)
        return _stream_ai_json(ai_client, system_prompt=system_prompt, user_content=user_content)

    partials: list[dict[str, Any]] = []
    for index, transcript_chunk in enumerate(transcript_chunks, start=1):
        meta = _meeting_meta(job, index, len(transcript_chunks)) if job is not None else ""
        guidance = (f"{meta}\n\n" if meta else "") + (
            f"这是该场原始转写的第 {index}/{len(transcript_chunks)} 段。"
            "只依据本段中明确出现的内容记录事实; 跨段无法确认的内容标记待补充证据。"
        )
        rules = _rules_block()
        user_content = f"{guidance}\n\n原始转写片段:\n{transcript_chunk}"
        if rules:
            user_content = f"{guidance}\n\n{rules}\n\n原始转写片段:\n{transcript_chunk}"
        partials.append(await _counted_call(_analysis_system_prompt(job), user_content))
    if len(partials) == 1:
        return _normalize_analysis(partials[0])
    meta = _meeting_meta(job) if job is not None else ""
    synthesis = await _counted_call(
        _analysis_system_prompt(job),
        user_content=(f"{meta}\n\n" if meta else "")
        + (
            "下面是同一场会议原始转写各段的独立分析。请合并去重并只保留有证据的结论, "
            "不能遗漏任何片段中的行为事实; 无法互相印证的内容明确标记待补充证据。"
            "输出完整的会议分析 JSON。\n\n"
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


async def _append_run_metrics(base: str, meeting_name: str, entry: dict[str, Any]) -> None:
    """Append one JSON line per run to ``run_metrics.jsonl`` (观察期指标)。"""
    path = meeting_artifact_root(base, meeting_name) / "run_metrics.jsonl"
    line = json.dumps(entry, ensure_ascii=False)
    # 指标写失败绝不掩盖主结果
    with suppress(Exception):
        lock = await path_lock(path)
        file_handle = await anyio.open_file(str(path), "a", encoding="utf-8")
        async with lock, file_handle:
            await file_handle.write(line + "\n")


async def _notify_failure_alert(
    job: MeetingJob,
    base: str,
    meeting_name: str,
    *,
    record_file_id: str,
    status: str,
    error: str,
) -> None:
    """向 ``alert_recipients`` 发一条失败告警 (幂等: 同一 record/同一天只发一次)。

    回执键按 record_file_id; 采集阶段失败拿不到录制时用日期伪键, 保证每天至多一条
    同状态告警, 不会刷屏。告警发送失败只吞掉, 主结果不受影响。
    """
    recipients = job.alert_recipients
    if not recipients or not base:
        return
    title = job.title
    date = datetime.now().astimezone().date().isoformat()
    key_record = record_file_id or f"__alert_no_record__{meeting_name}__{date}"
    text = (
        f"{ALERT_MESSAGE_PREFIX} {title}({meeting_name}) {status}"
        f"{f' | record={record_file_id}' if record_file_id else ''}"
        f" | {date}\n{error[:ALERT_ERROR_TRUNCATE_CHARS]}"
    )
    for recipient in recipients:
        # 告警失败不得影响主结果
        with suppress(Exception):
            await meeting_session_notify(
                meeting_name=meeting_name,
                recipient=recipient,
                text=text,
                record_file_id=key_record,
                appdata_root=base,
            )


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
    started = time.perf_counter()

    def _ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    async def _record(status: str, *, record_file_id: str = "", entry: dict[str, Any] | None = None) -> None:
        row = {
            "ts": datetime.now().astimezone().isoformat(),
            "meeting_name": meeting_name,
            "meeting_code": meeting_code,
            "status": status,
            "record_file_id": record_file_id,
            "stages_ms": entry.pop("stages_ms", {}) if entry else {},
            **(entry or {}),
        }
        # 指标写失败不影响主结果
        with suppress(Exception):
            await _append_run_metrics(base, meeting_name, row)

    try:
        base = await resolve_appdata_root(appdata_root)
        artifact = meeting_artifact_root(base, meeting_name)
        state_path = artifact / "pipeline_state.json"
        state: dict[str, Any] = _json_file(state_path)
        prepare_started = time.perf_counter()
        prepare_result = json.loads(
            await meeting_transcript_prepare(
                meeting_code=meeting_code,
                meeting_name=meeting_name,
                appdata_root=base,
            )
        )
        prepare_ms = int((time.perf_counter() - prepare_started) * 1000)
        if not prepare_result.get("ok"):
            error_text = str(prepare_result.get("error", "Tencent transcript preparation failed"))
            await _write_json(state_path, {"status": "transcript_prepare_failed", "prepare": prepare_result})
            await _record(
                "transcript_prepare_failed", record_file_id="", entry={"error": error_text[:ALERT_ERROR_TRUNCATE_CHARS]}
            )
            await _notify_failure_alert(
                meeting_job,
                base,
                meeting_name,
                record_file_id="",
                status="transcript_prepare_failed",
                error=error_text,
            )
            return json.dumps(
                {"ok": False, "status": "transcript_prepare_failed", "error": error_text},
                ensure_ascii=False,
            )
        manifest = read_meeting_manifest(base, meeting_name)
        record_file_id = str(
            prepare_result.get("record_file_id") or manifest.get("record_file_id") or state.get("record_file_id") or ""
        )
        if not record_file_id:
            await _write_json(state_path, {"status": "transcript_pending", "prepare": prepare_result})
            await _record("transcript_pending", record_file_id="")
            return json.dumps({"ok": True, "status": "transcript_pending"}, ensure_ascii=False)

        analysis_stats: dict[str, int] = {}
        analysis_new = False
        if state.get("record_file_id") == record_file_id and state.get("analysis_text"):
            analysis = _normalize_analysis(state)
        else:
            read_started = time.perf_counter()
            transcript, source_chunks = await _read_full_transcript(
                base, meeting_name, int(manifest.get("chunk_count") or 1)
            )
            read_ms = int((time.perf_counter() - read_started) * 1000)
            smart_minutes = ""
            smart_path = artifact / "smart_minutes.json"
            smart_path_anyio = anyio.Path(str(smart_path))
            if await smart_path_anyio.is_file():
                smart_minutes = await smart_path_anyio.read_text(encoding="utf-8")
            analyze_started = time.perf_counter()
            sop_rules, positive_rules = await _load_analysis_rules(meeting_job)
            analysis = await _analyze_meeting_transcript(
                transcript,
                smart_minutes,
                job=meeting_job,
                sop_rules=sop_rules,
                positive_rules=positive_rules,
                stats=analysis_stats,
            )
            analyze_ms = int((time.perf_counter() - analyze_started) * 1000)
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
        notify_started = time.perf_counter()
        notifications_ok = True
        # 卡片优先: 每收件人一张会议总结卡(summary/analysis/overview 合成一张,
        # 收件人 = summary + overview 名单去重)。渲染失败(模板缺失/数据异常)
        # 回落文本双名单, 与历史行为一致。
        card_render = render_meeting_summary_card(
            meeting_title=meeting_job.title,
            meeting_code=meeting_code,
            meeting_date=datetime.now().astimezone().date().isoformat(),
            analysis=analysis,
        )
        if card_render.get("ok") and isinstance(card_render.get("card"), dict):
            card_json = json.dumps(card_render["card"], ensure_ascii=False)
            card_recipients: list[str] = []
            for candidate in (*meeting_job.summary_recipients, *meeting_job.overview_recipients):
                if candidate not in card_recipients:
                    card_recipients.append(candidate)
            for recipient in card_recipients:
                receipt = json.loads(
                    await notify_meeting_card(
                        meeting_name=meeting_name,
                        recipient=recipient,
                        card_json=card_json,
                        record_file_id=record_file_id,
                        appdata_root=base,
                    )
                )
                receipts[recipient] = receipt
                notifications_ok = notifications_ok and bool(receipt.get("ok"))
        else:
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
        notify_ms = int((time.perf_counter() - notify_started) * 1000)
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
        entry = {
            "stages_ms": {
                "prepare": prepare_ms,
                "read": read_ms if analysis_new else None,
                "analyze": analyze_ms if analysis_new else None,
                "notify": notify_ms,
                "total": _ms(),
            },
            "analysis_reused": not analysis_new,
            "analysis": {
                "ai_calls": analysis_stats.get("ai_calls", 0),
                "ai_input_chars": analysis_stats.get("ai_input_chars", 0),
            },
            "transcript_chars": int(manifest.get("transcript_chars") or 0),
            "paragraph_count": int(manifest.get("paragraph_count") or 0),
            "chunk_count": int(manifest.get("chunk_count") or 0),
            "notifications": {
                name: {"ok": bool(receipt.get("ok")), "status": receipt.get("status", "")}
                for name, receipt in receipts.items()
            },
        }
        await _record(final_status, record_file_id=record_file_id, entry=entry)
        if not notifications_ok:
            failures = {
                name: str(r.get("status") or r.get("error") or "failed")
                for name, r in receipts.items()
                if not r.get("ok")
            }
            await _notify_failure_alert(
                meeting_job,
                base,
                meeting_name,
                record_file_id=record_file_id,
                status=final_status,
                error=f"通知失败收件人: {json.dumps(failures, ensure_ascii=False)}",
            )
        return json.dumps(
            {"ok": True, "status": final_status, "meeting_name": meeting_name, "record_file_id": record_file_id},
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        # 兜底记录/告警失败不影响返回
        with suppress(Exception):
            await _record(
                "meeting_pipeline_failed", record_file_id="", entry={"error": error_text[:ALERT_ERROR_TRUNCATE_CHARS]}
            )
            await _notify_failure_alert(
                meeting_job,
                base,
                meeting_name,
                record_file_id="",
                status="meeting_pipeline_failed",
                error=error_text,
            )
        return json.dumps(
            {"ok": False, "status": "meeting_pipeline_failed", "error": error_text},
            ensure_ascii=False,
        )


__all__ = ["meeting_pipeline_run"]
