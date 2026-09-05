from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import anyio
from anyio import to_thread

from psi_agent._appdata import appdata_state_latest_path, resolve_history_read_path

from .journal import EvidenceSpan
from .store import IngestCheckpoint, MemoryStore

_TRANSFER_MARKER = re.compile(r"\[\s*(?:SEND|RECV)\s*:\s*[^\]\n]*?\]", re.IGNORECASE)
_MAX_ROUND_MESSAGES = frozenset({"[max tool rounds reached]"})


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    normalized: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class HistorySource:
    session_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class CommittedHistoryProvenance:
    path: str
    appdata_root: str
    user_line: int
    assistant_line: int


@dataclass(frozen=True, slots=True)
class IngestReport:
    files_scanned: int = 0
    completed_turns: int = 0
    spans_appended: int = 0
    spans_indexed: int = 0
    rescanned_files: int = 0


def normalize_workspace(path: str | Path) -> str:
    value = os.path.realpath(os.path.abspath(os.fspath(path)))
    return os.path.normcase(value) if sys.platform == "win32" else value


def workspace_scope(path: str | Path) -> WorkspaceScope:
    normalized = normalize_workspace(path)
    return WorkspaceScope(normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest())


def _visible_content(row: Mapping[str, object]) -> str:
    value = row.get("content", "")
    if not isinstance(value, str):
        return ""
    return _TRANSFER_MARKER.sub("", value)


def _kind(row: Mapping[str, object]) -> str:
    if "kind" in row:
        return "chat" if row.get("kind") == "chat" else ""
    if "chat_type" in row:
        return "chat" if row.get("chat_type") == "common" else ""
    return "chat"


def _timestamp(row: dict[str, object]) -> str | None:
    value = row.get("timestamp", row.get("created_at"))
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _turn_id(
    session_id: str,
    user_line: int,
    assistant_line: int,
    user_content_hash: str,
    assistant_content_hash: str,
) -> str:
    seed = f"{session_id}|{user_line}|{assistant_line}|{user_content_hash}|{assistant_content_hash}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _span_id(scope: WorkspaceScope, session_id: str, line_no: int, speaker: str, content: str) -> tuple[str, str]:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    seed = f"{scope.workspace_id}|{session_id}|{line_no}|{speaker}|{content_hash}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest(), content_hash


