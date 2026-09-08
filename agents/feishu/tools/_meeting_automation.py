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
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import anyio
import yaml

from psi_agent._appdata import resolve_appdata_root

_AGENT_ROOT = Path(__file__).resolve().parent.parent
#: 会议自动化运行口径 (config/meeting-automation.yaml, 契约同 todo-sop.yaml)。
#: 硬边界: 白名单 (name/code/cron/retry/token_env 配对) 一律留代码, 见
#: ``_MEETING_JOBS_WHITELIST`` 与加载器对覆盖键的白名单校验。
MEETING_AUTOMATION_CONFIG_PATH = _AGENT_ROOT / "config" / "meeting-automation.yaml"
_MEETING_OVERLAY_KEYS = frozenset(
    {
        "title",
        "summary_recipients",
        "overview_recipients",
        "analysis_sop_skills",
        "alert_recipients",
    }
)
_WHITELIST_ONLY_KEYS = frozenset(
    {"name", "meeting_code", "cron", "retry_crons", "recipients", "fire", "tool_name", "tool_args", "token_env"}
)


def load_meeting_automation_config(path: str | Path | None = None) -> dict[str, Any]:
    """读取并契约校验 ``meeting-automation.yaml``; 缺失/解析失败/越权键一律显式报错。"""
    config_path = Path(path) if path is not None else MEETING_AUTOMATION_CONFIG_PATH
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"会议自动化配置缺失: {config_path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"会议自动化配置无法解析: {config_path}: {exc}") from exc
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), dict) for key in ("meetings", "runtime", "resources")
    ):
        raise RuntimeError(f"会议自动化配置不符合契约(需 meetings/runtime/resources 段): {config_path}")
    meetings = data["meetings"]
    for name, overlay in meetings.items():
        if not isinstance(overlay, dict):
            raise RuntimeError(f"meetings.{name} 必须是对象: {config_path}")
        extra = set(overlay) - _MEETING_OVERLAY_KEYS
        if extra:
            hint = "授权(白名单/token_env/调度)不可经配置文件修改" if extra & _WHITELIST_ONLY_KEYS else "未知字段"
            raise RuntimeError(f"meetings.{name} 含不允许的键 {sorted(extra)} ({hint}): {config_path}")
    return data


MEETING_AUTOMATION_CONFIG = load_meeting_automation_config()


def automation_runtime() -> dict[str, Any]:
    """meeting-automation.yaml 的 ``runtime`` 段 (引擎运行参数)。"""
    return MEETING_AUTOMATION_CONFIG["runtime"]


def automation_resources() -> dict[str, Any]:
    """meeting-automation.yaml 的 ``resources`` 段 (相对 agent 包根的引用路径)。"""
    return MEETING_AUTOMATION_CONFIG["resources"]


#: 引擎常量: 单一来源为 yaml (runtime.analysis.chunk_chars); 此处仅为同值导出, 测试可覆写。
TRANSCRIPT_CHUNK_CHARS = int(MEETING_AUTOMATION_CONFIG["runtime"]["analysis"]["chunk_chars"])
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
    # Versioned SOP skill(s) whose text is injected verbatim into scheduled
    # analysis prompts ("meeting-sop/<name>").  Missing file is a hard error.
    analysis_sop_skills: tuple[str, ...] = ()
    # Ops contacts notified when a run fails (prepare/analysis/notify).
    alert_recipients: tuple[str, ...] = ()
    token_env: str = "TENCENT_MEETING_TOKEN"
    fire: str = "tool"
    tool_name: str = "meeting_pipeline_run"
    tool_args: tuple[tuple[str, str], ...] = ()


