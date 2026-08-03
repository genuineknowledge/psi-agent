"""Durable state for FusionFlow runs that may wait for human input.

This module is intentionally workspace-private.  It persists the small amount
of adapter state needed to end a Haitun turn at a Human step and resume the
same workflow from the next user message.  It does not implement an approval
UI or an input channel.
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import secrets
import threading
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import ExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from os import PathLike
from typing import BinaryIO, Literal, Protocol, cast

import anyio
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from loguru import logger

from .workflow_execution import (
    ExecutionCheckpoint,
    ForeachIterationCheckpoint,
    ResourceCapacity,
)

STATE_VERSION = 3
type RunStatus = Literal[
    "running",
    "waiting_for_human",
    "completed",
    "failed",
    "cancelled",
]

_OPAQUE_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_RUN_KEYS = frozenset(
    {
        "version",
        "run_id",
        "status",
        "flow_path",
        "definition_digest",
        "inputs",
        "resource_capacities",
        "checkpoint",
        "prepared_request",
        "human_responses",
        "outputs",
        "error",
    }
)
_CHECKPOINT_KEYS = frozenset(
    {
        "workflow_id",
        "plan_digest",
        "values",
        "completed_step_ids",
        "completed_selection_ids",
        "foreach_iterations",
    }
)
_REQUEST_KEYS = frozenset(
    {
        "request_id",
        "step_id",
        "question",
        "output_artifact_ids",
        "options",
        "recommended",
        "default",
    }
)
_LOCKING_MODULE = __import__("msvcrt" if os.name == "nt" else "fcntl")
_PROCESS_LOCK_RESERVATIONS: set[str] = set()
_PROCESS_LOCK_RESERVATIONS_GUARD = threading.Lock()


class _WindowsLockingModule(Protocol):
    LK_NBLCK: int

    def locking(self, fd: int, mode: int, nbytes: int, /) -> None: ...


class _PosixLockingModule(Protocol):
    LOCK_EX: int
    LOCK_NB: int

    def flock(self, fd: int, operation: int, /) -> None: ...


class JobStoreError(RuntimeError):
    """Base class for persisted FusionFlow job errors."""


class InvalidRunStateError(JobStoreError):
    """A persisted run document does not satisfy the current schema."""


class RunAlreadyActiveError(JobStoreError):
    """Another caller currently owns the run's advisory-lock lease."""


def new_opaque_id() -> str:
    """Return an unguessable identifier safe to use as a state filename."""

    return secrets.token_hex(16)


@dataclass(frozen=True, slots=True)
class HumanRequestSpec:
    """The prepared arguments for Haitun's existing ``clarify`` tool."""

    request_id: str
    step_id: str
    question: str
    output_artifact_ids: tuple[str, ...]
    options: tuple[str, ...] = ()
    recommended: int = 0
    default: str = ""

    def __post_init__(self) -> None:
        """Normalize immutable sequences and reject invalid clarify arguments."""

        object.__setattr__(self, "output_artifact_ids", tuple(self.output_artifact_ids))
        object.__setattr__(self, "options", tuple(self.options))
        _validate_request(self, error_type=ValueError)

    @classmethod
    def create(
        cls,
        *,
        step_id: str,
        question: str,
        output_artifact_ids: Sequence[str],
        options: Sequence[str] = (),
        recommended: int = 0,
        default: str = "",
    ) -> HumanRequestSpec:
        """Build a request with a fresh opaque request ID."""

        return cls(
            request_id=new_opaque_id(),
            step_id=step_id,
            question=question,
            output_artifact_ids=tuple(output_artifact_ids),
            options=tuple(options),
            recommended=recommended,
            default=default,
        )


