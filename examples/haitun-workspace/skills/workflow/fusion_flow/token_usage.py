"""Workflow-local model token usage aggregation and atomic persistence."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

import anyio
from loguru import logger

from ._atomic_io import atomic_write_text

type ExecutorKind = Literal["Agent", "Program", "Human"]
type RunUsageStatus = Literal["running", "completed", "failed", "cancelled"]

_REPORT_VERSION = 1
_FILENAME = "token-usage.json"


@dataclass(frozen=True, slots=True)
class TokenCount:
    """Usage for one or more model calls with explicit completeness."""

    model_calls: int
    input_tokens: int | None
    output_tokens: int | None

    def __post_init__(self) -> None:
        if type(self.model_calls) is not int or self.model_calls < 0:
            raise ValueError("model_calls must be a non-negative integer")
        _validate_optional_count(self.input_tokens, "input_tokens")
        _validate_optional_count(self.output_tokens, "output_tokens")
        if (self.input_tokens is None) != (self.output_tokens is None):
            raise ValueError("input_tokens and output_tokens must both be known or both be null")

    @property
    def complete(self) -> bool:
        return self.input_tokens is not None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    def merged(self, other: TokenCount) -> TokenCount:
        complete = self.complete and other.complete
        return TokenCount(
            model_calls=self.model_calls + other.model_calls,
            input_tokens=cast(int, self.input_tokens) + cast(int, other.input_tokens) if complete else None,
            output_tokens=cast(int, self.output_tokens) + cast(int, other.output_tokens) if complete else None,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class AttemptTokenUsage:
    """Accumulated model usage for one dispatcher attempt."""

    attempt: int
    iteration_index: int | None
    usage: TokenCount

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if self.iteration_index is not None and (type(self.iteration_index) is not int or self.iteration_index < 0):
            raise ValueError("iteration_index must be a non-negative integer or null")
        if not isinstance(self.usage, TokenCount):
            raise TypeError("usage must be a TokenCount")

    def to_dict(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "iteration_index": self.iteration_index,
            **self.usage.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class StepTokenUsage:
    """Usage for one logical workflow step across attempts and iterations."""

    step_id: str
    executor_id: str
    executor_kind: ExecutorKind
    attempts: tuple[AttemptTokenUsage, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.step_id, "step_id")
        _require_non_empty(self.executor_id, "executor_id")
        if self.executor_kind not in {"Agent", "Program", "Human"}:
            raise ValueError("executor_kind must be Agent, Program, or Human")
        attempts = tuple(self.attempts)
        if not all(isinstance(item, AttemptTokenUsage) for item in attempts):
            raise TypeError("attempts must contain only AttemptTokenUsage records")
        identities = {(item.iteration_index, item.attempt) for item in attempts}
        if len(identities) != len(attempts):
            raise ValueError("attempts must not contain duplicate iteration/attempt pairs")
        object.__setattr__(
            self,
            "attempts",
            tuple(sorted(attempts, key=lambda item: (_iteration_sort_key(item.iteration_index), item.attempt))),
        )

    @property
    def usage(self) -> TokenCount:
        return _merge_counts(item.usage for item in self.attempts)

    def to_dict(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "executor_id": self.executor_id,
            "executor_kind": self.executor_kind,
            **self.usage.to_dict(),
            "attempts": [item.to_dict() for item in self.attempts],
        }


class TokenUsageCollector:
    """Merge usage observations by stable step, iteration, and attempt identity."""

    def __init__(self, records: Sequence[StepTokenUsage] = ()) -> None:
        self._records: dict[str, StepTokenUsage] = {}
        for record in records:
            self._records[record.step_id] = record

    def record(
        self,
        *,
        step_id: str,
        executor_id: str,
        executor_kind: ExecutorKind,
        attempt: int,
        iteration_index: int | None,
        usage: TokenCount,
    ) -> None:
        incoming = AttemptTokenUsage(
            attempt=attempt,
            iteration_index=iteration_index,
            usage=usage,
        )
        previous = self._records.get(step_id)
        if previous is None:
            self._records[step_id] = StepTokenUsage(
                step_id=step_id,
                executor_id=executor_id,
                executor_kind=executor_kind,
                attempts=(incoming,),
            )
            return
        if (previous.executor_id, previous.executor_kind) != (executor_id, executor_kind):
            raise ValueError("step token usage metadata changed across observations")
        attempts = {(item.iteration_index, item.attempt): item for item in previous.attempts}
        identity = (iteration_index, attempt)
        if identity in attempts:
            old = attempts[identity]
            incoming = AttemptTokenUsage(
                attempt=attempt,
                iteration_index=iteration_index,
                usage=old.usage.merged(usage),
            )
        attempts[identity] = incoming
        self._records[step_id] = StepTokenUsage(
            step_id=step_id,
            executor_id=executor_id,
            executor_kind=executor_kind,
            attempts=tuple(attempts.values()),
        )

    def snapshot(self) -> tuple[StepTokenUsage, ...]:
        return tuple(self._records[step_id] for step_id in sorted(self._records))

    @property
    def totals(self) -> TokenCount:
        return _merge_counts(record.usage for record in self.snapshot())


class TokenUsageStore:
    """Persist one workflow run's usage without changing workflow outputs."""

    def __init__(
        self,
        run_dir: anyio.Path,
        *,
        run_id: str,
        workflow_id: str,
        flow_path: str,
        collector: TokenUsageCollector,
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
    ) -> TokenUsageStore:
        await run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / _FILENAME
        records: tuple[StepTokenUsage, ...] = ()
        if await path.exists():
            payload = _load_payload(await path.read_text(encoding="utf-8"))
            _require_identity(
                payload,
                run_id=run_id,
                workflow_id=workflow_id,
                flow_path=flow_path,
            )
            records = _parse_steps(payload["steps"])
        return cls(
            run_dir,
            run_id=run_id,
            workflow_id=workflow_id,
            flow_path=flow_path,
            collector=TokenUsageCollector(records),
        )

    async def persist(self) -> None:
        await _atomic_write_json(self.run_dir / _FILENAME, self._payload(status="running", error_type=None))

    async def finalize(
        self,
        *,
        status: Literal["completed", "failed", "cancelled"],
        error_type: str | None,
    ) -> None:
        if status == "completed" and error_type is not None:
            raise ValueError("completed token usage reports cannot have error_type")
        if status != "completed":
            _require_non_empty(error_type, "error_type")
        await _atomic_write_json(self.run_dir / _FILENAME, self._payload(status=status, error_type=error_type))

    def _payload(self, *, status: RunUsageStatus, error_type: str | None) -> dict[str, object]:
        totals = self.collector.totals
        return {
            "version": _REPORT_VERSION,
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "flow_path": self.flow_path,
            "status": status,
            "error_type": error_type,
            "complete": totals.complete,
            "totals": totals.to_dict(),
            "steps": [record.to_dict() for record in self.collector.snapshot()],
        }


