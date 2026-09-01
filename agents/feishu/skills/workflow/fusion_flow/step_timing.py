"""Workflow-local timing records and atomic sidecar persistence."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

import anyio
from loguru import logger

from ._atomic_io import atomic_write_text

type TimingStatus = Literal["ok", "error", "cancelled"]
type ExecutorKind = Literal["Agent", "Program"]
type RunTimingStatus = Literal["running", "completed", "failed", "cancelled"]

_REPORT_VERSION = 1
_LEGACY_PARTIAL_FILENAME = ".step-timings.partial.json"
_FINAL_FILENAME = "step-timings.json"


@dataclass(frozen=True, slots=True)
class StepTimingMetadata:
    """Static display metadata for one timed Agent or Program Step."""

    step_name: str
    executor_id: str
    executor_kind: ExecutorKind

    def __post_init__(self) -> None:
        _require_non_empty(self.step_name, "step_name")
        _require_non_empty(self.executor_id, "executor_id")
        if self.executor_kind not in {"Agent", "Program"}:
            raise ValueError("executor_kind must be Agent or Program")


@dataclass(frozen=True, slots=True)
class AttemptTiming:
    """One resource-admitted dispatcher attempt."""

    attempt: int
    started_at: str
    finished_at: str
    duration_ms: float
    status: TimingStatus
    error_type: str | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        _validate_interval(self.started_at, self.finished_at, self.duration_ms)
        _validate_terminal(self.status, self.error_type)

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_type": self.error_type,
        }


@dataclass(frozen=True, slots=True)
class IterationTiming:
    """One terminal foreach iteration and all attempts it consumed."""

    iteration_index: int
    started_at: str
    finished_at: str
    duration_ms: float
    status: TimingStatus
    attempts: tuple[AttemptTiming, ...]
    error_type: str | None = None

    def __post_init__(self) -> None:
        if type(self.iteration_index) is not int or self.iteration_index < 0:
            raise ValueError("iteration_index must be a non-negative integer")
        _validate_interval(self.started_at, self.finished_at, self.duration_ms)
        _validate_terminal(self.status, self.error_type)
        attempts = tuple(self.attempts)
        if not all(isinstance(attempt, AttemptTiming) for attempt in attempts):
            raise ValueError("attempts must contain only AttemptTiming records")
        if len({attempt.attempt for attempt in attempts}) != len(attempts):
            raise ValueError("attempts must not contain duplicate attempt numbers")
        object.__setattr__(self, "attempts", tuple(sorted(attempts, key=lambda item: item.attempt)))

    def to_dict(self) -> dict[str, object]:
        return {
            "iteration_index": self.iteration_index,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_type": self.error_type,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


@dataclass(frozen=True, slots=True)
class StepTiming:
    """One terminal logical workflow Step."""

    step_id: str
    step_name: str
    executor_id: str
    executor_kind: ExecutorKind
    foreach: bool
    started_at: str
    finished_at: str
    duration_ms: float
    status: TimingStatus
    attempts: tuple[AttemptTiming, ...] = ()
    iterations: tuple[IterationTiming, ...] = ()
    error_type: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.step_id, "step_id")
        metadata = StepTimingMetadata(
            step_name=self.step_name,
            executor_id=self.executor_id,
            executor_kind=self.executor_kind,
        )
        object.__setattr__(self, "step_name", metadata.step_name)
        if type(self.foreach) is not bool:
            raise ValueError("foreach must be a bool")
        _validate_interval(self.started_at, self.finished_at, self.duration_ms)
        _validate_terminal(self.status, self.error_type)
        attempts = tuple(self.attempts)
        iterations = tuple(self.iterations)
        if not all(isinstance(attempt, AttemptTiming) for attempt in attempts):
            raise ValueError("attempts must contain only AttemptTiming records")
        if not all(isinstance(iteration, IterationTiming) for iteration in iterations):
            raise ValueError("iterations must contain only IterationTiming records")
        if self.foreach and attempts:
            raise ValueError("foreach Step timings must keep attempts on iterations")
        if not self.foreach and iterations:
            raise ValueError("non-foreach Step timings must not contain iterations")
        if len({attempt.attempt for attempt in attempts}) != len(attempts):
            raise ValueError("attempts must not contain duplicate attempt numbers")
        if len({iteration.iteration_index for iteration in iterations}) != len(iterations):
            raise ValueError("iterations must not contain duplicate iteration indexes")
        object.__setattr__(self, "attempts", tuple(sorted(attempts, key=lambda item: item.attempt)))
        object.__setattr__(
            self,
            "iterations",
            tuple(sorted(iterations, key=lambda item: item.iteration_index)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "step_name": self.step_name,
            "executor_id": self.executor_id,
            "executor_kind": self.executor_kind,
            "foreach": self.foreach,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_type": self.error_type,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "iterations": [iteration.to_dict() for iteration in self.iterations],
        }


class StepTimingCollector:
    """Keep the latest terminal record for every logical Step."""

    def __init__(self, records: Sequence[StepTiming] = ()) -> None:
        self._records: dict[str, StepTiming] = {}
        for record in records:
            self.record(record)

    def record(self, record: StepTiming) -> None:
        if not isinstance(record, StepTiming):
            raise TypeError("record must be a StepTiming")
        previous = self._records.get(record.step_id)
        if previous is not None and previous.foreach and record.foreach:
            previous_identity = (
                previous.step_name,
                previous.executor_id,
                previous.executor_kind,
            )
            current_identity = (
                record.step_name,
                record.executor_id,
                record.executor_kind,
            )
            if previous_identity != current_identity:
                raise ValueError("foreach timing metadata changed across resume")
            iterations = {item.iteration_index: item for item in previous.iterations}
            iterations.update({item.iteration_index: item for item in record.iterations})
            record = StepTiming(
                step_id=record.step_id,
                step_name=record.step_name,
                executor_id=record.executor_id,
                executor_kind=record.executor_kind,
                foreach=True,
                started_at=min(previous.started_at, record.started_at),
                finished_at=max(previous.finished_at, record.finished_at),
                duration_ms=previous.duration_ms + record.duration_ms,
                status=record.status,
                error_type=record.error_type,
                iterations=tuple(iterations.values()),
            )
        self._records[record.step_id] = record

    def snapshot(self) -> tuple[StepTiming, ...]:
        return tuple(self._records[step_id] for step_id in sorted(self._records))


class StepTimingStore:
    """Persist one workflow run's timing collector without touching checkpoints."""

    def __init__(
        self,
        run_dir: anyio.Path,
        *,
        run_id: str,
        workflow_id: str,
        flow_path: str,
        collector: StepTimingCollector,
    ) -> None:
        self.run_dir = run_dir
        self.run_id = _require_non_empty(run_id, "run_id")
        self.workflow_id = _require_non_empty(workflow_id, "workflow_id")
        self.flow_path = _require_non_empty(flow_path, "flow_path")
        self.collector = collector

    @classmethod
    async def open(
        cls,
        run_dir: anyio.Path,
        *,
        run_id: str,
        workflow_id: str,
        flow_path: str,
    ) -> StepTimingStore:
        await run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / _FINAL_FILENAME
        legacy_path = run_dir / _LEGACY_PARTIAL_FILENAME
        records: tuple[StepTiming, ...] = ()
        source_path: anyio.Path | None = None
        if await report_path.exists():
            source_path = report_path
        elif await legacy_path.exists():
            source_path = legacy_path
        if source_path is not None:
            payload = _load_payload(await source_path.read_text(encoding="utf-8"))
            _require_identity(
                payload,
                run_id=run_id,
                workflow_id=workflow_id,
                flow_path=flow_path,
            )
            records = tuple(_step_from_dict(item) for item in _require_list(payload["steps"], "steps"))
            if len({record.step_id for record in records}) != len(records):
                raise ValueError("steps must not contain duplicate step IDs")
        return cls(
            run_dir,
            run_id=run_id,
            workflow_id=workflow_id,
            flow_path=flow_path,
            collector=StepTimingCollector(records),
        )

    async def persist(self) -> None:
        await _atomic_write_json(
            self.run_dir / _FINAL_FILENAME,
            self._payload(status="running", error_type=None),
        )
        with anyio.CancelScope(shield=True):
            with suppress(FileNotFoundError):
                await (self.run_dir / _LEGACY_PARTIAL_FILENAME).unlink()

    async def finalize(
        self,
        *,
        status: Literal["completed", "failed", "cancelled"],
        error_type: str | None,
    ) -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("final timing status must be completed, failed, or cancelled")
        if status == "completed" and error_type is not None:
            raise ValueError("completed timing report must not have error_type")
        if error_type is not None:
            _require_non_empty(error_type, "error_type")
        await _atomic_write_json(
            self.run_dir / _FINAL_FILENAME,
            self._payload(status=status, error_type=error_type),
        )
        with anyio.CancelScope(shield=True):
            with suppress(FileNotFoundError):
                await (self.run_dir / _LEGACY_PARTIAL_FILENAME).unlink()

    def _payload(
        self,
        *,
        status: RunTimingStatus,
        error_type: str | None,
    ) -> dict[str, object]:
        return {
            "version": _REPORT_VERSION,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "flow_path": self.flow_path,
            "status": status,
            "error_type": error_type,
            "steps": [record.to_dict() for record in self.collector.snapshot()],
        }