@dataclass(frozen=True, slots=True)
class HumanWorkflowRun:
    """One versioned, JSON-serializable FusionFlow run record."""

    run_id: str
    status: RunStatus
    flow_path: str
    definition_digest: str
    inputs: dict[str, object]
    resource_capacities: dict[str, ResourceCapacity]
    checkpoint: ExecutionCheckpoint | None = None
    prepared_request: HumanRequestSpec | None = None
    human_responses: dict[str, object] = field(default_factory=dict)
    outputs: dict[str, object] | None = None
    error: str | None = None
    version: int = STATE_VERSION

    def __post_init__(self) -> None:
        """Defensively copy mutable payloads and enforce run invariants."""

        object.__setattr__(
            self,
            "inputs",
            _copy_json_mapping(self.inputs, context="inputs", error_type=ValueError),
        )
        object.__setattr__(
            self,
            "resource_capacities",
            _normalize_resource_capacities(
                self.resource_capacities,
                error_type=ValueError,
            ),
        )
        object.__setattr__(
            self,
            "checkpoint",
            _copy_checkpoint(self.checkpoint, error_type=ValueError),
        )
        object.__setattr__(
            self,
            "human_responses",
            _copy_json_mapping(
                self.human_responses,
                context="human_responses",
                error_type=ValueError,
            ),
        )
        if self.outputs is not None:
            object.__setattr__(
                self,
                "outputs",
                _copy_json_mapping(
                    self.outputs,
                    context="outputs",
                    error_type=ValueError,
                ),
            )
        _validate_run(self, error_type=ValueError)


class RunLease:
    """Exclusive access to one run while its advisory lock is held."""

    __slots__ = ("_active", "_store", "run_id")

    def __init__(self, store: JobStore, run_id: str) -> None:
        self._store = store
        self.run_id = run_id
        self._active = True

    async def load(self) -> HumanWorkflowRun:
        """Load the leased run's latest atomically published state."""

        self._require_active()
        return await self._store.load(self.run_id)

    async def save(self, run: HumanWorkflowRun) -> None:
        """Atomically save state for this lease's run."""

        self._require_active()
        if run.run_id != self.run_id:
            raise ValueError(f"lease for run {self.run_id!r} cannot save run {run.run_id!r}")
        await self._store.save(run)

    def _require_active(self) -> None:
        if not self._active:
            raise JobStoreError(f"lease for run {self.run_id!r} is no longer active")

    def _release(self) -> None:
        self._active = False


class _RunLock:
    """An OS-released advisory lock backed by one open file handle."""

    __slots__ = ("_file", "_reservation_key")

    def __init__(self, lock_file: BinaryIO, reservation_key: str) -> None:
        self._file: BinaryIO | None = lock_file
        self._reservation_key = reservation_key

    @classmethod
    async def try_acquire(cls, path: anyio.Path) -> _RunLock | None:
        """Try once to lock ``path`` without blocking."""

        with anyio.CancelScope(shield=True):
            reservation_key = await run_sync_in_worker_thread(
                _try_reserve_process_lock,
                str(path),
            )
            if reservation_key is None:
                return None
            retained = False
            try:
                lock_file = await run_sync_in_worker_thread(
                    _try_open_locked_file,
                    str(path),
                )
                if lock_file is None:
                    return None
                run_lock = cls(lock_file, reservation_key)
                retained = True
                return run_lock
            finally:
                if not retained:
                    await run_sync_in_worker_thread(
                        _release_process_lock,
                        reservation_key,
                    )

    async def release(self) -> None:
        """Close the handle, releasing the lock even during cancellation."""

        lock_file = self._file
        if lock_file is None:
            return
        self._file = None
        with anyio.CancelScope(shield=True):
            await run_sync_in_worker_thread(
                _close_locked_file,
                lock_file,
                self._reservation_key,
            )


