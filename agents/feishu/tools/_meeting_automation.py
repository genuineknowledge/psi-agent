"""Shared meeting automation primitives.

Meeting jobs are deliberately code-owned rather than user-configured.  Their
schedules ship as static company seed tasks under ``agents/feishu/schedules``
(one ``TASK.md`` per job and per retry, projected from :data:`MEETING_JOBS` by
:func:`meeting_schedule_files`); the generic SchedulerManager seed mechanism
drops them into the configured company workspace and owns the scheduler
Session.  Meeting artifacts live in AppData so every Feishu Session can read
the same source and analysis results.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psi_agent._appdata import resolve_appdata_root

TRANSCRIPT_CHUNK_CHARS = 8_000
MAIN_MEETING_GROUP_NAME = "HaiTun Agent主战场"


@dataclass(frozen=True)
class MeetingJob:
    name: str
    meeting_code: str
    cron: str
    title: str
    recipients: tuple[str, ...]
    retry_crons: tuple[str, ...] = ()
    summary_recipients: tuple[str, ...] = ("程秀秀",)
    overview_recipients: tuple[str, ...] = ("罗霖",)
    token_env: str = "TENCENT_MEETING_TOKEN"
    fire: str = "tool"
    tool_name: str = "meeting_pipeline_run"
    tool_args: tuple[tuple[str, str], ...] = ()


MEETING_JOBS: tuple[MeetingJob, ...] = (
    MeetingJob(
        name="weekday-alignment",
        meeting_code="57152787045",
        cron="0 12 * * 1,3,5",
        title="周中对齐会",
        recipients=(MAIN_MEETING_GROUP_NAME, "罗霖"),
        summary_recipients=(MAIN_MEETING_GROUP_NAME,),
        tool_args=(("meeting_name", "weekday-alignment"), ("meeting_code", "57152787045")),
    ),
    MeetingJob(
        name="weekday-alignment-1100",
        meeting_code="42654699903",
        cron="0 12 * * 1,3,5",
        title="日会",
        recipients=("张浩", "王金旺", "罗霖"),
        retry_crons=("30 17 * * 1,3,5",),
        summary_recipients=("张浩", "王金旺"),
        overview_recipients=("罗霖",),
        token_env="TENCENT_MEETING_TOKEN_42654699903",
        tool_args=(("meeting_name", "weekday-alignment-1100"), ("meeting_code", "42654699903")),
    ),
)


def meeting_credential_env(meeting_name: str, meeting_code: str) -> str:
    """Return the process environment variable for one fixed meeting.

    Credentials stay outside code and tool arguments.  The meeting name/code
    pair is deliberately checked here so a task cannot accidentally use the
    host credential for a different meeting.
    """

    name = meeting_name.strip()
    code = meeting_code.strip()
    return meeting_job_for(name, code).token_env


def meeting_job_for(meeting_name: str, meeting_code: str) -> MeetingJob:
    """Return the code-owned job matching a meeting name and number."""

    name = meeting_name.strip()
    code = meeting_code.strip()
    for job in MEETING_JOBS:
        if job.name == name and job.meeting_code == code:
            return job
    raise ValueError(f"meeting {name!r} and code {code!r} does not match a fixed meeting")


def meeting_store_root(appdata_root: str = "") -> Path:
    """Return the shared artifact root without relying on a user Session."""

    explicit = appdata_root.strip() or os.environ.get("PSI_APPDATA", "").strip()
    # resolve_appdata_root is async for filesystem normalization; tools call the
    # async ``async_meeting_store_root`` helper below.  This sync helper is for
    # tests and callers that already have an absolute env path.
    if explicit:
        return Path(explicit).expanduser().resolve() / "meeting-session"
    return Path.home() / ".local" / "share" / "Haitun" / "meeting-session"


async def async_meeting_store_root(appdata_root: str = "") -> Path:
    root = await resolve_appdata_root(appdata_root)
    return Path(root) / "meeting-session"


def chunk_text(text: str, max_chars: int = TRANSCRIPT_CHUNK_CHARS) -> list[str]:
    """Split text without dropping bytes or returning an oversized tool result."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)] or [""]