class StepTimingReporter:
    """Best-effort adapter that prevents observability failures from affecting a run."""

    def __init__(self, store: StepTimingStore | None) -> None:
        self._store = store

    @classmethod
    async def open(
        cls,
        run_dir: anyio.Path,
        *,
        run_id: str,
        workflow_id: str,
        flow_path: str,
    ) -> StepTimingReporter:
        try:
            store = await StepTimingStore.open(
                run_dir,
                run_id=run_id,
                workflow_id=workflow_id,
                flow_path=flow_path,
            )
        except Exception as error:
            logger.warning(f"Workflow timing sidecar disabled after {type(error).__name__}: {error}")
            store = None
        return cls(store)

    def record(self, record: StepTiming) -> None:
        if self._store is None:
            return
        try:
            self._store.collector.record(record)
        except Exception as error:
            logger.warning(f"Workflow timing record ignored after {type(error).__name__}: {error}")

    async def persist(self) -> None:
        if self._store is None:
            return
        try:
            await self._store.persist()
        except Exception as error:
            logger.warning(f"Workflow timing sidecar write ignored after {type(error).__name__}: {error}")

    async def finalize(
        self,
        *,
        status: Literal["completed", "failed", "cancelled"],
        error_type: str | None,
    ) -> None:
        if self._store is None:
            return
        try:
            await self._store.finalize(
                status=status,
                error_type=error_type,
            )
        except Exception as error:
            logger.warning(f"Workflow timing sidecar finalization ignored after {type(error).__name__}: {error}")