class JobStore:
    """Versioned JSON store with per-run cross-process exclusion."""

    def __init__(self, root: str | PathLike[str] | anyio.Path) -> None:
        self.root = anyio.Path(root)
        self._locks_dir = self.root / "locks"

    async def create(
        self,
        *,
        flow_path: str | PathLike[str],
        definition_digest: str,
        inputs: Mapping[str, object],
        resource_capacities: Mapping[str, ResourceCapacity] | None = None,
        checkpoint: ExecutionCheckpoint | None = None,
    ) -> HumanWorkflowRun:
        """Create and persist a new running job with an opaque run ID."""

        if _DIGEST_PATTERN.fullmatch(definition_digest) is None:
            raise ValueError("definition_digest must be 64 lowercase hexadecimal characters")
        normalized_path = str(flow_path)
        if not normalized_path:
            raise ValueError("flow_path must be non-empty")
        await self.root.mkdir(parents=True, exist_ok=True)
        await self._locks_dir.mkdir(parents=True, exist_ok=True)

        for _attempt in range(10):
            run_id = new_opaque_id()
            run = HumanWorkflowRun(
                run_id=run_id,
                status="running",
                flow_path=normalized_path,
                definition_digest=definition_digest,
                inputs=dict(inputs),
                resource_capacities=dict(resource_capacities or {}),
                checkpoint=checkpoint,
            )
            run_lock: _RunLock | None = None
            try:
                run_lock = await _RunLock.try_acquire(self._lock_path(run_id))
                if run_lock is None:
                    continue
                logger.debug(f"FusionFlow run lock acquired for create {run_id!r}")
                if await self._run_path(run_id).exists():
                    continue
                await self._write(run)
                return run
            finally:
                if run_lock is not None:
                    await run_lock.release()
                    logger.debug(f"FusionFlow run lock released for create {run_id!r}")
        raise JobStoreError("could not allocate a unique FusionFlow run ID")

    async def load(self, run_id: str) -> HumanWorkflowRun:
        """Load and strictly validate one atomically published run document."""

        _validate_opaque_id(run_id, "run_id", error_type=ValueError)
        path = self._run_path(run_id)
        try:
            source = await path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise FileNotFoundError(f"FusionFlow run {run_id!r} does not exist") from None
        try:
            payload = json.loads(
                source,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, InvalidRunStateError) as error:
            raise InvalidRunStateError(f"invalid state document for FusionFlow run {run_id!r}: {error}") from error
        run = _run_from_json(payload)
        if run.run_id != run_id:
            raise InvalidRunStateError(f"state filename identifies run {run_id!r}, document identifies {run.run_id!r}")
        return run

    async def save(self, run: HumanWorkflowRun) -> None:
        """Atomically replace an existing run document.

        Mutating adapters should call this through :meth:`acquire`'s
        :class:`RunLease`.  The direct method remains public for composition and
        tests, but never creates a missing run.
        """

        _validate_run(run, error_type=ValueError)
        if not await self._run_path(run.run_id).exists():
            raise FileNotFoundError(f"FusionFlow run {run.run_id!r} does not exist")
        await self._write(run)

    @asynccontextmanager
    async def acquire(self, run_id: str) -> AsyncIterator[RunLease]:
        """Acquire a non-blocking, per-run advisory-lock lease.

        A concurrent caller is rejected instead of waiting so a duplicate
        Haitun message cannot execute the same checkpoint twice.  The open
        file handle is released by the OS if this process exits unexpectedly.
        """

        _validate_opaque_id(run_id, "run_id", error_type=ValueError)
        if not await self._run_path(run_id).exists():
            raise FileNotFoundError(f"FusionFlow run {run_id!r} does not exist")
        await self._locks_dir.mkdir(parents=True, exist_ok=True)
        run_lock: _RunLock | None = None
        lease: RunLease | None = None
        try:
            run_lock = await _RunLock.try_acquire(self._lock_path(run_id))
            if run_lock is None:
                logger.debug(f"FusionFlow run lock busy for {run_id!r}")
                raise RunAlreadyActiveError(f"FusionFlow run {run_id!r} is already active")
            logger.debug(f"FusionFlow run lock acquired for {run_id!r}")
            lease = RunLease(self, run_id)
            # Recheck under the lease in case a future deletion API races us.
            if not await self._run_path(run_id).exists():
                raise FileNotFoundError(f"FusionFlow run {run_id!r} does not exist")
            yield lease
        finally:
            if lease is not None:
                lease._release()
            if run_lock is not None:
                await run_lock.release()
                logger.debug(f"FusionFlow run lock released for {run_id!r}")

    def _run_path(self, run_id: str) -> anyio.Path:
        return self.root / f"{run_id}.json"

    def _lock_path(self, run_id: str) -> anyio.Path:
        # The distinct suffix intentionally ignores pre-advisory ``.lock``
        # directories, which could otherwise brick a run after an upgrade.
        return self._locks_dir / f"{run_id}.lockfile"

    async def _write(self, run: HumanWorkflowRun) -> None:
        payload = _run_to_json(run)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        target = self._run_path(run.run_id)
        temporary = self.root / f".{run.run_id}.{secrets.token_hex(8)}.tmp"
        try:
            await temporary.write_text(f"{encoded}\n", encoding="utf-8")
            await temporary.replace(target)
        finally:
            with suppress(FileNotFoundError):
                await temporary.unlink()