def _json_payload(raw: str | dict[str, Any] | list[Any]) -> Any:
    """Decode a Tencent MCP text response, tolerating a small log prefix."""
    if isinstance(raw, (dict, list)):
        payload: Any = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # The proxy normally prints JSON, but error messages or wrappers can put
            # a line before it.  Find the first complete object/array conservatively.
            for match in re.finditer(r"[\[{]", text):
                try:
                    payload = json.loads(text[match.start() :])
                    break
                except json.JSONDecodeError:
                    continue
            else:
                raise ValueError(f"Tencent Meeting response is not JSON: {text[:240]}")

    if isinstance(payload, dict) and "status_code" in payload and "body" in payload:
        body = payload.get("body")
        decoded = _json_payload(body) if isinstance(body, (str, dict, list)) else {}
        if isinstance(decoded, dict):
            headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
            decoded = dict(decoded)
            decoded["_transport"] = {
                "status_code": payload.get("status_code"),
                "x_tc_trace": headers.get("X-Tc-Trace") or headers.get("x-tc-trace") or "",
                "rpc_uuid": headers.get("rpcUuid") or headers.get("Rpcuuid") or "",
            }
        return decoded
    return payload


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _record_file_type(record: dict[str, Any]) -> str:
    return (
        str(
            record.get("record_file_type")
            or record.get("file_type")
            or record.get("record_type")
            or record.get("type")
            or ""
        )
        .strip()
        .casefold()
    )


def _is_transcript_record(record: dict[str, Any]) -> bool:
    value = _record_file_type(record)
    return any(token in value for token in ("文字转写", "转写", "transcript", "transcription", "text"))


def _record_sort_key(record: dict[str, Any]) -> str:
    for key in (
        "media_start_time",
        "record_start_time",
        "start_time",
        "end_time",
        "create_time",
        "created_at",
        "update_time",
    ):
        value = record.get(key)
        if value:
            return str(value)
    return ""