def _read_rows(path: Path, start_line: int = 1) -> Iterator[tuple[int, dict[str, object]]]:
    with path.open(encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            if line_no < start_line:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError, UnicodeDecodeError:
                continue
            if isinstance(value, dict):
                yield line_no, value


def _read_rows_reverse(path: Path, start_line: int, end_line: int) -> Iterator[tuple[int, dict[str, object]]]:
    """Yield a committed tail range without scanning or materializing its prefix."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if not size:
            return
        handle.seek(-1, os.SEEK_END)
        trailing_newline = handle.read(1) == b"\n"
        position = size
        line_no = end_line
        buffer = b""
        skip_trailing_empty = trailing_newline
        while position:
            chunk_size = min(64 * 1024, position)
            position -= chunk_size
            handle.seek(position)
            buffer = handle.read(chunk_size) + buffer
            parts = buffer.split(b"\n")
            buffer = parts[0]
            for raw in reversed(parts[1:]):
                if skip_trailing_empty and raw == b"":
                    skip_trailing_empty = False
                    continue
                skip_trailing_empty = False
                current_line = line_no
                line_no -= 1
                if current_line < start_line:
                    return
                try:
                    value = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError, UnicodeDecodeError:
                    continue
                if isinstance(value, dict):
                    yield current_line, value
        if line_no >= start_line and buffer:
            try:
                value = json.loads(buffer.decode("utf-8"))
            except json.JSONDecodeError, UnicodeDecodeError:
                return
            if isinstance(value, dict):
                yield line_no, value


def _span_pair(
    scope: WorkspaceScope,
    source: HistorySource,
    user_line: int,
    user_row: dict[str, object],
    user_text: str,
    assistant_line: int,
    assistant_row: dict[str, object],
    assistant_text: str,
) -> tuple[EvidenceSpan, EvidenceSpan]:
    path = Path(source.path)
    user_id, user_hash = _span_id(scope, source.session_id, user_line, "user", user_text)
    assistant_id, assistant_hash = _span_id(scope, source.session_id, assistant_line, "assistant", assistant_text)
    turn_id = _turn_id(source.session_id, user_line, assistant_line, user_hash, assistant_hash)
    return (
        EvidenceSpan(
            span_id=user_id,
            workspace_id=scope.workspace_id,
            session_id=source.session_id,
            turn_id=turn_id,
            line_no=user_line,
            speaker="user",
            content=user_text,
            content_hash=user_hash,
            timestamp=_timestamp(user_row),
            source_uri=f"history://{path.as_posix()}#L{user_line}",
        ),
        EvidenceSpan(
            span_id=assistant_id,
            workspace_id=scope.workspace_id,
            session_id=source.session_id,
            turn_id=turn_id,
            line_no=assistant_line,
            speaker="assistant",
            content=assistant_text,
            content_hash=assistant_hash,
            timestamp=_timestamp(assistant_row),
            source_uri=f"history://{path.as_posix()}#L{assistant_line}",
        ),
    )


def _eligible_assistant(row: dict[str, object]) -> str | None:
    visible = _visible_content(row)
    tool_calls = row.get("tool_calls") or row.get("tools")
    if (
        row.get("role") != "assistant"
        or _kind(row) != "chat"
        or not visible.strip()
        or tool_calls
        or visible.strip() == "HEARTBEAT_OK"
        or visible.strip().casefold() in _MAX_ROUND_MESSAGES
    ):
        return None
    return visible


def parse_latest_completed_turn(
    scope: WorkspaceScope,
    source: HistorySource,
    start_line: int = 1,
    end_line: int | None = None,
) -> list[EvidenceSpan]:
    if end_line is None:
        latest: list[EvidenceSpan] = []
        pending: tuple[int, dict[str, object], str] | None = None
        for line_no, row in _read_rows(Path(source.path), start_line):
            if row.get("role") == "user":
                pending = None
                user_text = _visible_content(row)
                if _kind(row) == "chat" and user_text.strip():
                    pending = (line_no, row, user_text)
                continue
            assistant_text = _eligible_assistant(row)
            if assistant_text is None or pending is None:
                continue
            user_line, user_row, user_text = pending
            latest = list(_span_pair(scope, source, user_line, user_row, user_text, line_no, row, assistant_text))
            pending = None
        return latest

    candidate: tuple[int, dict[str, object], str] | None = None
    for line_no, row in _read_rows_reverse(Path(source.path), start_line, end_line):
        assistant_text = _eligible_assistant(row)
        if assistant_text is not None:
            candidate = (line_no, row, assistant_text)
            continue
        if row.get("role") != "user":
            continue
        user_text = _visible_content(row)
        if _kind(row) != "chat" or not user_text.strip():
            candidate = None
            continue
        if candidate is None:
            continue
        user_line, user_row, assistant_text = line_no, row, candidate[2]
        return list(
            _span_pair(scope, source, user_line, user_row, user_text, candidate[0], candidate[1], assistant_text)
        )
    return []


def parse_completed_turns(
    scope: WorkspaceScope,
    source: HistorySource,
    start_line: int = 1,
    max_turns: int | None = None,
) -> list[EvidenceSpan]:
    if max_turns is not None and max_turns <= 0:
        return []
    result: list[EvidenceSpan] = []
    pending: tuple[int, dict[str, object], str] | None = None
    path = Path(source.path)
    completed = 0
    for line_no, row in _read_rows(path, start_line):
        role = row.get("role")
        kind = _kind(row)
        if role == "user":
            pending = None
            visible = _visible_content(row)
            if kind == "chat" and visible.strip():
                pending = (line_no, row, visible)
            continue
        if role != "assistant":
            continue
        visible = _visible_content(row)
        tool_calls = row.get("tool_calls") or row.get("tools")
        if kind != "chat" or not visible.strip() or tool_calls:
            continue
        if visible.strip() == "HEARTBEAT_OK" or visible.strip().casefold() in _MAX_ROUND_MESSAGES:
            continue
        if pending is None:
            continue
        user_line, user_row, user_text = pending
        result.extend(_span_pair(scope, source, user_line, user_row, user_text, line_no, row, visible))
        pending = None
        completed += 1
        if max_turns is not None and completed >= max_turns:
            break
    return result


async def _owned_history_source(
    scope: WorkspaceScope,
    session_id: str,
    path: Path,
    appdata_root: Path,
) -> tuple[str, HistorySource] | None:
    if re.fullmatch(r"[a-zA-Z0-9_-]+", session_id) is None:
        return None
    allowed_roots = (Path(appdata_root) / "histories", Path(scope.normalized) / "histories")
    key, expected = await to_thread.run_sync(_history_ownership, path, allowed_roots, session_id)
    if key not in expected or not await anyio.Path(path).is_file():
        return None
    return key, HistorySource(session_id, path)


async def _appdata_history_is_owned(appdata_root: Path, scope: WorkspaceScope, session_id: str) -> bool:
    """Require the Gateway snapshot to bind shared AppData history to a workspace."""
    state_path = appdata_state_latest_path(str(appdata_root))
    try:
        raw = await state_path.read_text(encoding="utf-8")
        snapshot = json.loads(raw)
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return False
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("sessions"), list):
        return False
    owners = [item for item in snapshot["sessions"] if isinstance(item, dict) and item.get("id") == session_id]
    return (
        len(owners) == 1
        and isinstance(owners[0].get("workspace"), str)
        and normalize_workspace(owners[0]["workspace"]) == scope.normalized
    )


def _history_ownership(path: Path, allowed_roots: tuple[Path, Path], session_id: str) -> tuple[str, set[str]]:
    key = os.path.normcase(os.path.realpath(os.fspath(path)))
    expected = {
        normalized
        for root in allowed_roots
        if (normalized := os.path.normcase(os.path.abspath(os.fspath(root / f"{session_id}.jsonl"))))
        == os.path.normcase(os.path.realpath(os.fspath(root / f"{session_id}.jsonl")))
    }
    return key, expected


async def discover_current_history(
    scope: WorkspaceScope,
    current_session_id: str,
    appdata_root: Path,
    committed_path: str = "",
) -> HistorySource | None:
    if re.fullmatch(r"[a-zA-Z0-9_-]+", current_session_id) is None:
        return None
    if committed_path:
        owned = await _owned_history_source(scope, current_session_id, Path(committed_path), appdata_root)
        return owned[1] if owned else None
    current_path = Path(
        str(
            await resolve_history_read_path(
                appdata_root=str(appdata_root), workspace=scope.normalized, session_id=current_session_id
            )
        )
    )
    appdata_path = Path(appdata_root) / "histories" / f"{current_session_id}.jsonl"
    if normalize_workspace(current_path) == normalize_workspace(appdata_path) and not await _appdata_history_is_owned(
        appdata_root, scope, current_session_id
    ):
        return None
    owned = await _owned_history_source(scope, current_session_id, current_path, appdata_root)
    return owned[1] if owned else None


def _prefix_hash(path: Path, line_count: int) -> str:
    """Fingerprint only raw chat evidence; mutable context rows are irrelevant."""
    digest = hashlib.sha256()
    for line_no, row in _read_rows(path):
        if line_no > line_count:
            break
        role = row.get("role")
        if role == "user":
            content = _visible_content(row)
            if _kind(row) != "chat" or not content.strip():
                continue
        elif role == "assistant":
            content = _eligible_assistant(row)
            if content is None:
                continue
        else:
            continue
        digest.update(json.dumps([line_no, role, content], ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


def committed_history_provenance(message: Mapping[str, object]) -> CommittedHistoryProvenance | None:
    provenance = message.get("_psi_history_provenance")
    if not isinstance(provenance, dict):
        return None
    path = provenance.get("path")
    appdata_root = provenance.get("appdata_root")
    user_line = provenance.get("user_line")
    assistant_line = provenance.get("assistant_line")
    if (
        not isinstance(path, str)
        or not path.strip()
        or not isinstance(appdata_root, str)
        or not appdata_root.strip()
        or not isinstance(user_line, int)
        or isinstance(user_line, bool)
        or not isinstance(assistant_line, int)
        or isinstance(assistant_line, bool)
        or user_line < 1
        or assistant_line <= user_line
    ):
        return None
    return CommittedHistoryProvenance(path, appdata_root, user_line, assistant_line)


def ingest_confirmed_turn(
    store: MemoryStore,
    scope: WorkspaceScope,
    source: HistorySource,
    user_message: Mapping[str, object],
    assistant_message: Mapping[str, object],
) -> tuple[IngestReport, list[EvidenceSpan]]:
    path = Path(source.path)
    if not path.is_file():
        return IngestReport(), []
    if (
        user_message.get("role") != "user"
        or assistant_message.get("role") != "assistant"
        or _kind(user_message) != "chat"
        or _kind(assistant_message) != "chat"
    ):
        return IngestReport(files_scanned=1), []
    user_text = _visible_content(user_message)
    assistant_text = _visible_content(assistant_message)
    if (
        not user_text.strip()
        or not assistant_text.strip()
        or assistant_message.get("tool_calls")
        or assistant_message.get("tools")
        or assistant_text.strip() == "HEARTBEAT_OK"
        or assistant_text.strip().casefold() in _MAX_ROUND_MESSAGES
    ):
        return IngestReport(files_scanned=1), []

    stat = path.stat()
    checkpoint = store.read_checkpoint(scope.workspace_id, str(path.resolve()))
    committed = committed_history_provenance(user_message)
    if committed is not None:
        if normalize_workspace(committed.path) != normalize_workspace(path):
            return IngestReport(files_scanned=1), []
        if (
            checkpoint
            and checkpoint.session_id == source.session_id
            and committed.assistant_line <= checkpoint.confirmed_line_count
        ):
            return IngestReport(files_scanned=1), []
        rescanned = 0
        parsed = parse_latest_completed_turn(scope, source, committed.user_line, committed.assistant_line)
    else:
        checkpoint_valid = bool(
            checkpoint and _prefix_hash(path, checkpoint.confirmed_line_count) == checkpoint.prefix_hash
        )
        rescanned = int(bool(checkpoint and not checkpoint_valid))
        start_line = checkpoint.confirmed_line_count + 1 if checkpoint_valid and checkpoint else 1
        parsed = parse_latest_completed_turn(scope, source, start_line)
    selected: list[EvidenceSpan] = []
    terminal_turn = parsed[-2:]
    lines_match = committed is None or (
        len(terminal_turn) == 2
        and terminal_turn[0].line_no == committed.user_line
        and terminal_turn[1].line_no == committed.assistant_line
    )
    if (
        len(terminal_turn) == 2
        and lines_match
        and terminal_turn[0].content == user_text
        and terminal_turn[1].content == assistant_text
    ):
        selected = terminal_turn
    if not selected:
        return IngestReport(files_scanned=1, rescanned_files=rescanned), []

    new = store.index_spans(selected)
    confirmed_line = selected[-1].line_no
    prior = checkpoint if checkpoint and checkpoint.session_id == source.session_id else None
    prefix_hash = (
        f"committed:{selected[0].content_hash}:{selected[1].content_hash}"
        if committed
        else _prefix_hash(path, confirmed_line)
    )
    store.write_checkpoint(
        IngestCheckpoint(
            workspace_id=scope.workspace_id,
            history_path=str(path.resolve()),
            session_id=source.session_id,
            confirmed_line_count=confirmed_line,
            prefix_hash=prefix_hash,
            file_size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            extraction_line=prior.extraction_line if prior else 0,
            embedding_line=prior.embedding_line if prior else 0,
            card_line=prior.card_line if prior else 0,
            updated_at="",
        )
    )
    return IngestReport(1, 1, len(new), len(selected), rescanned), selected