def _try_open_locked_file(path: str) -> BinaryIO | None:
    """Open ``path`` and take its platform advisory lock without waiting."""

    with ExitStack() as stack:
        lock_file = stack.enter_context(open(path, "a+b"))
        if os.name == "nt":
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            locking_module = cast(_WindowsLockingModule, _LOCKING_MODULE)
            try:
                locking_module.locking(
                    lock_file.fileno(),
                    locking_module.LK_NBLCK,
                    1,
                )
            except OSError as error:
                if error.errno in {
                    errno.EACCES,
                    errno.EAGAIN,
                    errno.EDEADLK,
                }:
                    return None
                raise
        else:
            locking_module = cast(_PosixLockingModule, _LOCKING_MODULE)
            try:
                locking_module.flock(
                    lock_file.fileno(),
                    locking_module.LOCK_EX | locking_module.LOCK_NB,
                )
            except BlockingIOError:
                return None
        stack.pop_all()
        return lock_file


def _try_reserve_process_lock(path: str) -> str | None:
    """Reserve one canonical lock path inside this process."""

    reservation_key = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    with _PROCESS_LOCK_RESERVATIONS_GUARD:
        if reservation_key in _PROCESS_LOCK_RESERVATIONS:
            return None
        _PROCESS_LOCK_RESERVATIONS.add(reservation_key)
    return reservation_key


def _release_process_lock(reservation_key: str) -> None:
    """Release one process-local reservation after its OS lock is gone."""

    with _PROCESS_LOCK_RESERVATIONS_GUARD:
        _PROCESS_LOCK_RESERVATIONS.remove(reservation_key)


def _close_locked_file(
    lock_file: BinaryIO,
    reservation_key: str,
) -> None:
    """Close the OS lock before making its process reservation available."""

    try:
        lock_file.close()
    finally:
        _release_process_lock(reservation_key)


def _run_to_json(run: HumanWorkflowRun) -> dict[str, object]:
    _validate_run(run, error_type=ValueError)
    checkpoint: dict[str, object] | None = None
    if run.checkpoint is not None:
        checkpoint = {
            "workflow_id": run.checkpoint.workflow_id,
            "plan_digest": run.checkpoint.plan_digest,
            "values": _copy_json_mapping(
                run.checkpoint.values,
                context="checkpoint.values",
                error_type=ValueError,
            ),
            "completed_step_ids": list(run.checkpoint.completed_step_ids),
            "completed_selection_ids": list(run.checkpoint.completed_selection_ids),
            "foreach_iterations": [
                {
                    "step_id": iteration.step_id,
                    "iteration_index": iteration.iteration_index,
                    "attempts": iteration.attempts,
                    "outputs": iteration.outputs,
                    "error": iteration.error,
                }
                for iteration in run.checkpoint.foreach_iterations
            ],
        }
    request: dict[str, object] | None = None
    if run.prepared_request is not None:
        request = {
            "request_id": run.prepared_request.request_id,
            "step_id": run.prepared_request.step_id,
            "question": run.prepared_request.question,
            "output_artifact_ids": list(run.prepared_request.output_artifact_ids),
            "options": list(run.prepared_request.options),
            "recommended": run.prepared_request.recommended,
            "default": run.prepared_request.default,
        }
    capacities: dict[str, object] = {}
    for resource_id, capacity in run.resource_capacities.items():
        capacities[resource_id] = capacity if type(capacity) is int else list(cast(Sequence[str], capacity))
    return {
        "version": run.version,
        "run_id": run.run_id,
        "status": run.status,
        "flow_path": run.flow_path,
        "definition_digest": run.definition_digest,
        "inputs": run.inputs,
        "resource_capacities": capacities,
        "checkpoint": checkpoint,
        "prepared_request": request,
        "human_responses": run.human_responses,
        "outputs": run.outputs,
        "error": run.error,
    }