class TokenUsageReporter:
    """Best-effort adapter that keeps observability failures out of workflow results."""

    def __init__(self, store: TokenUsageStore | None) -> None:
        self._store = store

    @classmethod
    async def open(
        cls,
        run_dir: anyio.Path,
        *,
        run_id: str,
        workflow_id: str,
        flow_path: str,
    ) -> TokenUsageReporter:
        try:
            store = await TokenUsageStore.open(
                run_dir,
                run_id=run_id,
                workflow_id=workflow_id,
                flow_path=flow_path,
            )
        except Exception as error:
            logger.warning(f"Workflow token usage sidecar disabled after {type(error).__name__}: {error}")
            store = None
        return cls(store)

    def record(
        self,
        *,
        step_id: str,
        executor_id: str,
        executor_kind: ExecutorKind,
        attempt: int,
        iteration_index: int | None,
        model_calls: int,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        if self._store is None:
            return
        try:
            self._store.collector.record(
                step_id=step_id,
                executor_id=executor_id,
                executor_kind=executor_kind,
                attempt=attempt,
                iteration_index=iteration_index,
                usage=TokenCount(
                    model_calls=model_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ),
            )
        except Exception as error:
            logger.warning(f"Workflow token usage record ignored after {type(error).__name__}: {error}")

    async def persist(self) -> None:
        if self._store is None:
            return
        try:
            await self._store.persist()
        except Exception as error:
            logger.warning(f"Workflow token usage sidecar write ignored after {type(error).__name__}: {error}")

    async def finalize(
        self,
        *,
        status: Literal["completed", "failed", "cancelled"],
        error_type: str | None,
    ) -> None:
        if self._store is None:
            return
        try:
            await self._store.finalize(status=status, error_type=error_type)
        except Exception as error:
            logger.warning(f"Workflow token usage finalization ignored after {type(error).__name__}: {error}")


def _merge_counts(counts: Iterable[TokenCount]) -> TokenCount:
    result = TokenCount(model_calls=0, input_tokens=0, output_tokens=0)
    for count in counts:
        result = result.merged(count)
    return result


def _validate_optional_count(value: object, name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or null")


def _require_non_empty(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _iteration_sort_key(value: int | None) -> int:
    return -1 if value is None else value


async def _atomic_write_json(path: anyio.Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
    await atomic_write_text(path, f"{encoded}\n", newline="")


def _load_payload(source: str) -> dict[str, object]:
    try:
        raw = json.loads(source)
    except json.JSONDecodeError as error:
        raise ValueError("invalid token usage sidecar JSON") from error
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise ValueError("token usage sidecar must be a JSON object")
    payload = cast(dict[str, object], raw)
    expected = {
        "version",
        "run_id",
        "workflow_id",
        "flow_path",
        "status",
        "error_type",
        "complete",
        "totals",
        "steps",
    }
    if set(payload) != expected or payload["version"] != _REPORT_VERSION:
        raise ValueError("unsupported token usage sidecar schema")
    if payload["status"] != "running" or payload["error_type"] is not None:
        raise ValueError("resumable token usage report must have running status")
    return payload


def _require_identity(
    payload: Mapping[str, object],
    *,
    run_id: str,
    workflow_id: str,
    flow_path: str,
) -> None:
    if (payload.get("run_id"), payload.get("workflow_id"), payload.get("flow_path")) != (
        run_id,
        workflow_id,
        flow_path,
    ):
        raise ValueError("token usage sidecar identity does not match the workflow run")


def _parse_steps(value: object) -> tuple[StepTokenUsage, ...]:
    if not isinstance(value, list):
        raise ValueError("token usage steps must be an array")
    records: list[StepTokenUsage] = []
    for raw_step in value:
        if not isinstance(raw_step, dict):
            raise ValueError("token usage step must be an object")
        raw_attempts = raw_step.get("attempts")
        if not isinstance(raw_attempts, list):
            raise ValueError("token usage attempts must be an array")
        attempts = tuple(_parse_attempt(item) for item in raw_attempts)
        records.append(
            StepTokenUsage(
                step_id=cast(str, raw_step.get("step_id")),
                executor_id=cast(str, raw_step.get("executor_id")),
                executor_kind=cast(ExecutorKind, raw_step.get("executor_kind")),
                attempts=attempts,
            )
        )
    return tuple(records)


def _parse_attempt(value: object) -> AttemptTokenUsage:
    if not isinstance(value, dict):
        raise ValueError("token usage attempt must be an object")
    return AttemptTokenUsage(
        attempt=cast(int, value.get("attempt")),
        iteration_index=cast(int | None, value.get("iteration_index")),
        usage=TokenCount(
            model_calls=cast(int, value.get("model_calls")),
            input_tokens=cast(int | None, value.get("input_tokens")),
            output_tokens=cast(int | None, value.get("output_tokens")),
        ),
    )
