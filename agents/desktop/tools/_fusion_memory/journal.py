from __future__ import annotations

import json
import math
import os
import shutil
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    span_id: str
    workspace_id: str
    session_id: str
    turn_id: str
    line_no: int
    speaker: Literal["user", "assistant"]
    content: str
    content_hash: str
    timestamp: str | None
    source_uri: str


@dataclass(frozen=True, slots=True)
class ScopeClear:
    clear_id: str
    workspace_id: str
    session_id: str | None
    timestamp: str


@dataclass(frozen=True, slots=True)
class MemoryPromotion:
    promotion_id: str
    workspace_id: str
    source_span_ids: tuple[str, ...]
    kind: str
    salience: float
    timestamp: str


@dataclass(frozen=True, slots=True)
class ReplayReport:
    records: int = 0
    inserted: int = 0
    duplicates: int = 0
    scope_clears: int = 0
    skipped_records: int = 0
    skipped_tail: int = 0


class JournalConflictError(ValueError):
    """An ID already exists with different canonical record bytes."""


def span_to_record(span: EvidenceSpan) -> dict[str, object]:
    return {"record_type": "evidence_span", "schema_version": 1, **asdict(span)}


def canonical_json(record: dict[str, object]) -> bytes:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def _clear_to_record(clear: ScopeClear) -> dict[str, object]:
    return {"record_type": "scope_clear", "schema_version": 1, **asdict(clear)}


def _promotion_to_record(promotion: MemoryPromotion) -> dict[str, object]:
    return {"record_type": "memory_promotion", "schema_version": 1, **asdict(promotion)}


_SPAN_FIELDS = {f.name for f in EvidenceSpan.__dataclass_fields__.values()}
_CLEAR_FIELDS = {f.name for f in ScopeClear.__dataclass_fields__.values()}
_PROMOTION_FIELDS = {f.name for f in MemoryPromotion.__dataclass_fields__.values()}


def _promotion_from_record(record: dict[str, object]) -> MemoryPromotion:
    promotion_id = record["promotion_id"]
    workspace_id = record["workspace_id"]
    source_span_ids = record["source_span_ids"]
    kind = record["kind"]
    salience = record["salience"]
    timestamp = record["timestamp"]
    if not isinstance(promotion_id, str) or not promotion_id:
        raise TypeError("promotion_id must be a non-empty string")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise TypeError("workspace_id must be a non-empty string")
    if not isinstance(kind, str) or not kind:
        raise TypeError("kind must be a non-empty string")
    if not isinstance(timestamp, str) or not timestamp:
        raise TypeError("promotion string fields must be non-empty")
    if (
        not isinstance(source_span_ids, list)
        or not source_span_ids
        or not all(isinstance(item, str) and item for item in source_span_ids)
    ):
        raise TypeError("source_span_ids must be a non-empty string list")
    if not isinstance(salience, (int, float)) or not math.isfinite(salience):
        raise TypeError("salience must be finite")
    span_ids = tuple(item for item in source_span_ids if isinstance(item, str))
    return MemoryPromotion(promotion_id, workspace_id, span_ids, kind, float(salience), timestamp)