def _run_from_json(payload: object) -> HumanWorkflowRun:
    if not isinstance(payload, dict):
        raise InvalidRunStateError("run state must be a JSON object")
    payload = cast(dict[str, object], payload)
    _require_exact_keys(payload, _RUN_KEYS, "run state")
    if type(payload["version"]) is not int or payload["version"] != STATE_VERSION:
        raise InvalidRunStateError(f"unsupported run state version: {payload['version']!r}")

    checkpoint_payload = payload["checkpoint"]
    checkpoint: ExecutionCheckpoint | None
    if checkpoint_payload is None:
        checkpoint = None
    else:
        if not isinstance(checkpoint_payload, dict):
            raise InvalidRunStateError("checkpoint must be an object or null")
        checkpoint_payload = cast(dict[str, object], checkpoint_payload)
        _require_exact_keys(checkpoint_payload, _CHECKPOINT_KEYS, "checkpoint")
        try:
            iteration_payloads = checkpoint_payload["foreach_iterations"]
            if not isinstance(iteration_payloads, list):
                raise InvalidRunStateError("checkpoint.foreach_iterations must be a list")
            foreach_iterations: list[ForeachIterationCheckpoint] = []
            for index, raw_iteration in enumerate(iteration_payloads):
                if not isinstance(raw_iteration, dict):
                    raise InvalidRunStateError(f"checkpoint.foreach_iterations[{index}] must be an object")
                raw_iteration = cast(dict[str, object], raw_iteration)
                _require_exact_keys(
                    raw_iteration,
                    frozenset(
                        {
                            "step_id",
                            "iteration_index",
                            "attempts",
                            "outputs",
                            "error",
                        }
                    ),
                    f"checkpoint.foreach_iterations[{index}]",
                )
                foreach_iterations.append(
                    ForeachIterationCheckpoint(
                        step_id=_require_string(
                            raw_iteration["step_id"],
                            context=f"checkpoint.foreach_iterations[{index}].step_id",
                        ),
                        iteration_index=_require_int(
                            raw_iteration["iteration_index"],
                            context=(f"checkpoint.foreach_iterations[{index}].iteration_index"),
                        ),
                        attempts=_require_int(
                            raw_iteration["attempts"],
                            context=f"checkpoint.foreach_iterations[{index}].attempts",
                        ),
                        outputs=(
                            None
                            if raw_iteration["outputs"] is None
                            else _require_json_mapping(
                                raw_iteration["outputs"],
                                context=(f"checkpoint.foreach_iterations[{index}].outputs"),
                            )
                        ),
                        error=(
                            None
                            if raw_iteration["error"] is None
                            else _require_json_mapping(
                                raw_iteration["error"],
                                context=(f"checkpoint.foreach_iterations[{index}].error"),
                            )
                        ),
                    )
                )
            checkpoint = ExecutionCheckpoint(
                workflow_id=_require_string(
                    checkpoint_payload["workflow_id"],
                    context="checkpoint.workflow_id",
                ),
                plan_digest=_require_string(
                    checkpoint_payload["plan_digest"],
                    context="checkpoint.plan_digest",
                ),
                values=_require_json_mapping(
                    checkpoint_payload["values"],
                    context="checkpoint.values",
                ),
                completed_step_ids=_require_string_tuple(
                    checkpoint_payload["completed_step_ids"],
                    context="checkpoint.completed_step_ids",
                ),
                completed_selection_ids=_require_string_tuple(
                    checkpoint_payload["completed_selection_ids"],
                    context="checkpoint.completed_selection_ids",
                ),
                foreach_iterations=tuple(foreach_iterations),
            )
        except ValueError as error:
            raise InvalidRunStateError(str(error)) from error

    request_payload = payload["prepared_request"]
    request: HumanRequestSpec | None
    if request_payload is None:
        request = None
    else:
        if not isinstance(request_payload, dict):
            raise InvalidRunStateError("prepared_request must be an object or null")
        request_payload = cast(dict[str, object], request_payload)
        _require_exact_keys(
            request_payload,
            _REQUEST_KEYS,
            "prepared_request",
        )
        try:
            request = HumanRequestSpec(
                request_id=_require_string(
                    request_payload["request_id"],
                    context="prepared_request.request_id",
                ),
                step_id=_require_string(
                    request_payload["step_id"],
                    context="prepared_request.step_id",
                ),
                question=_require_string(
                    request_payload["question"],
                    context="prepared_request.question",
                ),
                output_artifact_ids=_require_string_tuple(
                    request_payload["output_artifact_ids"],
                    context="prepared_request.output_artifact_ids",
                ),
                options=_require_string_tuple(
                    request_payload["options"],
                    context="prepared_request.options",
                ),
                recommended=_require_int(
                    request_payload["recommended"],
                    context="prepared_request.recommended",
                ),
                default=_require_string(
                    request_payload["default"],
                    context="prepared_request.default",
                ),
            )
        except ValueError as error:
            raise InvalidRunStateError(str(error)) from error

    try:
        return HumanWorkflowRun(
            run_id=_require_string(payload["run_id"], context="run_id"),
            status=cast(
                RunStatus,
                _require_string(payload["status"], context="status"),
            ),
            flow_path=_require_string(payload["flow_path"], context="flow_path"),
            definition_digest=_require_string(
                payload["definition_digest"],
                context="definition_digest",
            ),
            inputs=_require_json_mapping(payload["inputs"], context="inputs"),
            resource_capacities=_decode_resource_capacities(payload["resource_capacities"]),
            checkpoint=checkpoint,
            prepared_request=request,
            human_responses=_require_json_mapping(
                payload["human_responses"],
                context="human_responses",
            ),
            outputs=(
                None if payload["outputs"] is None else _require_json_mapping(payload["outputs"], context="outputs")
            ),
            error=(None if payload["error"] is None else _require_string(payload["error"], context="error")),
            version=payload["version"],
        )
    except (TypeError, ValueError) as error:
        raise InvalidRunStateError(str(error)) from error