def _record_candidates(payload: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(payload, dict):
        meetings = payload.get("record_meetings")
        if isinstance(meetings, list):
            for meeting in meetings:
                if not isinstance(meeting, dict):
                    continue
                files = meeting.get("record_files")
                if not isinstance(files, list):
                    continue
                for record_file in files:
                    if not isinstance(record_file, dict):
                        continue
                    normalized = {key: value for key, value in meeting.items() if key != "record_files"}
                    normalized.update(record_file)
                    normalized["record_file_type"] = normalized.get("record_file_type") or meeting.get("record_type")
                    record_file_id = str(normalized.get("record_file_id") or "").strip()
                    if record_file_id and record_file_id not in seen:
                        candidates.append(normalized)
                        seen.add(record_file_id)
    for record in _walk_dicts(payload):
        record_file_id = str(record.get("record_file_id") or record.get("file_id") or "").strip()
        if record_file_id and record_file_id not in seen:
            candidates.append(dict(record))
            seen.add(record_file_id)
    return candidates


def extract_latest_transcript_record(payload: Any, processed_ids: set[str] | None = None) -> dict[str, Any] | None:
    """Find a completed transcript from the newest meeting occurrence only."""
    processed = processed_ids or set()
    records = _record_candidates(payload)
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        occurrence_id = str(record.get("sub_meeting_id") or "").strip()
        if occurrence_id:
            occurrences.setdefault(occurrence_id, []).append(record)
    if occurrences:
        latest_occurrence_id = max(
            occurrences,
            key=lambda occurrence_id: max(_record_sort_key(record) for record in occurrences[occurrence_id]),
        )
        records = occurrences[latest_occurrence_id]
        if any(
            record.get("state_int", record.get("state")) is not None
            and not should_process_recording({**record, "record_file_id": "__state_check__"}, set())
            for record in records
        ):
            return None
    candidates: list[dict[str, Any]] = []
    for record in records:
        record_file_id = str(record.get("record_file_id") or record.get("file_id") or "").strip()
        if not record_file_id or not _is_transcript_record(record):
            continue
        normalized = dict(record)
        normalized["record_file_id"] = record_file_id
        if should_process_recording(normalized, processed):
            candidates.append(normalized)
    return max(candidates, key=_record_sort_key) if candidates else None


def _paragraph_id(item: dict[str, Any]) -> str:
    return str(item.get("pid") or item.get("paragraph_id") or item.get("id") or "").strip()


def extract_paragraph_ids(payload: Any) -> list[str]:
    ids: list[str] = []
    for item in _walk_dicts(payload):
        pid = _paragraph_id(item)
        if pid and pid not in ids and any(key in item for key in ("pid", "paragraph_id")):
            ids.append(pid)
    return ids


def extract_paragraph_items(payload: Any) -> list[dict[str, Any]]:
    """Return paragraph-like objects while avoiding duplicate nested wrappers."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _walk_dicts(payload):
        pid = _paragraph_id(item)
        has_content = any(key in item for key in ("sentences", "words", "content", "text"))
        if not pid or not has_content or pid in seen:
            continue
        seen.add(pid)
        items.append(item)
    return items


def render_transcript_paragraphs(paragraphs: list[dict[str, Any]]) -> str:
    """Render all raw transcript paragraphs into readable, lossless text."""
    lines: list[str] = []
    for paragraph in paragraphs:
        timestamp = str(
            paragraph.get("timestamp") or paragraph.get("start_time") or paragraph.get("start") or ""
        ).strip()
        speaker = str(
            paragraph.get("speaker")
            or paragraph.get("speaker_name")
            or paragraph.get("user_name")
            or paragraph.get("username")
            or "未知发言人"
        ).strip()
        pieces: list[str] = []
        for sentence in paragraph.get("sentences", []) if isinstance(paragraph.get("sentences"), list) else []:
            if isinstance(sentence, dict):
                value = sentence.get("text") or sentence.get("content") or sentence.get("sentence")
                if not value and isinstance(sentence.get("words"), list):
                    value = "".join(
                        str(word.get("text") or word.get("content") or "")
                        for word in sentence["words"]
                        if isinstance(word, dict)
                    )
            else:
                value = sentence
            if value:
                pieces.append(str(value))
        if not pieces:
            content = paragraph.get("content") or paragraph.get("text") or ""
            if isinstance(content, list):
                pieces.extend(
                    str(item.get("text") or item.get("content") or "") for item in content if isinstance(item, dict)
                )
            elif content:
                pieces.append(str(content))
        content_text = "".join(piece for piece in pieces if piece)
        if not content_text:
            continue
        prefix = f"[{timestamp}] " if timestamp else ""
        lines.append(f"{prefix}{speaker}: {content_text}")
    return "\n".join(lines)


def meeting_artifact_root(appdata_root: str, meeting_name: str) -> Path:
    name = meeting_name.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("meeting_name must be a simple directory name")
    return meeting_store_root(appdata_root) / name


def read_meeting_manifest(appdata_root: str | Path, meeting_name: str) -> dict[str, Any]:
    path = meeting_artifact_root(str(appdata_root), meeting_name) / "manifest.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def should_process_recording(record: dict[str, Any], processed_ids: set[str]) -> bool:
    """Only process completed, previously unseen transcript recording files."""

    record_file_id = str(record.get("record_file_id") or "").strip()
    if not record_file_id or record_file_id in processed_ids:
        return False
    state = record.get("state_int", record.get("state"))
    if isinstance(state, str):
        state_text = state.strip().lower()
        if state_text not in {"3", "completed", "转码完成", "transcoded"}:
            return False
    elif state is not None and int(state) != 3:
        return False
    return True


def _task_body(job: MeetingJob, *, name: str | None = None, cron: str | None = None) -> str:
    tool_args = json.dumps(dict(job.tool_args), ensure_ascii=False, separators=(",", ":"))
    return f"""---
name: {name or job.name}
description: 会后自动获取{job.title}原始全文转写并分析
cron: \"{cron or job.cron}\"
visibility: silent
fire: {job.fire}
tool: {job.tool_name}
tool_args: {tool_args}
---
"""


def _job_schedules(job: MeetingJob) -> list[tuple[str, str]]:
    schedules = [(job.name, job.cron)]
    for retry_cron in job.retry_crons:
        fields = retry_cron.split()
        if len(fields) != 5:
            raise ValueError(f"invalid retry cron for {job.name}: {retry_cron}")
        minute, hour = fields[:2]
        schedules.append((f"{job.name}-retry-{hour.zfill(2)}{minute.zfill(2)}", retry_cron))
    return schedules


def meeting_schedule_files() -> dict[str, str]:
    """Project ``MEETING_JOBS`` onto static seed TASK.md files.

    Returns ``{schedule directory name: TASK.md content}`` for every job cron
    and every retry cron.  The committed files under
    ``agents/feishu/schedules/<name>/TASK.md`` must equal this projection —
    the scheduler seeds them into the company workspace verbatim (only when
    missing), and the consistency test pins both sides to this single source.
    """
    files: dict[str, str] = {}
    for job in MEETING_JOBS:
        for schedule_name, cron in _job_schedules(job):
            files[schedule_name] = _task_body(job, name=schedule_name, cron=cron)
    return files


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "MAIN_MEETING_GROUP_NAME",
    "MEETING_JOBS",
    "MeetingJob",
    "async_meeting_store_root",
    "chunk_text",
    "extract_latest_transcript_record",
    "extract_paragraph_ids",
    "extract_paragraph_items",
    "json_text",
    "meeting_artifact_root",
    "meeting_credential_env",
    "meeting_job_for",
    "meeting_schedule_files",
    "meeting_store_root",
    "read_meeting_manifest",
    "render_transcript_paragraphs",
    "should_process_recording",
]