class JsonlJournal:
    _process_lock = threading.RLock()

    def __init__(self, path: str | os.PathLike[str], *, fsync: bool = True) -> None:
        self.path = Path(path)
        self.fsync = fsync
        self._lock = self._process_lock
        self._span_records: dict[str, bytes] = {}
        self._active_spans: OrderedDict[str, EvidenceSpan] = OrderedDict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._recover_tail()
            self._refresh_index()

    def _recover_tail(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            if end == 0:
                return
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) == b"\n":
                return
            split_at = -1
            cursor = end
            while cursor > 0:
                size = min(64 * 1024, cursor)
                cursor -= size
                fh.seek(cursor)
                chunk = fh.read(size)
                relative = chunk.rfind(b"\n")
                if relative >= 0:
                    split_at = cursor + relative
                    break
            tail_start = split_at + 1
            fh.seek(tail_start)
            tail = fh.read()
        try:
            record = json.loads(tail.decode("utf-8"))
        except UnicodeDecodeError, json.JSONDecodeError:
            self._quarantine_tail(tail)
            with self.path.open("r+b") as fh:
                fh.truncate(tail_start)
                if self.fsync:
                    fh.flush()
                    os.fsync(fh.fileno())
            return
        if not self._recognized_record(record):
            self._quarantine_tail(tail)
            with self.path.open("r+b") as fh:
                fh.truncate(tail_start)
                if self.fsync:
                    fh.flush()
                    os.fsync(fh.fileno())
            return
        with self.path.open("ab") as fh:
            fh.write(b"\n")
            if self.fsync:
                fh.flush()
                os.fsync(fh.fileno())

    def _quarantine_tail(self, tail: bytes) -> None:
        for _ in range(20):
            name = f"{self.path.name}.partial-{uuid.uuid4().hex[:12]}"
            destination = self.path.with_name(name)
            try:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(tail)
                    if self.fsync:
                        fh.flush()
                        os.fsync(fh.fileno())
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
            return
        raise FileExistsError("could not allocate unique partial journal path")

    @staticmethod
    def _recognized_record(record: object) -> bool:
        if not isinstance(record, dict) or record.get("schema_version") != 1:
            return False
        kind = record.get("record_type")
        if kind == "evidence_span":
            return record.keys() >= _SPAN_FIELDS and record.get("speaker") in {"user", "assistant"}
        if kind == "scope_clear":
            return record.keys() >= _CLEAR_FIELDS
        if kind == "memory_promotion":
            return record.keys() >= _PROMOTION_FIELDS
        return False

    def _refresh_index(self) -> None:
        self._span_records.clear()
        self._active_spans.clear()
        if not self.path.exists():
            return
        for line in self.path.read_bytes().splitlines():
            try:
                record = json.loads(line.decode("utf-8"))
                if not self._recognized_record(record):
                    continue
                if record.get("record_type") == "evidence_span":
                    span = EvidenceSpan(**{key: record[key] for key in _SPAN_FIELDS})
                    canonical = canonical_json(record)
                elif record.get("record_type") == "scope_clear":
                    clear = ScopeClear(**{key: record[key] for key in _CLEAR_FIELDS})
                else:
                    _promotion_from_record(record)
            except UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError, ValueError:
                continue
            if record.get("record_type") == "evidence_span":
                existing = self._span_records.get(span.span_id)
                if existing is not None and existing != canonical:
                    raise JournalConflictError(f"span_id conflict: {span.span_id}")
                self._span_records[span.span_id] = canonical
                self._active_spans[span.span_id] = span
            elif record.get("record_type") == "scope_clear":
                self._apply_clear(clear)

    def _apply_clear(self, clear: ScopeClear) -> None:
        for span_id, span in list(self._active_spans.items()):
            if span.workspace_id == clear.workspace_id and (
                clear.session_id is None or span.session_id == clear.session_id
            ):
                del self._active_spans[span_id]

    def append_spans(self, spans: Iterable[EvidenceSpan]) -> list[EvidenceSpan]:
        batch = list(spans)
        with self._lock:
            self._recover_tail()
            pending: dict[str, tuple[EvidenceSpan, bytes]] = {}
            for span in batch:
                record_bytes = canonical_json(span_to_record(span))
                existing = self._span_records.get(span.span_id)
                if existing is not None and existing != record_bytes:
                    raise JournalConflictError(f"span_id conflict: {span.span_id}")
                prior = pending.get(span.span_id)
                if prior is not None and prior[1] != record_bytes:
                    raise JournalConflictError(f"span_id conflict: {span.span_id}")
                pending.setdefault(span.span_id, (span, record_bytes))
            new = [(item, data) for sid, (item, data) in pending.items() if sid not in self._span_records]
            if new:
                with self.path.open("ab") as fh:
                    for _, data in new:
                        fh.write(data + b"\n")
                    if self.fsync:
                        fh.flush()
                        os.fsync(fh.fileno())
                self._span_records.update((item.span_id, data) for item, data in new)
                self._active_spans.update((item.span_id, item) for item, _ in new)
            return [item for item, _ in new]

    def active_spans(self, spans: Iterable[EvidenceSpan]) -> list[EvidenceSpan]:
        with self._lock:
            return [span for span in spans if span.span_id in self._active_spans]

    def append_scope_clear(self, workspace_id: str, session_id: str | None = None) -> ScopeClear:
        clear = ScopeClear(uuid.uuid4().hex, workspace_id, session_id, datetime.now(UTC).isoformat())
        data = canonical_json(_clear_to_record(clear)) + b"\n"
        with self._lock:
            self._recover_tail()
            with self.path.open("ab") as fh:
                fh.write(data)
                if self.fsync:
                    fh.flush()
                    os.fsync(fh.fileno())
            self._apply_clear(clear)
        return clear

    def append_promotion(
        self, workspace_id: str, source_span_ids: Iterable[str], kind: str, salience: float
    ) -> MemoryPromotion:
        promotion = MemoryPromotion(
            uuid.uuid4().hex,
            workspace_id,
            tuple(source_span_ids),
            kind,
            float(salience),
            datetime.now(UTC).isoformat(),
        )
        data = canonical_json(_promotion_to_record(promotion)) + b"\n"
        with self._lock:
            self._recover_tail()
            with self.path.open("ab") as fh:
                fh.write(data)
                if self.fsync:
                    fh.flush()
                    os.fsync(fh.fileno())
        return promotion

    def replay(
        self,
        on_span: Callable[[EvidenceSpan], object],
        on_clear: Callable[[ScopeClear], object],
        on_promotion: Callable[[MemoryPromotion], object] | None = None,
    ) -> ReplayReport:
        counts = [0, 0, 0, 0, 0, 0]
        if not self.path.exists():
            return ReplayReport()
        data = self.path.read_bytes()
        tail = 0
        if data and not data.endswith(b"\n"):
            tail = 1
            data = data[: data.rfind(b"\n") + 1]
        for line in data.splitlines():
            counts[0] += 1
            try:
                record = json.loads(line.decode("utf-8"))
                if not self._recognized_record(record):
                    raise ValueError
                if record["record_type"] == "evidence_span":
                    span = EvidenceSpan(**{k: record[k] for k in _SPAN_FIELDS})
                elif record["record_type"] == "scope_clear":
                    clear = ScopeClear(**{k: record[k] for k in _CLEAR_FIELDS})
                else:
                    promotion = _promotion_from_record(record)
            except UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError, ValueError:
                counts[4] += 1
                continue
            if record["record_type"] == "evidence_span":
                inserted = on_span(span)
                counts[2 if inserted is False else 1] += 1
            elif record["record_type"] == "scope_clear":
                on_clear(clear)
                counts[3] += 1
            elif on_promotion is not None:
                on_promotion(promotion)
        counts[5] = tail
        return ReplayReport(*counts)

    def iter_active_spans(self) -> Iterator[EvidenceSpan]:
        active: OrderedDict[str, EvidenceSpan] = OrderedDict()

        def add(item: EvidenceSpan) -> None:
            active[item.span_id] = item

        def clear(item: ScopeClear) -> None:
            for sid, value in list(active.items()):
                if value.workspace_id == item.workspace_id and (
                    item.session_id is None or value.session_id == item.session_id
                ):
                    del active[sid]

        self.replay(add, clear)
        return iter(active.values())

    def copy_to(self, destination: str | os.PathLike[str]) -> None:
        with self._lock:
            target = Path(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copyfile(self.path, target)
            else:
                target.touch(mode=0o600)