def _copy_checkpoint(
    checkpoint: ExecutionCheckpoint | None,
    *,
    error_type: type[Exception],
) -> ExecutionCheckpoint | None:
    if checkpoint is None:
        return None
    if not isinstance(checkpoint, ExecutionCheckpoint):
        raise error_type("checkpoint must be an ExecutionCheckpoint or None")
    return ExecutionCheckpoint(
        workflow_id=checkpoint.workflow_id,
        plan_digest=checkpoint.plan_digest,
        values=_copy_json_mapping(
            checkpoint.values,
            context="checkpoint.values",
            error_type=error_type,
        ),
        completed_step_ids=tuple(checkpoint.completed_step_ids),
        completed_selection_ids=tuple(checkpoint.completed_selection_ids),
        foreach_iterations=tuple(checkpoint.foreach_iterations),
    )


def _validate_run(
    run: HumanWorkflowRun,
    *,
    error_type: type[Exception],
) -> None:
    if type(run.version) is not int or run.version != STATE_VERSION:
        raise error_type(f"version must be {STATE_VERSION}")
    _validate_opaque_id(run.run_id, "run_id", error_type=error_type)
    if run.status not in {
        "running",
        "waiting_for_human",
        "completed",
        "failed",
        "cancelled",
    }:
        raise error_type(f"unsupported run status: {run.status!r}")
    if not isinstance(run.flow_path, str) or not run.flow_path:
        raise error_type("flow_path must be a non-empty string")
    if not isinstance(run.definition_digest, str) or _DIGEST_PATTERN.fullmatch(run.definition_digest) is None:
        raise error_type("definition_digest must be 64 lowercase hexadecimal characters")
    _copy_json_mapping(run.inputs, context="inputs", error_type=error_type)
    _normalize_resource_capacities(
        run.resource_capacities,
        error_type=error_type,
    )
    _copy_checkpoint(run.checkpoint, error_type=error_type)
    if run.prepared_request is not None:
        _validate_request(run.prepared_request, error_type=error_type)
    responses = _copy_json_mapping(
        run.human_responses,
        context="human_responses",
        error_type=error_type,
    )
    for request_id in responses:
        _validate_opaque_id(
            request_id,
            "human response request_id",
            error_type=error_type,
        )
    if run.status == "waiting_for_human":
        if run.checkpoint is None:
            raise error_type("waiting_for_human requires a checkpoint")
        if run.prepared_request is None:
            raise error_type("waiting_for_human requires prepared_request")
        if run.prepared_request.request_id in responses:
            raise error_type("waiting_for_human request already has a submitted response")
    elif run.prepared_request is not None:
        raise error_type("prepared_request is only valid while waiting_for_human")

    if run.status == "completed":
        if run.outputs is None:
            raise error_type("completed runs require outputs")
    elif run.outputs is not None:
        raise error_type("outputs are only valid for completed runs")
    if run.outputs is not None:
        _copy_json_mapping(run.outputs, context="outputs", error_type=error_type)

    if run.status == "failed":
        if not isinstance(run.error, str) or not run.error.strip():
            raise error_type("failed runs require a non-empty error")
    elif run.error is not None:
        raise error_type("error is only valid for failed runs")