#: 代码权威白名单: name/meeting_code/cron/retry/token_env 配对 (授权门) 只能改这里;
#: 投递/SOP/告警口径的当前值作为缺省, 可被 meeting-automation.yaml 的 meetings 覆盖。
_MEETING_JOBS_WHITELIST: tuple[MeetingJob, ...] = (
    MeetingJob(
        name="weekday-alignment",
        meeting_code="57152787045",
        cron="0 12 * * 1,3,5",
        title="周中对齐会",
        recipients=(MAIN_MEETING_GROUP_NAME, "罗霖"),
        retry_crons=("30 17 * * 1,3,5",),
        summary_recipients=(MAIN_MEETING_GROUP_NAME,),
        analysis_sop_skills=("meeting-sop/weekday-alignment",),
        alert_recipients=("张浩", "王金旺"),
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
        analysis_sop_skills=("meeting-sop/weekday-alignment",),
        alert_recipients=("张浩", "王金旺"),
        token_env="TENCENT_MEETING_TOKEN_42654699903",
        tool_args=(("meeting_name", "weekday-alignment-1100"), ("meeting_code", "42654699903")),
    ),
)


def _meeting_jobs_with_overlay() -> tuple[MeetingJob, ...]:
    """白名单 (代码) + yaml meetings 覆盖 (仅允许的五个口径键) 合并出运行时会议列表。

    yaml 里出现的会议名必须全部命中代码白名单 (授权不可经配置文件扩展); 白名单中的
    会议也必须有 yaml 条目 (防止误删后口径悄然回退代码缺省)。
    """
    meetings: dict[str, Any] = MEETING_AUTOMATION_CONFIG["meetings"]
    whitelist_names = {job.name for job in _MEETING_JOBS_WHITELIST}
    extra = set(meetings) - whitelist_names
    if extra:
        raise RuntimeError(f"meeting-automation.yaml 含白名单外会议, 授权不可经配置文件扩展: {sorted(extra)}")
    merged: list[MeetingJob] = []
    for job in _MEETING_JOBS_WHITELIST:
        overlay = meetings.get(job.name)
        if overlay is None:
            raise RuntimeError(f"代码白名单会议 {job.name} 缺少 meeting-automation.yaml 的 meetings.{job.name} 条目")
        kwargs: dict[str, Any] = {}
        for key, value in overlay.items():
            kwargs[key] = tuple(value) if isinstance(value, list) else value
        merged.append(replace(job, **kwargs))
    return tuple(merged)


MEETING_JOBS: tuple[MeetingJob, ...] = _meeting_jobs_with_overlay()


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


_PATH_LOCKS: dict[str, anyio.Lock] = {}
_PATH_LOCKS_GUARD = anyio.Lock()


async def path_lock(path: str | Path) -> anyio.Lock:
    """进程内按路径去重的互斥锁 (读-改-写同一产物文件时使用)。

    跨进程仍依赖「同一 Gateway 只 watch 一份」的单实例约定 (与 notify 的回执锁
    同思路); 锁只防同进程内定时触发与手动重跑并发。
    """
    key = str(path)
    async with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = anyio.Lock()
            _PATH_LOCKS[key] = lock
        return lock


async def atomic_write_text(path: str | Path, text: str) -> None:
    """原子写文本文件: 同目录临时文件 + ``os.replace``, 读方永远不会看到半截内容。

    写失败时清理临时文件并把异常原样抛出 (不静默)。
    """
    target = Path(path)
    await anyio.Path(str(target.parent)).mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        await anyio.Path(str(tmp)).write_text(text, encoding="utf-8")
        await anyio.to_thread.run_sync(os.replace, str(tmp), str(target))  # ty: ignore
    finally:
        if tmp.exists():
            with suppress(OSError):
                tmp.unlink()


__all__ = [
    "MAIN_MEETING_GROUP_NAME",
    "MEETING_AUTOMATION_CONFIG_PATH",
    "MEETING_JOBS",
    "MeetingJob",
    "async_meeting_store_root",
    "atomic_write_text",
    "automation_resources",
    "automation_runtime",
    "chunk_text",
    "extract_latest_transcript_record",
    "extract_paragraph_ids",
    "extract_paragraph_items",
    "json_text",
    "load_meeting_automation_config",
    "meeting_artifact_root",
    "meeting_credential_env",
    "meeting_job_for",
    "meeting_schedule_files",
    "meeting_store_root",
    "path_lock",
    "read_meeting_manifest",
    "render_transcript_paragraphs",
    "should_process_recording",
]