def _require_non_empty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _validate_interval(started_at: str, finished_at: str, duration_ms: float) -> None:
    started = _parse_timestamp(started_at, "started_at")
    finished = _parse_timestamp(finished_at, "finished_at")
    if finished < started:
        raise ValueError("finished_at must not precede started_at")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int | float):
        raise ValueError("duration_ms must be a finite non-negative number")
    if not math.isfinite(duration_ms) or duration_ms < 0:
        raise ValueError("duration_ms must be a finite non-negative number")


def _parse_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{name} must be a UTC ISO 8601 timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise ValueError(f"{name} must be a UTC ISO 8601 timestamp ending in Z") from error
    if parsed.tzinfo != UTC:
        raise ValueError(f"{name} must be a UTC ISO 8601 timestamp ending in Z")
    return parsed


def _validate_terminal(status: object, error_type: object) -> None:
    if status not in {"ok", "error", "cancelled"}:
        raise ValueError("status must be ok, error, or cancelled")
    if status == "ok":
        if error_type is not None:
            raise ValueError("ok timing records must not have error_type")
        return
    _require_non_empty(error_type, "error_type")


async def _atomic_write_json(path: anyio.Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    await atomic_write_text(path, f"{encoded}\n", newline="")


def _load_payload(source: str) -> dict[str, object]:
    try:
        raw = json.loads(source)
    except json.JSONDecodeError as error:
        raise ValueError("invalid step timing sidecar JSON") from error
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ValueError("step timing sidecar must be a JSON object")
    payload = cast(dict[str, object], raw)
    expected = {
        "version",
        "run_id",
        "workflow_id",
        "flow_path",
        "status",
        "error_type",
        "steps",
    }
    if set(payload) != expected:
        raise ValueError("step timing sidecar has unexpected fields")
    if payload["version"] != _REPORT_VERSION:
        raise ValueError("unsupported step timing sidecar version")
    if payload["status"] != "running" or payload["error_type"] is not None:
        raise ValueError("resumable step timing report must have running status")
    return payload


def _require_identity(
    payload: Mapping[str, object],
    *,
    run_id: str,
    workflow_id: str,
    flow_path: str,
) -> None:
    expected = {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "flow_path": flow_path,
    }
    actual = {key: payload[key] for key in expected}
    if actual != expected:
        raise ValueError("step timing sidecar identity does not match the current workflow run")


def _require_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return cast(list[object], value)


def _require_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _require_exact_fields(
    payload: Mapping[str, object],
    expected: set[str],
    name: str,
) -> None:
    if set(payload) != expected:
        raise ValueError(f"{name} has missing or unexpected fields")


def _step_from_dict(value: object) -> StepTiming:
    payload = _require_mapping(value, "step timing")
    _require_exact_fields(
        payload,
        {
            "step_id",
            "step_name",
            "executor_id",
            "executor_kind",
            "foreach",
            "started_at",
            "finished_at",
            "duration_ms",
            "status",
            "error_type",
            "attempts",
            "iterations",
        },
        "step timing",
    )
    attempts = tuple(_attempt_from_dict(item) for item in _require_list(payload.get("attempts"), "attempts"))
    iterations = tuple(_iteration_from_dict(item) for item in _require_list(payload.get("iterations"), "iterations"))
    return StepTiming(
        step_id=cast(str, payload.get("step_id")),
        step_name=cast(str, payload.get("step_name")),
        executor_id=cast(str, payload.get("executor_id")),
        executor_kind=cast(ExecutorKind, payload.get("executor_kind")),
        foreach=cast(bool, payload.get("foreach")),
        started_at=cast(str, payload.get("started_at")),
        finished_at=cast(str, payload.get("finished_at")),
        duration_ms=cast(float, payload.get("duration_ms")),
        status=cast(TimingStatus, payload.get("status")),
        error_type=cast(str | None, payload.get("error_type")),
        attempts=attempts,
        iterations=iterations,
    )


def _iteration_from_dict(value: object) -> IterationTiming:
    payload = _require_mapping(value, "iteration timing")
    _require_exact_fields(
        payload,
        {
            "iteration_index",
            "started_at",
            "finished_at",
            "duration_ms",
            "status",
            "error_type",
            "attempts",
        },
        "iteration timing",
    )
    return IterationTiming(
        iteration_index=cast(int, payload.get("iteration_index")),
        started_at=cast(str, payload.get("started_at")),
        finished_at=cast(str, payload.get("finished_at")),
        duration_ms=cast(float, payload.get("duration_ms")),
        status=cast(TimingStatus, payload.get("status")),
        error_type=cast(str | None, payload.get("error_type")),
        attempts=tuple(_attempt_from_dict(item) for item in _require_list(payload.get("attempts"), "attempts")),
    )


def _attempt_from_dict(value: object) -> AttemptTiming:
    payload = _require_mapping(value, "attempt timing")
    _require_exact_fields(
        payload,
        {
            "attempt",
            "started_at",
            "finished_at",
            "duration_ms",
            "status",
            "error_type",
        },
        "attempt timing",
    )
    return AttemptTiming(
        attempt=cast(int, payload.get("attempt")),
        started_at=cast(str, payload.get("started_at")),
        finished_at=cast(str, payload.get("finished_at")),
        duration_ms=cast(float, payload.get("duration_ms")),
        status=cast(TimingStatus, payload.get("status")),
        error_type=cast(str | None, payload.get("error_type")),
    )