def _validate_request(
    request: HumanRequestSpec,
    *,
    error_type: type[Exception],
) -> None:
    _validate_opaque_id(
        request.request_id,
        "request_id",
        error_type=error_type,
    )
    if not isinstance(request.step_id, str) or not request.step_id.strip():
        raise error_type("step_id must be a non-empty string")
    if not isinstance(request.question, str) or not request.question.strip():
        raise error_type("question must be a non-empty string")
    if len(request.options) > 4:
        raise error_type("options must contain at most four entries")
    if not all(isinstance(option, str) and option.strip() for option in request.options):
        raise error_type("options must contain only non-empty strings")
    if type(request.recommended) is not int or not 0 <= request.recommended <= len(request.options):
        raise error_type(f"recommended must be between 0 and {len(request.options)}")
    if not isinstance(request.default, str):
        raise error_type("default must be a string")
    if not all(isinstance(artifact_id, str) and artifact_id for artifact_id in request.output_artifact_ids):
        raise error_type("output_artifact_ids must contain non-empty strings")
    if len(set(request.output_artifact_ids)) != len(request.output_artifact_ids):
        raise error_type("output_artifact_ids must be unique")


def _normalize_resource_capacities(
    capacities: Mapping[str, ResourceCapacity],
    *,
    error_type: type[Exception],
) -> dict[str, ResourceCapacity]:
    if not isinstance(capacities, Mapping):
        raise error_type("resource_capacities must be a mapping")
    normalized: dict[str, ResourceCapacity] = {}
    for resource_id, capacity in capacities.items():
        if not isinstance(resource_id, str) or not resource_id:
            raise error_type("resource capacity IDs must be non-empty strings")
        if type(capacity) is int:
            if capacity < 1:
                raise error_type(f"resource capacity for {resource_id!r} must be positive")
            normalized[resource_id] = capacity
            continue
        if isinstance(capacity, (str, bytes)) or not isinstance(
            capacity,
            Sequence,
        ):
            raise error_type(f"resource capacity for {resource_id!r} must be a positive integer or instance sequence")
        instances = tuple(capacity)
        if (
            not instances
            or not all(isinstance(instance_id, str) and instance_id for instance_id in instances)
            or len(set(instances)) != len(instances)
        ):
            raise error_type(f"resource instances for {resource_id!r} must be non-empty unique strings")
        normalized[resource_id] = cast(tuple[str, ...], instances)
    return normalized


def _decode_resource_capacities(payload: object) -> dict[str, ResourceCapacity]:
    if not isinstance(payload, dict):
        raise InvalidRunStateError("resource_capacities must be an object")
    decoded: dict[str, ResourceCapacity] = {}
    for resource_id, capacity in payload.items():
        if not isinstance(resource_id, str):
            raise InvalidRunStateError("resource capacity IDs must be strings")
        if type(capacity) is int:
            decoded[resource_id] = capacity
        elif isinstance(capacity, list):
            decoded[resource_id] = tuple(cast(list[str], capacity))
        else:
            raise InvalidRunStateError(f"resource capacity for {resource_id!r} must be an integer or array")
    return _normalize_resource_capacities(
        decoded,
        error_type=InvalidRunStateError,
    )


def _copy_json_mapping(
    value: object,
    *,
    context: str,
    error_type: type[Exception],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise error_type(f"{context} must be a mapping")
    copied = _copy_json_value(
        dict(value),
        context=context,
        active=set(),
        error_type=error_type,
    )
    return cast(dict[str, object], copied)


def _copy_json_value(
    value: object,
    *,
    context: str,
    active: set[int],
    error_type: type[Exception],
) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise error_type(f"{context} contains a non-finite number")
        return value
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise error_type(f"{context} contains a reference cycle")
        active.add(identity)
        try:
            return [
                _copy_json_value(
                    item,
                    context=f"{context}[{index}]",
                    active=active,
                    error_type=error_type,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise error_type(f"{context} contains a reference cycle")
        active.add(identity)
        try:
            copied: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise error_type(f"{context} contains a non-string object key")
                copied[key] = _copy_json_value(
                    item,
                    context=f"{context}.{key}",
                    active=active,
                    error_type=error_type,
                )
            return copied
        finally:
            active.remove(identity)
    raise error_type(f"{context} contains a non-JSON value of type {type(value).__name__}")


def _require_json_mapping(value: object, *, context: str) -> dict[str, object]:
    return _copy_json_mapping(
        value,
        context=context,
        error_type=InvalidRunStateError,
    )


def _require_string(value: object, *, context: str) -> str:
    if not isinstance(value, str):
        raise InvalidRunStateError(f"{context} must be a string")
    return value


def _require_int(value: object, *, context: str) -> int:
    if type(value) is not int:
        raise InvalidRunStateError(f"{context} must be an integer")
    return value


def _require_string_tuple(value: object, *, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidRunStateError(f"{context} must be an array of strings")
    return tuple(cast(list[str], value))


def _require_exact_keys(
    value: Mapping[str, object],
    expected: frozenset[str],
    context: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise InvalidRunStateError(f"{context} fields do not match schema: missing={missing}, unknown={unknown}")


def _validate_opaque_id(
    value: object,
    context: str,
    *,
    error_type: type[Exception],
) -> None:
    if not isinstance(value, str) or _OPAQUE_ID_PATTERN.fullmatch(value) is None:
        raise error_type(f"{context} must be exactly 32 lowercase hexadecimal characters")


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRunStateError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise InvalidRunStateError(f"non-finite JSON number: {value}")


__all__ = [
    "STATE_VERSION",
    "HumanRequestSpec",
    "HumanWorkflowRun",
    "InvalidRunStateError",
    "JobStore",
    "JobStoreError",
    "RunAlreadyActiveError",
    "RunLease",
    "RunStatus",
    "new_opaque_id",
]
