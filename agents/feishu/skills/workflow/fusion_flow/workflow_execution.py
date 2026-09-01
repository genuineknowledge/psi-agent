"""Compile and execute durable acyclic workflow plans."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import operator
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import cast

import anyio
from loguru import logger

from .execution.flow import _retry_operation, _run_parallel_tasks
from .step_timing import AttemptTiming, IterationTiming, StepTiming, StepTimingMetadata, TimingStatus
from .workflow_graph import (
    ArtifactOperand,
    ComparisonCondition,
    ConsumesEdge,
    ForeachEdge,
    LiteralOperand,
    ProducesEdge,
    ResourceRequirement,
    SelectCondition,
    StepNode,
    WorkflowGraph,
)


class ExecutionPlanError(ValueError):
    """A workflow graph cannot be lowered to the supported execution plan."""


class _WorkflowControlSignalError(Exception):
    """A dispatcher signal that suspends or redirects workflow execution.

    Unlike an ordinary step failure, a control signal must escape foreach
    error collection unchanged.  Human-input suspension is the first caller,
    but the marker intentionally contains no Human-specific state.
    """


WorkflowControlSignal = _WorkflowControlSignalError
WorkflowControlSignal.__name__ = "WorkflowControlSignal"
WorkflowControlSignal.__qualname__ = "WorkflowControlSignal"


class StepOutputError(ExecutionPlanError):
    """A dispatcher returned an invalid output mapping for one invocation."""


@dataclass(frozen=True, slots=True)
class Await:
    """Wait until the named steps complete."""

    step_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AwaitSelections:
    """Wait until the named select outputs are available."""

    artifact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Invoke:
    """Invoke one graph step."""

    step_id: str


@dataclass(frozen=True, slots=True)
class Select:
    """Evaluate the selector identified by its output artifact."""

    output_artifact_id: str


type PlanInstruction = Await | AwaitSelections | Invoke | Select
type OperationId = tuple[str, str]
type _TimingStart = tuple[str, float]


@dataclass(frozen=True, slots=True)
class Fiber:
    """A concurrently started sequence of plan instructions."""

    fiber_id: str
    instructions: tuple[PlanInstruction, ...]


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """An inspectable collection of fibers started by the executor."""

    workflow_id: str
    fibers: tuple[Fiber, ...]
    loops: tuple[LoopRegionPlan, ...] = ()


@dataclass(frozen=True, slots=True)
class LoopRegionPlan:
    """A feedback component lowered to one repeatable acyclic epoch plan."""

    loop_id: str
    feedback_artifact_ids: tuple[str, ...]
    step_ids: tuple[str, ...]
    terminal_step_id: str
    terminal_output_artifact_id: str
    fibers: tuple[Fiber, ...]


@dataclass(frozen=True, slots=True)
class ResourceGrant:
    """Concrete resource instances reserved for one step invocation."""

    resource_id: str
    instance_ids: tuple[str, ...]

    @property
    def amount(self) -> int:
        """Return the number of reserved instances."""

        return len(self.instance_ids)


@dataclass(frozen=True, slots=True)
class ResourceLease:
    """All resource grants held for one step invocation."""

    grants: tuple[ResourceGrant, ...] = ()

    def instances(self, resource_id: str) -> tuple[str, ...]:
        """Return the concrete instances granted for one resource."""

        for grant in self.grants:
            if grant.resource_id == resource_id:
                return grant.instance_ids
        return ()


@dataclass(frozen=True, slots=True)
class DispatchContext:
    """Runtime scheduling information supplied to one step invocation."""

    resource_lease: ResourceLease = ResourceLease()
    invocation_id: str = ""
    iteration_index: int | None = None
    attempt: int = 1
    loop_id: str | None = None
    epoch: int | None = None


type StepDispatcher = Callable[
    [StepNode, Mapping[str, object], DispatchContext],
    Awaitable[Mapping[str, object]],
]
type ResourceCapacity = int | Sequence[str]


@dataclass(frozen=True, slots=True)
class ForeachIterationCheckpoint:
    """One terminal foreach StepInstance result stored outside global artifacts."""

    step_id: str
    iteration_index: int
    attempts: int
    outputs: dict[str, object] | None = None
    error: dict[str, object] | None = None

    def __post_init__(self) -> None:
        """Validate a successful-or-failed terminal record and copy its JSON."""

        if not isinstance(self.step_id, str) or not self.step_id:
            raise ValueError("foreach iteration step_id must be a non-empty string")
        if type(self.iteration_index) is not int or self.iteration_index < 0:
            raise ValueError("foreach iteration_index must be a non-negative integer")
        if type(self.attempts) is not int or self.attempts < 1:
            raise ValueError("foreach attempts must be a positive integer")
        if (self.outputs is None) == (self.error is None):
            raise ValueError("foreach iteration must contain exactly one of outputs or error")
        if self.outputs is not None:
            object.__setattr__(
                self,
                "outputs",
                _copy_json_mapping(
                    self.outputs,
                    context="foreach iteration outputs",
                ),
            )
        if self.error is not None:
            copied_error = _copy_json_mapping(
                self.error,
                context="foreach iteration error",
            )
            if set(copied_error) != {"kind", "message"}:
                raise ValueError("foreach iteration error must contain exactly kind and message")
            if not isinstance(copied_error["kind"], str) or not copied_error["kind"]:
                raise ValueError("foreach iteration error kind must be a non-empty string")
            if not isinstance(copied_error["message"], str):
                raise ValueError("foreach iteration error message must be a string")
            object.__setattr__(self, "error", copied_error)


@dataclass(frozen=True, slots=True)
class LoopExecutionCheckpoint:
    """One feedback region's committed snapshot and optional inflight epoch.

    ``current_values`` is the last complete feedback-state vector.  Outputs
    from the epoch currently being evaluated live in ``staged_values`` until
    every next-state writer and the TerminalStep have completed.  Keeping the
    namespaces separate prevents a partially completed epoch from becoming a
    visible state update after resume.
    """

    loop_id: str
    epoch: int
    current_values: dict[str, object]
    staged_values: dict[str, object] = field(default_factory=dict)
    completed_step_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate identity/counters and defensively copy strict JSON."""

        if not isinstance(self.loop_id, str) or not self.loop_id:
            raise ValueError("loop checkpoint loop_id must be a non-empty string")
        if type(self.epoch) is not int or self.epoch < 0:
            raise ValueError("loop checkpoint epoch must be a non-negative integer")
        object.__setattr__(
            self,
            "current_values",
            _copy_json_mapping(
                self.current_values,
                context=f"loop {self.loop_id} current values",
            ),
        )
        object.__setattr__(
            self,
            "staged_values",
            _copy_json_mapping(
                self.staged_values,
                context=f"loop {self.loop_id} staged values",
            ),
        )
        if isinstance(self.completed_step_ids, str | bytes):
            raise ValueError("loop checkpoint completed_step_ids must be a sequence")
        normalized = tuple(self.completed_step_ids)
        if not all(isinstance(step_id, str) and step_id for step_id in normalized):
            raise ValueError("loop checkpoint completed_step_ids must contain non-empty strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("loop checkpoint completed_step_ids must not contain duplicates")
        object.__setattr__(self, "completed_step_ids", tuple(sorted(normalized)))


@dataclass(frozen=True, slots=True)
class ExecutionCheckpoint:
    """A plan-bound JSON snapshot of materialized values and completed operations."""

    workflow_id: str
    plan_digest: str
    values: dict[str, object]
    completed_step_ids: tuple[str, ...] = ()
    completed_selection_ids: tuple[str, ...] = ()
    foreach_iterations: tuple[ForeachIterationCheckpoint, ...] = ()
    loops: tuple[LoopExecutionCheckpoint, ...] = ()

    def __post_init__(self) -> None:
        """Validate the execution identity and defensively copy JSON values."""

        if not isinstance(self.workflow_id, str) or not self.workflow_id:
            raise ValueError("checkpoint workflow_id must be a non-empty string")
        if (
            not isinstance(self.plan_digest, str)
            or len(self.plan_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.plan_digest)
        ):
            raise ValueError("checkpoint plan_digest must be 64 lowercase hexadecimal characters")
        object.__setattr__(
            self,
            "values",
            _copy_json_mapping(
                self.values,
                context="checkpoint values",
            ),
        )
        for field_name, identifiers in (
            ("completed_step_ids", self.completed_step_ids),
            ("completed_selection_ids", self.completed_selection_ids),
        ):
            if isinstance(identifiers, str | bytes):
                raise ValueError(f"checkpoint {field_name} must be a sequence of operation IDs")
            normalized = tuple(identifiers)
            if not all(isinstance(identifier, str) and identifier for identifier in normalized):
                raise ValueError(f"checkpoint {field_name} must contain only non-empty strings")
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"checkpoint {field_name} must not contain duplicates")
            object.__setattr__(self, field_name, normalized)
        if not isinstance(self.foreach_iterations, tuple):
            raise ValueError("checkpoint foreach_iterations must be a tuple")
        if not all(isinstance(iteration, ForeachIterationCheckpoint) for iteration in self.foreach_iterations):
            raise ValueError("checkpoint foreach_iterations must contain only ForeachIterationCheckpoint")
        copied_iterations = tuple(
            ForeachIterationCheckpoint(
                step_id=iteration.step_id,
                iteration_index=iteration.iteration_index,
                attempts=iteration.attempts,
                outputs=iteration.outputs,
                error=iteration.error,
            )
            for iteration in self.foreach_iterations
        )
        ordered_iterations = tuple(
            sorted(
                copied_iterations,
                key=lambda iteration: (
                    iteration.step_id,
                    iteration.iteration_index,
                ),
            )
        )
        identities = [(iteration.step_id, iteration.iteration_index) for iteration in ordered_iterations]
        if len(set(identities)) != len(identities):
            raise ValueError("checkpoint foreach_iterations must not contain duplicates")
        object.__setattr__(self, "foreach_iterations", ordered_iterations)
        if not isinstance(self.loops, tuple):
            raise ValueError("checkpoint loops must be a tuple")
        if not all(isinstance(loop, LoopExecutionCheckpoint) for loop in self.loops):
            raise ValueError("checkpoint loops must contain only LoopExecutionCheckpoint")
        copied_loops = tuple(
            LoopExecutionCheckpoint(
                loop_id=loop.loop_id,
                epoch=loop.epoch,
                current_values=loop.current_values,
                staged_values=loop.staged_values,
                completed_step_ids=loop.completed_step_ids,
            )
            for loop in self.loops
        )
        loop_ids = [loop.loop_id for loop in copied_loops]
        if len(set(loop_ids)) != len(loop_ids):
            raise ValueError("checkpoint loops must not contain duplicate loop IDs")
        object.__setattr__(self, "loops", tuple(sorted(copied_loops, key=lambda loop: loop.loop_id)))


type CheckpointObserver = Callable[[ExecutionCheckpoint], Awaitable[None]]


def execution_plan_digest(
    plan: ExecutionPlan,
    graph: WorkflowGraph,
) -> str:
    """Return a stable digest of graph semantics and explicit plan structure."""

    if not isinstance(plan, ExecutionPlan):
        raise ExecutionPlanError("plan must be an ExecutionPlan")
    if not isinstance(graph, WorkflowGraph):
        raise ExecutionPlanError("graph must be a WorkflowGraph")
    if plan.workflow_id != graph.workflow_id:
        raise ExecutionPlanError(f"plan targets {plan.workflow_id}, not {graph.workflow_id}")

    def serialize_fibers(source: Sequence[Fiber]) -> list[dict[str, object]]:
        fibers: list[dict[str, object]] = []
        for fiber in sorted(source, key=lambda item: item.fiber_id):
            instructions: list[dict[str, object]] = []
            for instruction in fiber.instructions:
                if isinstance(instruction, Await):
                    instructions.append(
                        {
                            "kind": "await_steps",
                            "step_ids": sorted(instruction.step_ids),
                        }
                    )
                elif isinstance(instruction, AwaitSelections):
                    instructions.append(
                        {
                            "artifact_ids": sorted(instruction.artifact_ids),
                            "kind": "await_selections",
                        }
                    )
                elif isinstance(instruction, Invoke):
                    instructions.append(
                        {
                            "kind": "invoke",
                            "step_id": instruction.step_id,
                        }
                    )
                elif isinstance(instruction, Select):
                    instructions.append(
                        {
                            "kind": "select",
                            "output_artifact_id": instruction.output_artifact_id,
                        }
                    )
                else:
                    raise ExecutionPlanError(f"plan contains unknown instruction: {type(instruction).__name__}")
            fibers.append(
                {
                    "fiber_id": fiber.fiber_id,
                    "instructions": instructions,
                }
            )
        return fibers

    fibers = serialize_fibers(plan.fibers)
    loops = [
        {
            "feedback_artifact_ids": sorted(loop.feedback_artifact_ids),
            "fibers": serialize_fibers(loop.fibers),
            "loop_id": loop.loop_id,
            "step_ids": sorted(loop.step_ids),
            "terminal_output_artifact_id": loop.terminal_output_artifact_id,
            "terminal_step_id": loop.terminal_step_id,
        }
        for loop in sorted(plan.loops, key=lambda item: item.loop_id)
    ]

    plan_payload: dict[str, object] = {
        "fibers": fibers,
        "workflow_id": plan.workflow_id,
    }
    checkpoint_format = "psi-agent-execution-checkpoint-v1"
    if loops:
        checkpoint_format = "psi-agent-execution-checkpoint-v2"
        plan_payload["loops"] = loops
    payload = {
        "format": checkpoint_format,
        "graph": graph.to_dict(),
        "plan": plan_payload,
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def create_execution_checkpoint(
    plan: ExecutionPlan,
    graph: WorkflowGraph,
    *,
    values: Mapping[str, object],
    completed_step_ids: Sequence[str] = (),
    completed_selection_ids: Sequence[str] = (),
    foreach_iterations: Sequence[ForeachIterationCheckpoint] = (),
    loops: Sequence[LoopExecutionCheckpoint] = (),
) -> ExecutionCheckpoint:
    """Create a checkpoint bound to exactly one workflow and execution plan."""

    return ExecutionCheckpoint(
        workflow_id=graph.workflow_id,
        plan_digest=execution_plan_digest(plan, graph),
        values=dict(values),
        completed_step_ids=tuple(completed_step_ids),
        completed_selection_ids=tuple(completed_selection_ids),
        foreach_iterations=tuple(foreach_iterations),
        loops=tuple(loops),
    )


@dataclass(slots=True)
class _AdmissionState:
    """Run-local counters committed under one allocator condition."""

    max_concurrency: int | None
    running: int = 0


class ResourceAllocator:
    """Atomically lease named resource instances from fixed local pools."""

    def __init__(self, capacities: Mapping[str, ResourceCapacity]) -> None:
        """Validate and copy anonymous capacities or explicit instance IDs."""

        if not isinstance(capacities, Mapping):
            raise ExecutionPlanError("resource_capacities must be a mapping")

        instances_by_resource: dict[str, tuple[str, ...]] = {}
        for resource_id, capacity in capacities.items():
            if not isinstance(resource_id, str) or not resource_id:
                raise ExecutionPlanError("resource capacity IDs must be non-empty strings")
            if type(capacity) is int:
                if capacity < 1:
                    raise ExecutionPlanError(f"resource capacity for {resource_id!r} must be a positive integer")
                instances = tuple(f"{resource_id}-{index}" for index in range(capacity))
            else:
                if isinstance(capacity, (str, bytes)) or not isinstance(capacity, Sequence):
                    raise ExecutionPlanError(
                        f"resource capacity for {resource_id!r} must be a positive integer or instance sequence"
                    )
                instances = tuple(cast(Sequence[str], capacity))
                if not instances:
                    raise ExecutionPlanError(f"resource instances for {resource_id!r} must not be empty")
                if not all(isinstance(instance_id, str) and instance_id for instance_id in instances):
                    raise ExecutionPlanError(f"resource instances for {resource_id!r} must be non-empty strings")
                if len(set(instances)) != len(instances):
                    raise ExecutionPlanError(f"resource instances for {resource_id!r} must be unique")
            instances_by_resource[resource_id] = instances

        self._instances_by_resource = instances_by_resource
        self._available = {resource_id: list(instances) for resource_id, instances in instances_by_resource.items()}
        self._instance_order = {
            resource_id: {instance_id: index for index, instance_id in enumerate(instances)}
            for resource_id, instances in instances_by_resource.items()
        }
        self._condition = anyio.Condition()

    async def preflight(
        self,
        requirements_by_step: Mapping[str, tuple[ResourceRequirement, ...]],
    ) -> None:
        """Reject missing or forever-unsatisfiable requirements before dispatch."""

        for step_id in sorted(requirements_by_step):
            seen: set[str] = set()
            for requirement in requirements_by_step[step_id]:
                resource_id = requirement.resource_id
                if resource_id in seen:
                    raise ExecutionPlanError(f"step {step_id!r} has duplicate resource requirement: {resource_id!r}")
                seen.add(resource_id)
                if resource_id not in self._instances_by_resource:
                    raise ExecutionPlanError(f"step {step_id!r} requires missing resource: {resource_id!r}")
                if type(requirement.amount) is not int or requirement.amount < 1:
                    raise ExecutionPlanError(
                        f"step {step_id!r} resource amount for {resource_id!r} must be a positive integer"
                    )
                capacity = len(self._instances_by_resource[resource_id])
                if requirement.amount > capacity:
                    raise ExecutionPlanError(
                        f"step {step_id!r} requires {requirement.amount} of {resource_id!r}, "
                        f"but total capacity is {capacity}"
                    )

    @asynccontextmanager
    async def lease(
        self,
        requirements: tuple[ResourceRequirement, ...],
    ) -> AsyncIterator[ResourceLease]:
        """Wait for and atomically hold every requirement until context exit."""

        lease: ResourceLease | None = None
        try:
            lease = await self._acquire(requirements)
            yield lease
        finally:
            if lease is not None and lease.grants:
                # Workflow cancellation must never interrupt resource return.
                with anyio.CancelScope(shield=True):
                    await self._release(lease)

    @asynccontextmanager
    async def _admit(
        self,
        requirements: tuple[ResourceRequirement, ...],
        *,
        state: _AdmissionState,
    ) -> AsyncIterator[ResourceLease]:
        """Atomically reserve run concurrency and resources."""

        lease: ResourceLease | None = None
        try:
            try:
                lease = await self._acquire(
                    requirements,
                    state=state,
                )
            except ExecutionPlanError:
                raise
            except Exception as error:
                raise ExecutionPlanError("workflow resource admission failed") from error
            yield lease
        finally:
            if lease is not None:
                # A no-resource step still owns a run admission counter.
                with anyio.CancelScope(shield=True):
                    try:
                        await self._release(
                            lease,
                            state=state,
                        )
                    except ExecutionPlanError:
                        raise
                    except Exception as error:
                        raise ExecutionPlanError("workflow resource release failed") from error

    async def _acquire(
        self,
        requirements: tuple[ResourceRequirement, ...],
        *,
        state: _AdmissionState | None = None,
    ) -> ResourceLease:
        """Atomically commit admission counters and requested instances."""

        ordered = tuple(sorted(requirements, key=lambda requirement: requirement.resource_id))
        if not ordered and state is None:
            return ResourceLease()

        async with self._condition:
            while (
                state is not None and state.max_concurrency is not None and state.running >= state.max_concurrency
            ) or any(
                requirement.resource_id not in self._available
                or len(self._available[requirement.resource_id]) < requirement.amount
                for requirement in ordered
            ):
                await self._condition.wait()

            if state is not None:
                state.running += 1

            grants: list[ResourceGrant] = []
            for requirement in ordered:
                available = self._available[requirement.resource_id]
                instance_ids = tuple(available[: requirement.amount])
                del available[: requirement.amount]
                grants.append(
                    ResourceGrant(
                        resource_id=requirement.resource_id,
                        instance_ids=instance_ids,
                    )
                )
            lease = ResourceLease(tuple(grants))
            if lease.grants:
                logger.debug(f"Acquired workflow resources: {lease.grants}")
            return lease

    async def _release(
        self,
        lease: ResourceLease,
        *,
        state: _AdmissionState | None = None,
    ) -> None:
        """Return admission and resources, then wake every eligible waiter."""

        async with self._condition:
            if state is not None:
                state.running -= 1
            for grant in lease.grants:
                available = self._available[grant.resource_id]
                available.extend(grant.instance_ids)
                rank = self._instance_order[grant.resource_id]
                available.sort(key=rank.__getitem__)
            if lease.grants:
                logger.debug(f"Released workflow resources: {lease.grants}")
            self._condition.notify_all()


def generate_plan(graph: WorkflowGraph) -> ExecutionPlan:
    """Lower acyclic dataflow and one declarative feedback region into fibers."""

    loop = _discover_feedback_loop(graph)
    if loop is not None:
        return _generate_feedback_plan(graph, loop)

    step_producers = {edge.artifact_id: edge.step_id for edge in graph.edges if isinstance(edge, ProducesEdge)}
    selection_producers = {selector.output_artifact_id: selector.output_artifact_id for selector in graph.selectors}
    for artifact in graph.artifacts:
        if artifact.is_input and (
            artifact.artifact_id in step_producers or artifact.artifact_id in selection_producers
        ):
            raise ExecutionPlanError(f"input artifact is also produced: {artifact.artifact_id}")

    step_ids = {step.step_id for step in graph.steps}
    awaited_steps_by_step: dict[str, set[str]] = {}
    for step in graph.steps:
        explicit_dependencies = set(step.depends_on)
        unknown_dependencies = explicit_dependencies - step_ids
        if unknown_dependencies:
            raise ExecutionPlanError(f"step {step.step_id!r} depends on unknown steps: {sorted(unknown_dependencies)}")
        awaited_steps_by_step[step.step_id] = explicit_dependencies
    awaited_selections_by_step: dict[str, set[str]] = {step.step_id: set() for step in graph.steps}
    for edge in graph.edges:
        if not isinstance(edge, ConsumesEdge | ForeachEdge):
            continue
        step_producer = step_producers.get(edge.artifact_id)
        if step_producer is not None:
            awaited_steps_by_step[edge.step_id].add(step_producer)
        selection_producer = selection_producers.get(edge.artifact_id)
        if selection_producer is not None:
            awaited_selections_by_step[edge.step_id].add(selection_producer)

    awaited_steps_by_selection: dict[str, set[str]] = {}
    awaited_selections_by_selection: dict[str, set[str]] = {}
    for selector in graph.selectors:
        awaited_steps: set[str] = set()
        awaited_selections: set[str] = set()
        for artifact_id in selector.input_artifact_ids():
            step_producer = step_producers.get(artifact_id)
            if step_producer is not None:
                awaited_steps.add(step_producer)
            selection_producer = selection_producers.get(artifact_id)
            if selection_producer is not None:
                awaited_selections.add(selection_producer)
        awaited_steps_by_selection[selector.output_artifact_id] = awaited_steps
        awaited_selections_by_selection[selector.output_artifact_id] = awaited_selections

    dependencies: dict[OperationId, set[OperationId]] = {
        ("step", step.step_id): {
            *(("step", step_id) for step_id in awaited_steps_by_step[step.step_id]),
            *(("select", artifact_id) for artifact_id in awaited_selections_by_step[step.step_id]),
        }
        for step in graph.steps
    }
    dependencies.update(
        {
            ("select", selector.output_artifact_id): {
                *(("step", step_id) for step_id in awaited_steps_by_selection[selector.output_artifact_id]),
                *(
                    ("select", artifact_id)
                    for artifact_id in awaited_selections_by_selection[selector.output_artifact_id]
                ),
            }
            for selector in graph.selectors
        }
    )
    _reject_cycles(dependencies)

    fibers: list[Fiber] = []
    for step_id in sorted(awaited_steps_by_step):
        instructions: list[PlanInstruction] = []
        step_wait_ids = tuple(sorted(awaited_steps_by_step[step_id]))
        if step_wait_ids:
            instructions.append(Await(step_wait_ids))
        selection_wait_ids = tuple(sorted(awaited_selections_by_step[step_id]))
        if selection_wait_ids:
            instructions.append(AwaitSelections(selection_wait_ids))
        instructions.append(Invoke(step_id))
        fibers.append(Fiber(step_id, tuple(instructions)))
    for output_artifact_id in sorted(awaited_steps_by_selection):
        instructions = []
        step_wait_ids = tuple(sorted(awaited_steps_by_selection[output_artifact_id]))
        if step_wait_ids:
            instructions.append(Await(step_wait_ids))
        selection_wait_ids = tuple(sorted(awaited_selections_by_selection[output_artifact_id]))
        if selection_wait_ids:
            instructions.append(AwaitSelections(selection_wait_ids))
        instructions.append(Select(output_artifact_id))
        fibers.append(Fiber(output_artifact_id, tuple(instructions)))
    return ExecutionPlan(
        graph.workflow_id,
        tuple(sorted(fibers, key=lambda fiber: fiber.fiber_id)),
    )


@dataclass(frozen=True, slots=True)
class _FeedbackLoopDraft:
    """Static feedback region before its epoch fibers are lowered."""

    feedback_artifact_ids: tuple[str, ...]
    step_ids: tuple[str, ...]
    terminal_step_id: str
    terminal_output_artifact_id: str


def _discover_feedback_loop(graph: WorkflowGraph) -> _FeedbackLoopDraft | None:
    """Recognize a single fail-closed feedback component from graph roles."""

    step_ids = {step.step_id for step in graph.steps}
    artifact_by_id = {artifact.artifact_id: artifact for artifact in graph.artifacts}
    producer_by_artifact = {edge.artifact_id: edge.step_id for edge in graph.edges if isinstance(edge, ProducesEdge)}
    consumers_by_artifact: dict[str, set[str]] = {}
    foreach_steps: set[str] = set()
    for edge in graph.edges:
        if isinstance(edge, ConsumesEdge):
            consumers_by_artifact.setdefault(edge.artifact_id, set()).add(edge.step_id)
        elif isinstance(edge, ForeachEdge):
            consumers_by_artifact.setdefault(edge.artifact_id, set()).add(edge.step_id)
            foreach_steps.add(edge.step_id)

    dependencies: dict[str, set[str]] = {step_id: set() for step_id in step_ids}
    for step in graph.steps:
        dependencies[step.step_id].update(step.depends_on)
    for artifact_id, consumers in consumers_by_artifact.items():
        producer = producer_by_artifact.get(artifact_id)
        if producer is not None:
            for consumer in consumers:
                dependencies[consumer].add(producer)

    cyclic_components = [
        component
        for component in _strongly_connected_steps(dependencies)
        if len(component) > 1 or next(iter(component)) in dependencies[next(iter(component))]
    ]
    terminals = sorted(step.step_id for step in graph.steps if step.step_type == "TerminalStep")

    if not cyclic_components:
        if terminals:
            raise ExecutionPlanError(f"TERMINAL_LOOP_NOT_FOUND: TerminalStep has no feedback component: {terminals}")
        return None
    if len(cyclic_components) != 1:
        formatted = [sorted(component) for component in cyclic_components]
        raise ExecutionPlanError(f"MULTIPLE_FEEDBACK_COMPONENTS: only one feedback component is supported: {formatted}")
    if len(terminals) != 1:
        code = "MISSING_TERMINAL_STEP" if not terminals else "MULTIPLE_TERMINAL_STEPS"
        raise ExecutionPlanError(f"{code}: feedback component requires exactly one TerminalStep: {terminals}")
    if graph.selectors:
        raise ExecutionPlanError("EAGER_SELECT_IN_FEEDBACK: feedback plans do not support select operations")
    if foreach_steps:
        raise ExecutionPlanError(
            f"FOREACH_IN_FEEDBACK_UNSUPPORTED: feedback plans do not support foreach operations: "
            f"{sorted(foreach_steps)}"
        )

    component = cyclic_components[0]
    terminal_step_id = terminals[0]
    terminal_ancestors = _step_ancestors(terminal_step_id, dependencies)
    if not (component & terminal_ancestors):
        raise ExecutionPlanError(
            f"TERMINAL_LOOP_NOT_FOUND: TerminalStep {terminal_step_id!r} does not depend on the feedback component"
        )

    # Include the cyclic core and every same-epoch dependency on a path from
    # that core to the terminal predicate.  For the first implementation this
    # deliberately rejects detached side branches and multiple regions.
    forward = _step_descendants(component, dependencies)
    loop_steps = (forward & terminal_ancestors) | component | {terminal_step_id}
    feedback_ids = tuple(
        sorted(
            artifact.artifact_id
            for artifact in graph.artifacts
            if artifact.is_input
            and producer_by_artifact.get(artifact.artifact_id) in component
            and bool(consumers_by_artifact.get(artifact.artifact_id, set()) & component)
        )
    )
    if not feedback_ids:
        raise ExecutionPlanError("MISSING_INITIAL_STATE: cycle has no feedback Artifact initialized by input_workflow")
    produced_inputs = {
        artifact.artifact_id
        for artifact in graph.artifacts
        if artifact.is_input and artifact.artifact_id in producer_by_artifact
    }
    unexpected_produced_inputs = produced_inputs - set(feedback_ids)
    if unexpected_produced_inputs:
        raise ExecutionPlanError(
            f"AMBIGUOUS_FEEDBACK_STATE: input artifacts are produced outside the feedback state set: "
            f"{sorted(unexpected_produced_inputs)}"
        )

    terminal_outputs = sorted(
        artifact_id for artifact_id, producer in producer_by_artifact.items() if producer == terminal_step_id
    )
    if len(terminal_outputs) != 1:
        raise ExecutionPlanError(
            f"INVALID_TERMINAL_OUTPUT_COUNT: TerminalStep {terminal_step_id!r} must have one output"
        )
    terminal_output = terminal_outputs[0]
    if artifact_by_id[terminal_output].artifact_type != "BoolArtifact":
        raise ExecutionPlanError(
            f"INVALID_TERMINAL_OUTPUT_TYPE: TerminalStep {terminal_step_id!r} output must be BoolArtifact"
        )
    if artifact_by_id[terminal_output].is_output or consumers_by_artifact.get(terminal_output):
        raise ExecutionPlanError(
            f"TERMINAL_OUTPUT_ESCAPES_LOOP: {terminal_output!r} is loop control and cannot be consumed or exported"
        )

    # Rebuild, rather than subtract from the aggregated dependency sets: two
    # Artifacts may connect the same producer/consumer pair, with only one of
    # those edges crossing epochs.  A set subtraction would accidentally erase
    # the remaining same-epoch dependency and hide a residual cycle.
    feedback_set = set(feedback_ids)
    epoch_dependencies: dict[str, set[str]] = {
        step.step_id: set(step.depends_on) & loop_steps for step in graph.steps if step.step_id in loop_steps
    }
    for artifact_id, consumers in consumers_by_artifact.items():
        if artifact_id in feedback_set:
            continue
        producer = producer_by_artifact.get(artifact_id)
        if producer not in loop_steps:
            continue
        for consumer in consumers & loop_steps:
            epoch_dependencies[consumer].add(producer)
    try:
        _reject_cycles(
            {
                ("step", step_id): {("step", dependency) for dependency in awaited}
                for step_id, awaited in epoch_dependencies.items()
            }
        )
    except ExecutionPlanError as error:
        raise ExecutionPlanError(
            f"RESIDUAL_EPOCH_CYCLE: cycle remains after feedback edges become cross-epoch: {error}"
        ) from error

    return _FeedbackLoopDraft(
        feedback_artifact_ids=feedback_ids,
        step_ids=tuple(sorted(loop_steps)),
        terminal_step_id=terminal_step_id,
        terminal_output_artifact_id=terminal_output,
    )


def _generate_feedback_plan(graph: WorkflowGraph, loop: _FeedbackLoopDraft) -> ExecutionPlan:
    """Build repeatable epoch fibers and final-state top-level fibers."""

    loop_steps = set(loop.step_ids)
    feedback_ids = set(loop.feedback_artifact_ids)
    producer_by_artifact = {edge.artifact_id: edge.step_id for edge in graph.edges if isinstance(edge, ProducesEdge)}
    consumed_by_step: dict[str, set[str]] = {step.step_id: set() for step in graph.steps}
    for edge in graph.edges:
        if isinstance(edge, ConsumesEdge | ForeachEdge):
            consumed_by_step[edge.step_id].add(edge.artifact_id)

    internal_dependencies: dict[str, set[str]] = {step_id: set() for step_id in loop_steps}
    external_dependencies: dict[str, set[str]] = {step_id: set() for step_id in loop_steps}
    for step in graph.steps:
        if step.step_id not in loop_steps:
            continue
        for dependency in step.depends_on:
            target = internal_dependencies if dependency in loop_steps else external_dependencies
            target[step.step_id].add(dependency)
        for artifact_id in consumed_by_step[step.step_id]:
            producer = producer_by_artifact.get(artifact_id)
            if producer is None or artifact_id in feedback_ids:
                continue
            target = internal_dependencies if producer in loop_steps else external_dependencies
            target[step.step_id].add(producer)

    epoch_fibers: list[Fiber] = []
    for step_id in sorted(loop_steps):
        awaited = tuple(sorted(internal_dependencies[step_id] | external_dependencies[step_id]))
        instructions: list[PlanInstruction] = []
        if awaited:
            instructions.append(Await(awaited))
        instructions.append(Invoke(step_id))
        epoch_fibers.append(Fiber(step_id, tuple(instructions)))

    top_fibers: list[Fiber] = []
    for step in sorted(graph.steps, key=lambda item: item.step_id):
        if step.step_id in loop_steps:
            continue
        awaited = set(step.depends_on)
        for artifact_id in consumed_by_step[step.step_id]:
            producer = producer_by_artifact.get(artifact_id)
            if producer is not None:
                awaited.add(producer)
        instructions = []
        if awaited:
            instructions.append(Await(tuple(sorted(awaited))))
        instructions.append(Invoke(step.step_id))
        top_fibers.append(Fiber(step.step_id, tuple(instructions)))

    loop_id = loop.terminal_step_id
    return ExecutionPlan(
        workflow_id=graph.workflow_id,
        fibers=tuple(top_fibers),
        loops=(
            LoopRegionPlan(
                loop_id=loop_id,
                feedback_artifact_ids=loop.feedback_artifact_ids,
                step_ids=loop.step_ids,
                terminal_step_id=loop.terminal_step_id,
                terminal_output_artifact_id=loop.terminal_output_artifact_id,
                fibers=tuple(epoch_fibers),
            ),
        ),
    )


def _strongly_connected_steps(dependencies: Mapping[str, set[str]]) -> tuple[frozenset[str], ...]:
    """Return deterministic Tarjan SCCs for the Step dependency graph."""

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    components: list[frozenset[str]] = []

    def visit(step_id: str) -> None:
        nonlocal index
        indices[step_id] = index
        lowlinks[step_id] = index
        index += 1
        stack.append(step_id)
        active.add(step_id)
        for dependency in sorted(dependencies[step_id]):
            if dependency not in indices:
                visit(dependency)
                lowlinks[step_id] = min(lowlinks[step_id], lowlinks[dependency])
            elif dependency in active:
                lowlinks[step_id] = min(lowlinks[step_id], indices[dependency])
        if lowlinks[step_id] != indices[step_id]:
            return
        component: set[str] = set()
        while True:
            member = stack.pop()
            active.remove(member)
            component.add(member)
            if member == step_id:
                break
        components.append(frozenset(component))

    for step_id in sorted(dependencies):
        if step_id not in indices:
            visit(step_id)
    return tuple(sorted(components, key=lambda component: tuple(sorted(component))))


def _step_ancestors(step_id: str, dependencies: Mapping[str, set[str]]) -> set[str]:
    """Return one Step and every producer dependency reachable backwards."""

    result: set[str] = set()
    pending = [step_id]
    while pending:
        candidate = pending.pop()
        if candidate in result:
            continue
        result.add(candidate)
        pending.extend(dependencies[candidate])
    return result


def _step_descendants(source: frozenset[str], dependencies: Mapping[str, set[str]]) -> set[str]:
    """Return Steps reachable forward from any source Step."""

    dependents: dict[str, set[str]] = {step_id: set() for step_id in dependencies}
    for step_id, awaited in dependencies.items():
        for dependency in awaited:
            dependents[dependency].add(step_id)
    result = set(source)
    pending = list(source)
    while pending:
        candidate = pending.pop()
        for dependent in dependents[candidate]:
            if dependent not in result:
                result.add(dependent)
                pending.append(dependent)
    return result


async def execute_plan(
    plan: ExecutionPlan,
    graph: WorkflowGraph,
    *,
    inputs: Mapping[str, object],
    dispatch: StepDispatcher,
    resource_capacities: Mapping[str, ResourceCapacity] | None = None,
    allocator: ResourceAllocator | None = None,
    checkpoint: ExecutionCheckpoint | None = None,
    checkpoint_observer: CheckpointObserver | None = None,
    timing_recorder: Callable[[StepTiming], None] | None = None,
    timing_metadata: Mapping[str, StepTimingMetadata] | None = None,
    max_loop_epochs: int | None = None,
) -> dict[str, object]:
    """Start or resume all fibers and interpret their awaits and invocations."""

    if plan.workflow_id != graph.workflow_id:
        raise ExecutionPlanError(f"plan targets {plan.workflow_id}, not {graph.workflow_id}")
    if resource_capacities is not None and allocator is not None:
        raise ExecutionPlanError("resource_capacities and allocator are mutually exclusive")
    if max_loop_epochs is not None and (type(max_loop_epochs) is not int or max_loop_epochs < 1):
        raise ExecutionPlanError("max_loop_epochs must be a positive integer or None")
    steps = {step.step_id: step for step in graph.steps}
    if (timing_recorder is None) != (timing_metadata is None):
        raise ExecutionPlanError("timing_recorder and timing_metadata must be provided together")
    timing_by_step = dict(timing_metadata or {})
    unknown_timed_steps = timing_by_step.keys() - steps.keys()
    if unknown_timed_steps:
        raise ExecutionPlanError(f"timing metadata contains unknown steps: {sorted(unknown_timed_steps)}")
    if not all(isinstance(metadata, StepTimingMetadata) for metadata in timing_by_step.values()):
        raise ExecutionPlanError("timing_metadata values must be StepTimingMetadata")
    if plan.loops:
        return await _execute_plan_with_loops(
            plan,
            graph,
            inputs=inputs,
            dispatch=dispatch,
            resource_capacities=resource_capacities,
            allocator=allocator,
            checkpoint=checkpoint,
            checkpoint_observer=checkpoint_observer,
            timing_recorder=timing_recorder,
            timing_metadata=timing_by_step,
            max_loop_epochs=max_loop_epochs,
        )

    current_plan_digest = execution_plan_digest(plan, graph)
    expected_inputs = {artifact.artifact_id for artifact in graph.artifacts if artifact.is_input}
    supplied_inputs = set(inputs)
    if supplied_inputs != expected_inputs:
        raise ExecutionPlanError(
            f"workflow inputs must match exactly: expected {sorted(expected_inputs)}, got {sorted(supplied_inputs)}"
        )

    selectors = {selector.output_artifact_id: selector for selector in graph.selectors}
    consumed = {step_id: [] for step_id in steps}
    produced = {step_id: [] for step_id in steps}
    artifacts = {artifact.artifact_id: artifact for artifact in graph.artifacts}
    foreach_by_step = {edge.step_id: edge for edge in graph.edges if isinstance(edge, ForeachEdge)}
    step_producer_by_artifact = {
        edge.artifact_id: edge.step_id for edge in graph.edges if isinstance(edge, ProducesEdge)
    }
    for edge in graph.edges:
        if isinstance(edge, ConsumesEdge):
            if artifacts[edge.artifact_id].binding_step_id is None:
                consumed[edge.step_id].append(edge.artifact_id)
        elif isinstance(edge, ProducesEdge):
            produced[edge.step_id].append(edge.artifact_id)
    required_artifacts_by_step = {
        step_id: [
            *artifact_ids,
            *((foreach_by_step[step_id].artifact_id,) if step_id in foreach_by_step else ()),
        ]
        for step_id, artifact_ids in consumed.items()
    }

    invoked = [
        instruction.step_id
        for fiber in plan.fibers
        for instruction in fiber.instructions
        if isinstance(instruction, Invoke)
    ]
    unknown_invocations = set(invoked) - steps.keys()
    if unknown_invocations:
        raise ExecutionPlanError(f"plan invokes unknown steps: {sorted(unknown_invocations)}")
    if sorted(invoked) != sorted(steps):
        raise ExecutionPlanError("plan must invoke every graph step exactly once")

    selected = [
        instruction.output_artifact_id
        for fiber in plan.fibers
        for instruction in fiber.instructions
        if isinstance(instruction, Select)
    ]
    unknown_selections = set(selected) - selectors.keys()
    if unknown_selections:
        raise ExecutionPlanError(f"plan executes unknown selections: {sorted(unknown_selections)}")
    if sorted(selected) != sorted(selectors):
        raise ExecutionPlanError("plan must execute every graph select exactly once")

    plan_dependencies: dict[OperationId, set[OperationId]] = {("step", step_id): set() for step_id in steps}
    plan_dependencies.update({("select", artifact_id): set() for artifact_id in selectors})
    for fiber in plan.fibers:
        awaited_steps: set[str] = set()
        awaited_selections: set[str] = set()
        invoked_earlier: set[str] = set()
        selected_earlier: set[str] = set()
        for instruction in fiber.instructions:
            if isinstance(instruction, Await):
                unknown = set(instruction.step_ids) - steps.keys()
                if unknown:
                    raise ExecutionPlanError(f"plan awaits unknown steps: {sorted(unknown)}")
                awaited_steps.update(instruction.step_ids)
                continue

            if isinstance(instruction, AwaitSelections):
                unknown = set(instruction.artifact_ids) - selectors.keys()
                if unknown:
                    raise ExecutionPlanError(f"plan awaits unknown selections: {sorted(unknown)}")
                awaited_selections.update(instruction.artifact_ids)
                continue

            satisfied_steps = awaited_steps | invoked_earlier
            satisfied_selections = awaited_selections | selected_earlier
            if isinstance(instruction, Invoke):
                required_steps = set(steps[instruction.step_id].depends_on)
                required_steps.update(
                    step_producer_by_artifact[artifact_id]
                    for artifact_id in required_artifacts_by_step[instruction.step_id]
                    if artifact_id in step_producer_by_artifact
                )
                missing_steps = required_steps - satisfied_steps
                if missing_steps:
                    raise ExecutionPlanError(
                        f"plan is missing dependencies for {instruction.step_id}: {sorted(missing_steps)}"
                    )
                required_selections = {
                    artifact_id
                    for artifact_id in required_artifacts_by_step[instruction.step_id]
                    if artifact_id in selectors
                }
                missing_selections = required_selections - satisfied_selections
                if missing_selections:
                    raise ExecutionPlanError(
                        "plan is missing selection dependencies for "
                        f"{instruction.step_id}: {sorted(missing_selections)}"
                    )
                operation_id = ("step", instruction.step_id)
                invoked_earlier.add(instruction.step_id)
            elif isinstance(instruction, Select):
                selector = selectors[instruction.output_artifact_id]
                required_steps = {
                    step_producer_by_artifact[artifact_id]
                    for artifact_id in selector.input_artifact_ids()
                    if artifact_id in step_producer_by_artifact
                }
                missing_steps = required_steps - satisfied_steps
                if missing_steps:
                    raise ExecutionPlanError(
                        f"plan is missing dependencies for {instruction.output_artifact_id}: {sorted(missing_steps)}"
                    )
                required_selections = {
                    artifact_id for artifact_id in selector.input_artifact_ids() if artifact_id in selectors
                }
                missing_selections = required_selections - satisfied_selections
                if missing_selections:
                    raise ExecutionPlanError(
                        "plan is missing selection dependencies for "
                        f"{instruction.output_artifact_id}: "
                        f"{sorted(missing_selections)}"
                    )
                operation_id = ("select", instruction.output_artifact_id)
                selected_earlier.add(instruction.output_artifact_id)
            else:
                raise ExecutionPlanError(f"plan contains unknown instruction: {type(instruction).__name__}")

            plan_dependencies[operation_id].update(("step", step_id) for step_id in satisfied_steps)
            plan_dependencies[operation_id].update(("select", artifact_id) for artifact_id in satisfied_selections)
    _reject_cycles(plan_dependencies)

    completed_step_ids: set[str] = set()
    completed_selection_ids: set[str] = set()
    foreach_iterations: dict[
        tuple[str, int],
        ForeachIterationCheckpoint,
    ] = {}
    if checkpoint is not None:
        if not isinstance(checkpoint, ExecutionCheckpoint):
            raise ExecutionPlanError("checkpoint must be an ExecutionCheckpoint")
        if checkpoint.workflow_id != graph.workflow_id:
            raise ExecutionPlanError(
                f"checkpoint targets workflow {checkpoint.workflow_id!r}, not {graph.workflow_id!r}"
            )
        if checkpoint.plan_digest != current_plan_digest:
            raise ExecutionPlanError("checkpoint plan digest does not match the current graph and execution plan")
        if len(set(checkpoint.completed_step_ids)) != len(checkpoint.completed_step_ids):
            raise ExecutionPlanError("checkpoint contains duplicate completed step IDs")
        if len(set(checkpoint.completed_selection_ids)) != len(checkpoint.completed_selection_ids):
            raise ExecutionPlanError("checkpoint contains duplicate completed selection IDs")
        if not all(isinstance(step_id, str) and step_id for step_id in checkpoint.completed_step_ids):
            raise ExecutionPlanError("checkpoint completed step IDs must be non-empty strings")
        if not all(isinstance(artifact_id, str) and artifact_id for artifact_id in checkpoint.completed_selection_ids):
            raise ExecutionPlanError("checkpoint completed selection IDs must be non-empty strings")

        completed_step_ids.update(checkpoint.completed_step_ids)
        completed_selection_ids.update(checkpoint.completed_selection_ids)
        foreach_iterations.update(
            {(iteration.step_id, iteration.iteration_index): iteration for iteration in checkpoint.foreach_iterations}
        )
        unknown_completed_steps = completed_step_ids - steps.keys()
        if unknown_completed_steps:
            raise ExecutionPlanError(f"checkpoint contains unknown completed steps: {sorted(unknown_completed_steps)}")
        unknown_completed_selections = completed_selection_ids - selectors.keys()
        if unknown_completed_selections:
            raise ExecutionPlanError(
                f"checkpoint contains unknown completed selections: {sorted(unknown_completed_selections)}"
            )

        completed_operations = {
            *(("step", step_id) for step_id in completed_step_ids),
            *(("select", artifact_id) for artifact_id in completed_selection_ids),
        }
        for operation_id in sorted(completed_operations):
            missing_dependencies = plan_dependencies[operation_id] - completed_operations
            if missing_dependencies:
                formatted_operation = f"{operation_id[0]}:{operation_id[1]}"
                formatted_missing = sorted(f"{kind}:{identity}" for kind, identity in missing_dependencies)
                raise ExecutionPlanError(
                    f"checkpoint is not dependency-closed for {formatted_operation}: missing {formatted_missing}"
                )

        if not all(isinstance(artifact_id, str) for artifact_id in checkpoint.values):
            raise ExecutionPlanError("checkpoint values must have string artifact IDs")
        expected_checkpoint_values = set(expected_inputs)
        expected_checkpoint_values.update(
            artifact_id for step_id in completed_step_ids for artifact_id in produced[step_id]
        )
        expected_checkpoint_values.update(completed_selection_ids)
        actual_checkpoint_values = set(checkpoint.values)
        if actual_checkpoint_values != expected_checkpoint_values:
            raise ExecutionPlanError(
                "checkpoint values must match materialized artifacts exactly: "
                f"expected {sorted(expected_checkpoint_values)}, "
                f"got {sorted(actual_checkpoint_values)}"
            )
        for artifact_id in expected_inputs:
            if not _json_values_equal(checkpoint.values[artifact_id], inputs[artifact_id]):
                raise ExecutionPlanError(f"checkpoint input does not match current input: {artifact_id}")

        for (step_id, iteration_index), iteration in foreach_iterations.items():
            edge = foreach_by_step.get(step_id)
            if edge is None:
                raise ExecutionPlanError(f"checkpoint contains iteration for non-foreach step: {step_id}")
            if iteration.attempts > steps[step_id].max_attempts:
                raise ExecutionPlanError(
                    f"checkpoint iteration attempts exceed max_attempts: {step_id}[{iteration_index}]"
                )
            if iteration.error is not None and iteration.attempts != steps[step_id].max_attempts:
                raise ExecutionPlanError(
                    f"checkpoint failed iteration has not exhausted max_attempts: {step_id}[{iteration_index}]"
                )
            try:
                source = checkpoint.values[edge.artifact_id]
            except KeyError:
                raise ExecutionPlanError(f"checkpoint foreach source is unavailable: {edge.artifact_id}") from None
            if not isinstance(source, list):
                raise ExecutionPlanError(f"foreach source {edge.artifact_id!r} must be a List")
            if iteration_index >= len(source):
                raise ExecutionPlanError(
                    f"checkpoint foreach iteration index is out of range: {step_id}[{iteration_index}]"
                )
            if iteration.outputs is not None and set(iteration.outputs) != set(produced[step_id]):
                raise ExecutionPlanError(
                    f"checkpoint foreach outputs for {step_id}[{iteration_index}] "
                    f"must match exactly: expected {sorted(produced[step_id])}, "
                    f"got {sorted(iteration.outputs)}"
                )

            dependency_operations = plan_dependencies[("step", step_id)]
            completed_operations = {
                *(("step", completed_step_id) for completed_step_id in completed_step_ids),
                *(("select", artifact_id) for artifact_id in completed_selection_ids),
            }
            missing_dependencies = dependency_operations - completed_operations
            if missing_dependencies:
                formatted_missing = sorted(f"{kind}:{identity}" for kind, identity in missing_dependencies)
                raise ExecutionPlanError(
                    f"checkpoint foreach iteration is not dependency-closed for "
                    f"{step_id}[{iteration_index}]: missing {formatted_missing}"
                )

        for step_id in sorted(completed_step_ids & foreach_by_step.keys()):
            edge = foreach_by_step[step_id]
            source = checkpoint.values[edge.artifact_id]
            if not isinstance(source, list):
                raise ExecutionPlanError(f"foreach source {edge.artifact_id!r} must be a List")
            expected_indices = set(range(len(source)))
            actual_indices = {
                iteration_index
                for candidate_step_id, iteration_index in foreach_iterations
                if candidate_step_id == step_id
            }
            if actual_indices != expected_indices:
                raise ExecutionPlanError(
                    f"completed foreach checkpoint for {step_id!r} must contain "
                    f"every iteration: expected {sorted(expected_indices)}, "
                    f"got {sorted(actual_indices)}"
                )
            iteration_records = [
                foreach_iterations[(step_id, iteration_index)] for iteration_index in range(len(source))
            ]
            if any(iteration.outputs is None for iteration in iteration_records):
                raise ExecutionPlanError(f"completed foreach checkpoint for {step_id!r} contains a failed iteration")
            rebuilt_aggregates = {
                artifact_id: [
                    cast(dict[str, object], iteration.outputs)[artifact_id] for iteration in iteration_records
                ]
                for artifact_id in produced[step_id]
            }
            for artifact_id, rebuilt_value in rebuilt_aggregates.items():
                if not _json_values_equal(
                    checkpoint.values[artifact_id],
                    rebuilt_value,
                ):
                    raise ExecutionPlanError(
                        f"completed foreach checkpoint aggregate {artifact_id!r} "
                        f"does not match terminal iterations for step {step_id!r}"
                    )

    requirements_by_step = {
        step.step_id: step.resources for step in graph.steps if step.step_id not in completed_step_ids
    }
    has_resources = any(requirements_by_step.values())
    if allocator is None:
        if resource_capacities is None:
            if has_resources:
                raise ExecutionPlanError("resource capacities or an allocator are required")
            allocator = ResourceAllocator({})
        else:
            allocator = ResourceAllocator(resource_capacities)
    await allocator.preflight(requirements_by_step)

    values = dict(inputs if checkpoint is None else checkpoint.values)
    completed_steps = {step_id: anyio.Event() for step_id in steps}
    completed_selections = {artifact_id: anyio.Event() for artifact_id in selectors}
    for step_id in completed_step_ids:
        completed_steps[step_id].set()
    for artifact_id in completed_selection_ids:
        completed_selections[artifact_id].set()
    checkpoint_lock = anyio.Lock()
    capacity = graph.policy.max_concurrency
    admission_state = _AdmissionState(
        max_concurrency=capacity,
    )

    async def invoke_step(
        step: StepNode,
        step_inputs: Mapping[str, object],
        *,
        iteration_index: int | None = None,
        attempt_timings: list[AttemptTiming] | None = None,
    ) -> tuple[dict[str, object], int]:
        """Invoke and validate one logical StepInstance with per-attempt leases."""

        invocation_id = step.step_id if iteration_index is None else f"{step.step_id}[{iteration_index}]"

        async def call_dispatcher(
            context: DispatchContext,
            invocation_inputs: Mapping[str, object],
        ) -> Mapping[str, object]:
            return await dispatch(step, invocation_inputs, context)

        async def run_attempt(attempt: int) -> dict[str, object]:
            """Adapt one graph Step attempt to the shared Flow retry kernel."""

            async with allocator._admit(
                step.resources,
                state=admission_state,
            ) as resource_lease:
                logger.debug(f"Dispatching workflow step: {invocation_id} (attempt {attempt}/{step.max_attempts})")
                context = DispatchContext(
                    resource_lease=resource_lease,
                    invocation_id=invocation_id,
                    iteration_index=iteration_index,
                    attempt=attempt,
                )
                attempt_inputs = (
                    _copy_json_mapping(
                        step_inputs,
                        context=f"inputs for {invocation_id} attempt {attempt}",
                    )
                    if iteration_index is not None
                    else step_inputs
                )
                timing_start = _start_timing(enabled=attempt_timings is not None)
                try:
                    if step.timeout_seconds is None:
                        outputs = await call_dispatcher(
                            context,
                            attempt_inputs,
                        )
                    else:
                        with anyio.fail_after(step.timeout_seconds):
                            outputs = await call_dispatcher(
                                context,
                                attempt_inputs,
                            )
                    validated = _validate_step_outputs(
                        step.step_id,
                        outputs,
                        expected_output_ids=produced[step.step_id],
                    )
                except BaseException as error:
                    _append_attempt_timing(
                        attempt_timings,
                        timing_start=timing_start,
                        attempt=attempt,
                        status=_timing_status(error),
                        error_type=type(error).__name__,
                    )
                    raise
                _append_attempt_timing(
                    attempt_timings,
                    timing_start=timing_start,
                    attempt=attempt,
                    status="ok",
                    error_type=None,
                )
                return validated

        def should_retry(error: Exception, attempt: int) -> bool:
            """Retry ordinary executor/output failures, never graph control."""

            del attempt
            return _is_ordinary_step_error(error)

        def warn_retry(error: Exception, attempt: int) -> None:
            """Keep the graph-specific retry diagnostic outside the kernel."""

            del attempt
            logger.warning(f"Retrying workflow step {invocation_id} after {type(error).__name__}: {error}")

        return await _retry_operation(
            run_attempt,
            max_attempts=step.max_attempts,
            initial_delay=0,
            backoff_factor=1,
            max_delay=0,
            should_retry=should_retry,
            on_retry=warn_retry,
        )

    def checkpoint_iterations() -> tuple[ForeachIterationCheckpoint, ...]:
        """Return the current terminal iteration records in stable order."""

        return tuple(iteration for _, iteration in sorted(foreach_iterations.items()))

    async def observe_checkpoint() -> None:
        """Persist one snapshot; the caller must hold checkpoint_lock."""

        if checkpoint_observer is None:
            return
        await checkpoint_observer(
            create_execution_checkpoint(
                plan,
                graph,
                values=values,
                completed_step_ids=tuple(sorted(completed_step_ids)),
                completed_selection_ids=tuple(sorted(completed_selection_ids)),
                foreach_iterations=checkpoint_iterations(),
            )
        )

    async def commit_step_outputs(
        step_id: str,
        outputs: Mapping[str, object],
    ) -> None:
        """Atomically materialize one logical step and checkpoint its completion."""

        if checkpoint_observer is None:
            values.update(outputs)
            completed_step_ids.add(step_id)
            return
        async with checkpoint_lock:
            values.update(outputs)
            completed_step_ids.add(step_id)
            await observe_checkpoint()

    async def commit_iteration(
        iteration: ForeachIterationCheckpoint,
    ) -> None:
        """Record one terminal iteration before other iterations finish."""

        identity = (iteration.step_id, iteration.iteration_index)
        if checkpoint_observer is None:
            foreach_iterations[identity] = iteration
            return
        async with checkpoint_lock:
            foreach_iterations[identity] = iteration
            await observe_checkpoint()

    async def invoke_foreach_step(
        step: StepNode,
        *,
        iteration_timings: dict[int, IterationTiming] | None = None,
        iteration_finished: dict[int, float] | None = None,
    ) -> dict[str, object]:
        """Expand, execute, and deterministically collect one foreach step."""

        edge = foreach_by_step[step.step_id]
        try:
            source = values[edge.artifact_id]
        except KeyError:
            raise ExecutionPlanError(f"foreach source artifact is unavailable: {edge.artifact_id}") from None
        if not isinstance(source, list):
            raise ExecutionPlanError(f"foreach source {edge.artifact_id!r} must be a List")
        source = cast(
            list[object],
            _copy_json_value(
                source,
                context=f"foreach source {edge.artifact_id}",
                active=set(),
            ),
        )

        pending = tuple(
            (iteration_index, item)
            for iteration_index, item in enumerate(source)
            if (
                (iteration := foreach_iterations.get((step.step_id, iteration_index))) is None
                or iteration.outputs is None
            )
        )
        iteration_failures: dict[int, Exception] = {}

        async def run_iteration(
            iteration_index: int,
            item: object,
        ) -> ForeachIterationCheckpoint:
            """Execute and checkpoint one expanded StepInstance."""

            timing_start = _start_timing(enabled=iteration_timings is not None)
            attempt_timings: list[AttemptTiming] | None = [] if iteration_timings is not None else None
            try:
                step_inputs = {artifact_id: values[artifact_id] for artifact_id in consumed[step.step_id]}
            except KeyError as error:
                raise ExecutionPlanError(
                    f"foreach step {step.step_id!r} input artifact is unavailable: {error.args[0]!r}"
                ) from None
            step_inputs[edge.item_binding_id] = item
            step_inputs = _copy_json_mapping(
                step_inputs,
                context=f"inputs for {step.step_id}[{iteration_index}]",
            )
            try:
                outputs, attempts = await invoke_step(
                    step,
                    step_inputs,
                    iteration_index=iteration_index,
                    attempt_timings=attempt_timings,
                )
            except BaseException as error:
                if not isinstance(error, Exception) or not _is_ordinary_step_error(error):
                    _store_iteration_timing(
                        iteration_timings,
                        iteration_finished,
                        timing_start=timing_start,
                        iteration_index=iteration_index,
                        status=_timing_status(error),
                        error_type=type(error).__name__,
                        attempt_timings=attempt_timings,
                    )
                    raise
                iteration_failures[iteration_index] = error
                iteration = _failed_iteration(
                    step.step_id,
                    iteration_index,
                    step.max_attempts,
                    error,
                )
            else:
                iteration = ForeachIterationCheckpoint(
                    step_id=step.step_id,
                    iteration_index=iteration_index,
                    attempts=attempts,
                    outputs=outputs,
                )
            failure = iteration_failures.get(iteration_index)
            _store_iteration_timing(
                iteration_timings,
                iteration_finished,
                timing_start=timing_start,
                iteration_index=iteration_index,
                status="error" if failure is not None else "ok",
                error_type=type(failure).__name__ if failure is not None else None,
                attempt_timings=attempt_timings,
            )
            await commit_iteration(iteration)
            return iteration

        tasks: list[Callable[[], Awaitable[ForeachIterationCheckpoint]]] = []
        for iteration_index, item in pending:

            async def visit(
                iteration_index: int = iteration_index,
                item: object = item,
            ) -> ForeachIterationCheckpoint:
                return await run_iteration(iteration_index, item)

            tasks.append(visit)

        await _run_parallel_tasks(
            tasks,
            join="all",
            required=len(tasks),
            max_concurrency=capacity,
        )

        iteration_records = [
            foreach_iterations[(step.step_id, iteration_index)] for iteration_index in range(len(source))
        ]
        failed = [iteration for iteration in iteration_records if iteration.error is not None]
        if failed:
            raise ExceptionGroup(
                f"foreach step {step.step_id!r} failed",
                [iteration_failures[iteration.iteration_index] for iteration in failed],
            )
        return {
            artifact_id: [cast(dict[str, object], iteration.outputs)[artifact_id] for iteration in iteration_records]
            for artifact_id in produced[step.step_id]
        }

    async def run_fiber(fiber: Fiber) -> None:
        for instruction in fiber.instructions:
            if isinstance(instruction, Await):
                for step_id in instruction.step_ids:
                    event = completed_steps.get(step_id)
                    if event is None:
                        raise ExecutionPlanError(f"plan awaits unknown step: {step_id}")
                    await event.wait()
                continue

            if isinstance(instruction, AwaitSelections):
                for artifact_id in instruction.artifact_ids:
                    event = completed_selections.get(artifact_id)
                    if event is None:
                        raise ExecutionPlanError(f"plan awaits unknown selection: {artifact_id}")
                    await event.wait()
                continue

            if isinstance(instruction, Invoke):
                if instruction.step_id in completed_step_ids:
                    continue
                step = steps.get(instruction.step_id)
                if step is None:
                    raise ExecutionPlanError(f"plan invokes unknown step: {instruction.step_id}")
                metadata = timing_by_step.get(step.step_id)
                attempt_timings: list[AttemptTiming] | None = [] if metadata is not None else None
                iteration_timings: dict[int, IterationTiming] | None = {} if metadata is not None else None
                iteration_finished: dict[int, float] | None = {} if metadata is not None else None
                timing_start = _start_timing(enabled=metadata is not None)
                is_foreach = step.step_id in foreach_by_step
                try:
                    if is_foreach:
                        outputs = await invoke_foreach_step(
                            step,
                            iteration_timings=iteration_timings,
                            iteration_finished=iteration_finished,
                        )
                    else:
                        step_inputs = {artifact_id: values[artifact_id] for artifact_id in consumed[step.step_id]}
                        outputs, _ = await invoke_step(
                            step,
                            step_inputs,
                            attempt_timings=attempt_timings,
                        )
                except BaseException as error:
                    if metadata is not None:
                        _record_step_timing(
                            timing_recorder,
                            timing_start=timing_start,
                            step=step,
                            metadata=metadata,
                            is_foreach=is_foreach,
                            status=_timing_status(error),
                            error_type=type(error).__name__,
                            attempt_timings=attempt_timings,
                            iteration_timings=iteration_timings,
                            iteration_finished=iteration_finished,
                        )
                    raise

                if metadata is not None:
                    _record_step_timing(
                        timing_recorder,
                        timing_start=timing_start,
                        step=step,
                        metadata=metadata,
                        is_foreach=is_foreach,
                        status="ok",
                        error_type=None,
                        attempt_timings=attempt_timings,
                        iteration_timings=iteration_timings,
                        iteration_finished=iteration_finished,
                    )
                await commit_step_outputs(step.step_id, outputs)
                completed_steps[step.step_id].set()
                logger.debug(f"Completed workflow step: {step.step_id}")
                continue

            if isinstance(instruction, Select):
                if instruction.output_artifact_id in completed_selection_ids:
                    continue
                selector = selectors.get(instruction.output_artifact_id)
                if selector is None:
                    raise ExecutionPlanError(f"plan executes unknown selection: {instruction.output_artifact_id}")
                logger.debug(f"Evaluating workflow select: {selector.output_artifact_id}")
                candidate_artifact_id = (
                    selector.when_true_artifact_id
                    if _evaluate_condition(selector.condition, values)
                    else selector.when_false_artifact_id
                )
                try:
                    selected_value = values[candidate_artifact_id]
                except KeyError:
                    raise ExecutionPlanError(
                        f"select {selector.output_artifact_id} is missing candidate artifact: {candidate_artifact_id}"
                    ) from None
                if checkpoint_observer is None:
                    values[selector.output_artifact_id] = selected_value
                    completed_selection_ids.add(selector.output_artifact_id)
                else:
                    async with checkpoint_lock:
                        values[selector.output_artifact_id] = selected_value
                        completed_selection_ids.add(selector.output_artifact_id)
                        await observe_checkpoint()
                completed_selections[selector.output_artifact_id].set()
                logger.debug(f"Completed workflow select: {selector.output_artifact_id}")
                continue

            raise ExecutionPlanError(f"plan contains unknown instruction: {type(instruction).__name__}")

    async def run_fibers() -> None:
        async with anyio.create_task_group() as task_group:
            for fiber in plan.fibers:
                task_group.start_soon(run_fiber, fiber)

    if graph.policy.timeout_seconds is None:
        await run_fibers()
    else:
        with anyio.fail_after(graph.policy.timeout_seconds):
            await run_fibers()

    return {artifact.artifact_id: values[artifact.artifact_id] for artifact in graph.artifacts if artifact.is_output}


async def _execute_plan_with_loops(
    plan: ExecutionPlan,
    graph: WorkflowGraph,
    *,
    inputs: Mapping[str, object],
    dispatch: StepDispatcher,
    resource_capacities: Mapping[str, ResourceCapacity] | None,
    allocator: ResourceAllocator | None,
    checkpoint: ExecutionCheckpoint | None,
    checkpoint_observer: CheckpointObserver | None,
    timing_recorder: Callable[[StepTiming], None] | None,
    timing_metadata: Mapping[str, StepTimingMetadata],
    max_loop_epochs: int | None,
) -> dict[str, object]:
    """Execute repeatable epoch plans with snapshot reads and barrier commits.

    The first implementation deliberately keeps loops out of foreach/select;
    those constructs need their own epoch-scoped checkpoint identities.  The
    acyclic executor remains unchanged for plans without ``plan.loops``.
    """

    if graph.selectors:
        raise ExecutionPlanError("feedback loops do not yet support select operations")
    if any(isinstance(edge, ForeachEdge) for edge in graph.edges):
        raise ExecutionPlanError("feedback loops do not yet support foreach operations")

    current_plan_digest = execution_plan_digest(plan, graph)
    expected_inputs = {artifact.artifact_id for artifact in graph.artifacts if artifact.is_input}
    supplied_inputs = set(inputs)
    if supplied_inputs != expected_inputs:
        raise ExecutionPlanError(
            f"workflow inputs must match exactly: expected {sorted(expected_inputs)}, got {sorted(supplied_inputs)}"
        )

    steps = {step.step_id: step for step in graph.steps}
    artifacts = {artifact.artifact_id: artifact for artifact in graph.artifacts}
    consumed = {step_id: [] for step_id in steps}
    produced = {step_id: [] for step_id in steps}
    for edge in graph.edges:
        if isinstance(edge, ConsumesEdge):
            if artifacts[edge.artifact_id].binding_step_id is None:
                consumed[edge.step_id].append(edge.artifact_id)
        elif isinstance(edge, ProducesEdge):
            produced[edge.step_id].append(edge.artifact_id)
    producer_by_artifact = {
        artifact_id: step_id for step_id, artifact_ids in produced.items() for artifact_id in artifact_ids
    }

    loop_by_id: dict[str, LoopRegionPlan] = {}
    loop_by_step: dict[str, LoopRegionPlan] = {}
    for loop in plan.loops:
        if not isinstance(loop.loop_id, str) or not loop.loop_id:
            raise ExecutionPlanError("loop_id must be a non-empty string")
        if loop.loop_id in loop_by_id:
            raise ExecutionPlanError(f"duplicate loop_id: {loop.loop_id}")
        loop_by_id[loop.loop_id] = loop
        loop_steps = set(loop.step_ids)
        if not loop_steps:
            raise ExecutionPlanError(f"loop {loop.loop_id!r} has no steps")
        unknown_steps = loop_steps - steps.keys()
        if unknown_steps:
            raise ExecutionPlanError(f"loop {loop.loop_id!r} contains unknown steps: {sorted(unknown_steps)}")
        if loop.terminal_step_id not in loop_steps:
            raise ExecutionPlanError(f"loop {loop.loop_id!r} terminal step is not a member: {loop.terminal_step_id}")
        terminal = steps[loop.terminal_step_id]
        if getattr(terminal, "step_type", "Step") != "TerminalStep":
            raise ExecutionPlanError(f"loop {loop.loop_id!r} terminal {loop.terminal_step_id!r} must be a TerminalStep")
        terminal_outputs = produced[loop.terminal_step_id]
        if terminal_outputs != [loop.terminal_output_artifact_id]:
            raise ExecutionPlanError(
                f"TerminalStep {loop.terminal_step_id!r} must produce exactly {loop.terminal_output_artifact_id!r}"
            )
        terminal_artifact = artifacts.get(loop.terminal_output_artifact_id)
        if terminal_artifact is None or getattr(terminal_artifact, "artifact_type", "Artifact") != "BoolArtifact":
            raise ExecutionPlanError(f"TerminalStep {loop.terminal_step_id!r} output must be a BoolArtifact")
        feedback_ids = set(loop.feedback_artifact_ids)
        if not feedback_ids:
            raise ExecutionPlanError(f"loop {loop.loop_id!r} has no feedback artifacts")
        unknown_feedback = feedback_ids - artifacts.keys()
        if unknown_feedback:
            raise ExecutionPlanError(
                f"loop {loop.loop_id!r} contains unknown feedback artifacts: {sorted(unknown_feedback)}"
            )
        for step_id in loop.step_ids:
            previous = loop_by_step.get(step_id)
            if previous is not None:
                raise ExecutionPlanError(
                    f"step {step_id!r} belongs to multiple loops: {previous.loop_id!r}, {loop.loop_id!r}"
                )
            loop_by_step[step_id] = loop
        invoked = [
            instruction.step_id
            for fiber in loop.fibers
            for instruction in fiber.instructions
            if isinstance(instruction, Invoke)
        ]
        if sorted(invoked) != sorted(loop.step_ids):
            raise ExecutionPlanError(f"loop {loop.loop_id!r} must invoke every member step exactly once per epoch")

    top_invoked = [
        instruction.step_id
        for fiber in plan.fibers
        for instruction in fiber.instructions
        if isinstance(instruction, Invoke)
    ]
    expected_top_steps = steps.keys() - loop_by_step.keys()
    if sorted(top_invoked) != sorted(expected_top_steps):
        raise ExecutionPlanError("top-level plan must invoke every non-loop step exactly once")

    plan_dependencies: dict[OperationId, set[OperationId]] = {("step", step_id): set() for step_id in steps}

    def validate_dependency_coverage(
        fibers: Sequence[Fiber],
        *,
        loop: LoopRegionPlan | None,
    ) -> None:
        loop_steps = set() if loop is None else set(loop.step_ids)
        feedback_ids = set() if loop is None else set(loop.feedback_artifact_ids)
        scope = "top-level plan" if loop is None else f"loop {loop.loop_id!r} plan"
        for fiber in fibers:
            awaited_steps: set[str] = set()
            invoked_earlier: set[str] = set()
            for instruction in fiber.instructions:
                if isinstance(instruction, Await):
                    unknown_steps = set(instruction.step_ids) - steps.keys()
                    if unknown_steps:
                        raise ExecutionPlanError(f"{scope} awaits unknown steps: {sorted(unknown_steps)}")
                    awaited_steps.update(instruction.step_ids)
                    continue
                if not isinstance(instruction, Invoke):
                    continue
                satisfied_steps = awaited_steps | invoked_earlier
                required_steps = set(steps[instruction.step_id].depends_on)
                for artifact_id in consumed[instruction.step_id]:
                    producer_id = producer_by_artifact.get(artifact_id)
                    if producer_id is None:
                        continue
                    if loop is not None and artifact_id in feedback_ids and producer_id in loop_steps:
                        # Feedback reads are A_n snapshot reads.  Their loop
                        # producer stages A_(n+1), so it is not a same-epoch
                        # prerequisite and must not require an Await.
                        continue
                    required_steps.add(producer_id)
                missing_steps = required_steps - satisfied_steps
                if missing_steps:
                    raise ExecutionPlanError(
                        f"{scope} is missing dependencies for {instruction.step_id}: {sorted(missing_steps)}"
                    )
                plan_dependencies[("step", instruction.step_id)].update(
                    ("step", step_id) for step_id in satisfied_steps
                )
                invoked_earlier.add(instruction.step_id)

    validate_dependency_coverage(plan.fibers, loop=None)
    for loop in plan.loops:
        validate_dependency_coverage(loop.fibers, loop=loop)
    _reject_cycles(plan_dependencies)

    checkpoint_dependencies: dict[str, set[str]] = {step.step_id: set(step.depends_on) for step in graph.steps}
    for step_id, artifact_ids in consumed.items():
        checkpoint_dependencies[step_id].update(
            producer_by_artifact[artifact_id] for artifact_id in artifact_ids if artifact_id in producer_by_artifact
        )
    for (_, step_id), dependencies in plan_dependencies.items():
        checkpoint_dependencies[step_id].update(dependency_id for _, dependency_id in dependencies)

    completed_step_ids: set[str] = set()
    loop_checkpoints: dict[str, LoopExecutionCheckpoint] = {}
    if checkpoint is None:
        values = _copy_json_mapping(inputs, context="workflow inputs")
    else:
        if not isinstance(checkpoint, ExecutionCheckpoint):
            raise ExecutionPlanError("checkpoint must be an ExecutionCheckpoint")
        if checkpoint.workflow_id != graph.workflow_id:
            raise ExecutionPlanError(
                f"checkpoint targets workflow {checkpoint.workflow_id!r}, not {graph.workflow_id!r}"
            )
        if checkpoint.plan_digest != current_plan_digest:
            raise ExecutionPlanError("checkpoint plan digest does not match the current graph and execution plan")
        if checkpoint.completed_selection_ids or checkpoint.foreach_iterations:
            raise ExecutionPlanError("loop checkpoint cannot contain select or foreach completion records")
        completed_step_ids.update(checkpoint.completed_step_ids)
        unknown_completed = completed_step_ids - steps.keys()
        if unknown_completed:
            raise ExecutionPlanError(f"checkpoint contains unknown completed steps: {sorted(unknown_completed)}")
        loop_checkpoints.update({item.loop_id: item for item in checkpoint.loops})
        unknown_loops = loop_checkpoints.keys() - loop_by_id.keys()
        if unknown_loops:
            raise ExecutionPlanError(f"checkpoint contains unknown loops: {sorted(unknown_loops)}")
        values = _copy_json_mapping(checkpoint.values, context="checkpoint values")

        for loop in plan.loops:
            loop_steps = set(loop.step_ids)
            completed_loop_steps = completed_step_ids & loop_steps
            if completed_loop_steps and completed_loop_steps != loop_steps:
                raise ExecutionPlanError(
                    f"checkpoint loop {loop.loop_id!r} completion must be all-or-none: "
                    f"got {sorted(completed_loop_steps)}"
                )
            saved = loop_checkpoints.get(loop.loop_id)
            if completed_loop_steps and saved is None:
                raise ExecutionPlanError(f"completed checkpoint loop {loop.loop_id!r} has no committed loop state")
            if saved is None:
                continue
            feedback_ids = set(loop.feedback_artifact_ids)
            if saved.epoch < 1:
                raise ExecutionPlanError(
                    f"checkpoint loop {loop.loop_id!r} epoch must identify at least one committed barrier"
                )
            if set(saved.current_values) != feedback_ids:
                raise ExecutionPlanError(
                    f"checkpoint loop {loop.loop_id!r} current values must match feedback artifacts exactly: "
                    f"expected {sorted(feedback_ids)}, got {sorted(saved.current_values)}"
                )
            if saved.staged_values or saved.completed_step_ids:
                raise ExecutionPlanError(
                    f"checkpoint loop {loop.loop_id!r} must be an epoch barrier without staged values "
                    "or inflight completed steps"
                )
            required_external_steps = {
                dependency for step_id in loop.step_ids for dependency in checkpoint_dependencies[step_id] - loop_steps
            }
            missing_external_steps = required_external_steps - completed_step_ids
            if missing_external_steps:
                raise ExecutionPlanError(
                    f"checkpoint loop {loop.loop_id!r} is missing completed external dependencies: "
                    f"{sorted(missing_external_steps)}"
                )
            for artifact_id in loop.feedback_artifact_ids:
                if artifact_id not in values or not _json_values_equal(
                    values[artifact_id],
                    saved.current_values[artifact_id],
                ):
                    raise ExecutionPlanError(
                        f"checkpoint loop {loop.loop_id!r} current value does not match "
                        f"materialized feedback artifact: {artifact_id}"
                    )
            if completed_loop_steps:
                terminal_value = values.get(loop.terminal_output_artifact_id)
                if terminal_value is not True:
                    raise ExecutionPlanError(
                        f"completed checkpoint loop {loop.loop_id!r} must materialize a true TerminalStep result"
                    )

        for step_id in sorted(completed_step_ids):
            missing_dependencies = checkpoint_dependencies[step_id] - completed_step_ids
            if missing_dependencies:
                raise ExecutionPlanError(
                    f"loop checkpoint is not dependency-closed for {step_id}: missing {sorted(missing_dependencies)}"
                )

        expected_checkpoint_values = set(expected_inputs)
        expected_checkpoint_values.update(
            artifact_id for step_id in completed_step_ids - loop_by_step.keys() for artifact_id in produced[step_id]
        )
        for loop in plan.loops:
            if set(loop.step_ids) <= completed_step_ids:
                expected_checkpoint_values.update(
                    artifact_id for step_id in loop.step_ids for artifact_id in produced[step_id]
                )
            elif loop.loop_id in loop_checkpoints:
                expected_checkpoint_values.update(loop.feedback_artifact_ids)
        actual_checkpoint_values = set(values)
        if actual_checkpoint_values != expected_checkpoint_values:
            raise ExecutionPlanError(
                "loop checkpoint values must match materialized artifacts exactly: "
                f"expected {sorted(expected_checkpoint_values)}, "
                f"got {sorted(actual_checkpoint_values)}"
            )

        advanced_feedback_ids = {
            artifact_id
            for loop in plan.loops
            if loop.loop_id in loop_checkpoints
            for artifact_id in loop.feedback_artifact_ids
        }
        for artifact_id in expected_inputs - advanced_feedback_ids:
            if artifact_id not in values or not _json_values_equal(values[artifact_id], inputs[artifact_id]):
                raise ExecutionPlanError(f"checkpoint input does not match current input: {artifact_id}")

    if allocator is None:
        allocator = ResourceAllocator(resource_capacities or {})
    await allocator.preflight(
        {step.step_id: step.resources for step in graph.steps if step.step_id not in completed_step_ids}
    )
    admission_state = _AdmissionState(max_concurrency=graph.policy.max_concurrency)
    completed_steps = {step_id: anyio.Event() for step_id in steps}
    for step_id in completed_step_ids:
        completed_steps[step_id].set()
    commit_lock = anyio.Lock()

    def checkpoint_loops() -> tuple[LoopExecutionCheckpoint, ...]:
        return tuple(loop for _, loop in sorted(loop_checkpoints.items()))

    async def persist_candidate(
        candidate_values: Mapping[str, object],
        candidate_completed: set[str],
        candidate_loops: Mapping[str, LoopExecutionCheckpoint],
    ) -> None:
        if checkpoint_observer is None:
            return
        await checkpoint_observer(
            create_execution_checkpoint(
                plan,
                graph,
                values=candidate_values,
                completed_step_ids=tuple(sorted(candidate_completed)),
                loops=tuple(loop for _, loop in sorted(candidate_loops.items())),
            )
        )

    async def invoke(
        step: StepNode,
        step_inputs: Mapping[str, object],
        *,
        loop_id: str | None = None,
        epoch: int | None = None,
    ) -> dict[str, object]:
        invocation_id = step.step_id if loop_id is None else f"{loop_id}@{epoch}/{step.step_id}"
        metadata = timing_metadata.get(step.step_id)
        attempt_timings: list[AttemptTiming] | None = [] if metadata is not None else None
        timing_start = _start_timing(enabled=metadata is not None)

        async def run_attempt(attempt: int) -> dict[str, object]:
            async with allocator._admit(step.resources, state=admission_state) as resource_lease:
                logger.debug(f"Dispatching workflow step: {invocation_id} (attempt {attempt}/{step.max_attempts})")
                context = DispatchContext(
                    resource_lease=resource_lease,
                    invocation_id=invocation_id,
                    attempt=attempt,
                    loop_id=loop_id,
                    epoch=epoch,
                )
                attempt_start = _start_timing(enabled=attempt_timings is not None)
                try:
                    if step.timeout_seconds is None:
                        outputs = await dispatch(step, step_inputs, context)
                    else:
                        with anyio.fail_after(step.timeout_seconds):
                            outputs = await dispatch(step, step_inputs, context)
                    validated = _validate_step_outputs(
                        step.step_id,
                        outputs,
                        expected_output_ids=produced[step.step_id],
                    )
                except BaseException as error:
                    _append_attempt_timing(
                        attempt_timings,
                        timing_start=attempt_start,
                        attempt=attempt,
                        status=_timing_status(error),
                        error_type=type(error).__name__,
                    )
                    raise
                _append_attempt_timing(
                    attempt_timings,
                    timing_start=attempt_start,
                    attempt=attempt,
                    status="ok",
                    error_type=None,
                )
                return validated

        try:
            result = (
                await _retry_operation(
                    run_attempt,
                    max_attempts=step.max_attempts,
                    initial_delay=0,
                    backoff_factor=1,
                    max_delay=0,
                    should_retry=lambda error, attempt: _is_ordinary_step_error(error),
                    on_retry=lambda error, attempt: logger.warning(
                        f"Retrying workflow step {invocation_id} after {type(error).__name__}: {error}"
                    ),
                )
            )[0]
        except BaseException as error:
            if metadata is not None:
                _record_step_timing(
                    timing_recorder,
                    timing_start=timing_start,
                    step=replace(step, step_id=invocation_id),
                    metadata=metadata,
                    is_foreach=False,
                    status=_timing_status(error),
                    error_type=type(error).__name__,
                    attempt_timings=attempt_timings,
                    iteration_timings=None,
                    iteration_finished=None,
                )
            raise
        if metadata is not None:
            _record_step_timing(
                timing_recorder,
                timing_start=timing_start,
                step=replace(step, step_id=invocation_id),
                metadata=metadata,
                is_foreach=False,
                status="ok",
                error_type=None,
                attempt_timings=attempt_timings,
                iteration_timings=None,
                iteration_finished=None,
            )
        return result

    async def run_top_fiber(fiber: Fiber) -> None:
        for instruction in fiber.instructions:
            if isinstance(instruction, Await):
                for step_id in instruction.step_ids:
                    try:
                        event = completed_steps[step_id]
                    except KeyError:
                        raise ExecutionPlanError(f"plan awaits unknown step: {step_id}") from None
                    await event.wait()
                continue
            if isinstance(instruction, AwaitSelections | Select):
                raise ExecutionPlanError("feedback loop plans do not yet support select operations")
            if not isinstance(instruction, Invoke):
                raise ExecutionPlanError(f"plan contains unknown instruction: {type(instruction).__name__}")
            step_id = instruction.step_id
            if step_id in completed_step_ids:
                continue
            if step_id in loop_by_step:
                raise ExecutionPlanError(f"top-level plan invokes loop step directly: {step_id}")
            step = steps[step_id]
            if getattr(step, "step_type", "Step") == "TerminalStep":
                raise ExecutionPlanError(f"TerminalStep is not assigned to a feedback loop: {step_id}")
            try:
                step_inputs = {artifact_id: values[artifact_id] for artifact_id in consumed[step_id]}
            except KeyError as error:
                raise ExecutionPlanError(f"step {step_id!r} input artifact is unavailable: {error.args[0]!r}") from None
            outputs = await invoke(step, step_inputs)
            async with commit_lock:
                candidate_values = {**values, **outputs}
                candidate_completed = {*completed_step_ids, step_id}
                await persist_candidate(candidate_values, candidate_completed, loop_checkpoints)
                values.update(outputs)
                completed_step_ids.add(step_id)
            completed_steps[step_id].set()
            logger.debug(f"Completed workflow step: {step_id}")

    async def run_loop(loop: LoopRegionPlan) -> None:
        if set(loop.step_ids) <= completed_step_ids:
            for step_id in loop.step_ids:
                completed_steps[step_id].set()
            return
        saved = loop_checkpoints.get(loop.loop_id)
        epoch = 0 if saved is None else saved.epoch
        feedback_ids = set(loop.feedback_artifact_ids)
        if saved is None:
            for artifact_id in loop.feedback_artifact_ids:
                if artifact_id in values:
                    continue
                initializer_id = producer_by_artifact.get(artifact_id)
                if initializer_id is None or initializer_id in loop_by_step:
                    raise ExecutionPlanError(
                        f"loop {loop.loop_id!r} has no independent initial value for feedback artifact {artifact_id!r}"
                    )
                await completed_steps[initializer_id].wait()
            try:
                current = {artifact_id: values[artifact_id] for artifact_id in loop.feedback_artifact_ids}
            except KeyError as error:
                raise ExecutionPlanError(
                    f"loop {loop.loop_id!r} has no initial value for feedback artifact {error.args[0]!r}"
                ) from None
        else:
            if set(saved.current_values) != feedback_ids:
                raise ExecutionPlanError(
                    f"loop {loop.loop_id!r} checkpoint current values must match feedback artifacts exactly"
                )
            if saved.staged_values or saved.completed_step_ids:
                raise ExecutionPlanError(
                    f"loop {loop.loop_id!r} inflight resume is not supported; checkpoint must be at an epoch barrier"
                )
            current = dict(saved.current_values)

        while True:
            if max_loop_epochs is not None and epoch >= max_loop_epochs:
                raise ExecutionPlanError(
                    f"loop {loop.loop_id!r} reached max_loop_epochs={max_loop_epochs} without terminating"
                )
            epoch_values: dict[str, object] = {}
            epoch_completed = {step_id: anyio.Event() for step_id in loop.step_ids}
            epoch_lock = anyio.Lock()

            async def run_epoch_fiber(
                fiber: Fiber,
                *,
                epoch_index: int = epoch,
                current_values: Mapping[str, object] = current,
                staged_values: dict[str, object] = epoch_values,
                completed_events: Mapping[str, anyio.Event] = epoch_completed,
                staged_lock: anyio.Lock = epoch_lock,
            ) -> None:
                for instruction in fiber.instructions:
                    if isinstance(instruction, Await):
                        for step_id in instruction.step_ids:
                            event = completed_events.get(step_id, completed_steps.get(step_id))
                            if event is None:
                                raise ExecutionPlanError(f"loop plan awaits unknown step: {step_id}")
                            await event.wait()
                        continue
                    if isinstance(instruction, AwaitSelections | Select):
                        raise ExecutionPlanError("feedback loop epochs do not yet support select operations")
                    if not isinstance(instruction, Invoke):
                        raise ExecutionPlanError(
                            f"loop plan contains unknown instruction: {type(instruction).__name__}"
                        )
                    step_id = instruction.step_id
                    if step_id not in completed_events:
                        raise ExecutionPlanError(f"loop {loop.loop_id!r} epoch invokes non-member step: {step_id}")
                    step_inputs: dict[str, object] = {}
                    async with staged_lock:
                        for artifact_id in consumed[step_id]:
                            if artifact_id in feedback_ids:
                                source = current_values
                            elif artifact_id in staged_values:
                                source = staged_values
                            else:
                                source = values
                            try:
                                step_inputs[artifact_id] = source[artifact_id]
                            except KeyError:
                                raise ExecutionPlanError(
                                    f"loop {loop.loop_id!r} step {step_id!r} input artifact "
                                    f"is unavailable in epoch {epoch_index}: {artifact_id!r}"
                                ) from None
                    outputs = await invoke(
                        steps[step_id],
                        step_inputs,
                        loop_id=loop.loop_id,
                        epoch=epoch_index,
                    )
                    async with staged_lock:
                        collisions = set(outputs) & staged_values.keys()
                        if collisions:
                            raise ExecutionPlanError(
                                f"loop {loop.loop_id!r} epoch has multiple staged writers: {sorted(collisions)}"
                            )
                        staged_values.update(outputs)
                    completed_events[step_id].set()

            async with anyio.create_task_group() as task_group:
                for fiber in loop.fibers:
                    task_group.start_soon(run_epoch_fiber, fiber)

            missing_feedback = feedback_ids - epoch_values.keys()
            if missing_feedback:
                raise ExecutionPlanError(
                    f"loop {loop.loop_id!r} did not stage every next state in epoch {epoch}: {sorted(missing_feedback)}"
                )
            try:
                terminal_value = epoch_values[loop.terminal_output_artifact_id]
            except KeyError:
                raise ExecutionPlanError(
                    f"TerminalStep {loop.terminal_step_id!r} did not stage its Boolean result"
                ) from None
            if type(terminal_value) is not bool:
                raise StepOutputError(
                    f"TerminalStep {loop.terminal_step_id!r} output "
                    f"{loop.terminal_output_artifact_id!r} must be strict Boolean, "
                    f"got {type(terminal_value).__name__}"
                )

            next_current = {artifact_id: epoch_values[artifact_id] for artifact_id in loop.feedback_artifact_ids}
            next_epoch = epoch + 1
            next_loop_checkpoint = LoopExecutionCheckpoint(
                loop_id=loop.loop_id,
                epoch=next_epoch,
                current_values=next_current,
            )
            async with commit_lock:
                candidate_values = {**values, **next_current}
                candidate_completed = set(completed_step_ids)
                if terminal_value:
                    candidate_values.update(epoch_values)
                    candidate_completed.update(loop.step_ids)
                candidate_loops = {**loop_checkpoints, loop.loop_id: next_loop_checkpoint}
                await persist_candidate(candidate_values, candidate_completed, candidate_loops)
                values.clear()
                values.update(candidate_values)
                completed_step_ids.clear()
                completed_step_ids.update(candidate_completed)
                loop_checkpoints.clear()
                loop_checkpoints.update(candidate_loops)

            current = next_current
            epoch = next_epoch
            logger.debug(f"Committed workflow loop {loop.loop_id!r} epoch {epoch}")
            if terminal_value:
                for step_id in loop.step_ids:
                    completed_steps[step_id].set()
                logger.debug(f"Terminated workflow loop: {loop.loop_id!r}")
                return

    async def run_all() -> None:
        async with anyio.create_task_group() as task_group:
            for fiber in plan.fibers:
                task_group.start_soon(run_top_fiber, fiber)
            for loop in plan.loops:
                task_group.start_soon(run_loop, loop)

    if graph.policy.timeout_seconds is None:
        await run_all()
    else:
        with anyio.fail_after(graph.policy.timeout_seconds):
            await run_all()

    outputs: dict[str, object] = {}
    for artifact in graph.artifacts:
        if not artifact.is_output:
            continue
        try:
            outputs[artifact.artifact_id] = values[artifact.artifact_id]
        except KeyError:
            raise ExecutionPlanError(f"workflow output artifact is unavailable: {artifact.artifact_id}") from None
    return outputs


def _timing_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _start_timing(*, enabled: bool) -> _TimingStart | None:
    if not enabled:
        return None
    try:
        return _timing_now(), time.perf_counter()
    except Exception as error:
        logger.warning(f"Workflow timing start ignored after {type(error).__name__}: {error}")
        return None


def _finish_timing(
    timing_start: _TimingStart,
    *,
    finished: float | None = None,
) -> tuple[str, float, float]:
    started_at, started = timing_start
    if finished is None:
        finished = time.perf_counter()
    duration_ms = (finished - started) * 1_000
    if not math.isfinite(duration_ms) or duration_ms < 0:
        raise ValueError("workflow timing duration must be finite and non-negative")
    if not started_at.endswith("Z"):
        raise ValueError("workflow timing start must be a UTC timestamp")
    started_timestamp = datetime.fromisoformat(f"{started_at[:-1]}+00:00")
    if started_timestamp.tzinfo != UTC:
        raise ValueError("workflow timing start must be a UTC timestamp")
    finished_at = (started_timestamp + timedelta(milliseconds=duration_ms)).isoformat().replace("+00:00", "Z")
    return finished_at, duration_ms, finished


def _timing_status(error: BaseException) -> TimingStatus:
    if isinstance(error, anyio.get_cancelled_exc_class()):
        return "cancelled"
    if isinstance(error, BaseExceptionGroup) and all(
        _timing_status(nested) == "cancelled" for nested in error.exceptions
    ):
        return "cancelled"
    return "error"


def _append_attempt_timing(
    attempt_timings: list[AttemptTiming] | None,
    *,
    timing_start: _TimingStart | None,
    attempt: int,
    status: TimingStatus,
    error_type: str | None,
) -> None:
    if attempt_timings is None or timing_start is None:
        return
    try:
        finished_at, duration_ms, _ = _finish_timing(timing_start)
        attempt_timings.append(
            AttemptTiming(
                attempt=attempt,
                started_at=timing_start[0],
                finished_at=finished_at,
                duration_ms=duration_ms,
                status=status,
                error_type=error_type,
            )
        )
    except Exception as error:
        logger.warning(f"Workflow attempt timing ignored after {type(error).__name__}: {error}")


def _store_iteration_timing(
    iteration_timings: dict[int, IterationTiming] | None,
    iteration_finished: dict[int, float] | None,
    *,
    timing_start: _TimingStart | None,
    iteration_index: int,
    status: TimingStatus,
    error_type: str | None,
    attempt_timings: Sequence[AttemptTiming] | None,
) -> None:
    if iteration_timings is None or timing_start is None:
        return
    try:
        finished_at, duration_ms, finished = _finish_timing(timing_start)
        if iteration_finished is not None:
            iteration_finished[iteration_index] = finished
        iteration_timings[iteration_index] = IterationTiming(
            iteration_index=iteration_index,
            started_at=timing_start[0],
            finished_at=finished_at,
            duration_ms=duration_ms,
            status=status,
            error_type=error_type,
            attempts=tuple(attempt_timings or ()),
        )
    except Exception as error:
        logger.warning(f"Workflow iteration timing ignored after {type(error).__name__}: {error}")


def _record_step_timing(
    recorder: Callable[[StepTiming], None] | None,
    *,
    timing_start: _TimingStart | None,
    step: StepNode,
    metadata: StepTimingMetadata,
    is_foreach: bool,
    status: TimingStatus,
    error_type: str | None,
    attempt_timings: Sequence[AttemptTiming] | None,
    iteration_timings: Mapping[int, IterationTiming] | None,
    iteration_finished: Mapping[int, float] | None,
) -> None:
    if recorder is None or timing_start is None:
        return
    try:
        finished = max(iteration_finished.values()) if is_foreach and iteration_finished else time.perf_counter()
        finished_at, duration_ms, _ = _finish_timing(
            timing_start,
            finished=finished,
        )
        recorder(
            StepTiming(
                step_id=step.step_id,
                step_name=metadata.step_name,
                executor_id=metadata.executor_id,
                executor_kind=metadata.executor_kind,
                foreach=is_foreach,
                started_at=timing_start[0],
                finished_at=finished_at,
                duration_ms=duration_ms,
                status=status,
                error_type=error_type,
                attempts=tuple(attempt_timings or ()) if not is_foreach else (),
                iterations=(tuple(iteration_timings.values()) if is_foreach and iteration_timings is not None else ()),
            )
        )
    except Exception as error:
        logger.warning(f"Workflow step timing ignored after {type(error).__name__}: {error}")


def _validate_step_outputs(
    step_id: str,
    outputs: object,
    *,
    expected_output_ids: Sequence[str],
) -> dict[str, object]:
    """Require one exact finite-JSON output object from a dispatcher."""

    if not isinstance(outputs, Mapping) or not all(isinstance(artifact_id, str) for artifact_id in outputs):
        raise StepOutputError(f"outputs for {step_id} must be a mapping with string keys")
    outputs_by_id = cast(Mapping[str, object], outputs)
    expected_outputs = set(expected_output_ids)
    actual_outputs = set(outputs_by_id)
    if actual_outputs != expected_outputs:
        raise StepOutputError(
            f"outputs for {step_id} must match exactly: "
            f"expected {sorted(expected_outputs)}, "
            f"got {sorted(actual_outputs)}"
        )
    try:
        return _copy_json_mapping(
            outputs_by_id,
            context=f"outputs for {step_id}",
        )
    except ExecutionPlanError as error:
        raise StepOutputError(str(error)) from error


def _failed_iteration(
    step_id: str,
    iteration_index: int,
    attempts: int,
    error: Exception,
) -> ForeachIterationCheckpoint:
    """Convert one exhausted ordinary failure into a stable JSON record."""

    return ForeachIterationCheckpoint(
        step_id=step_id,
        iteration_index=iteration_index,
        attempts=attempts,
        error={
            "kind": type(error).__name__,
            "message": str(error),
        },
    )


def _is_ordinary_step_error(error: Exception) -> bool:
    """Return whether retry/foreach may handle a dispatcher failure."""

    if isinstance(error, BaseExceptionGroup):
        return all(isinstance(nested, Exception) and _is_ordinary_step_error(nested) for nested in error.exceptions)
    return isinstance(error, StepOutputError) or not isinstance(
        error,
        WorkflowControlSignal | ExecutionPlanError,
    )


def _copy_json_mapping(
    value: object,
    *,
    context: str,
) -> dict[str, object]:
    """Deep-copy one finite JSON object while rejecting ambiguous values."""

    if not isinstance(value, Mapping):
        raise ExecutionPlanError(f"{context} must be a mapping")
    copied = _copy_json_value(
        value,
        context=context,
        active=set(),
    )
    return cast(dict[str, object], copied)


def _copy_json_value(
    value: object,
    *,
    context: str,
    active: set[int],
) -> object:
    """Deep-copy one strict JSON value, rejecting cycles and non-finite numbers."""

    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ExecutionPlanError(f"{context} contains a non-finite number")
        return value
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise ExecutionPlanError(f"{context} contains a reference cycle")
        active.add(identity)
        try:
            return [
                _copy_json_value(
                    item,
                    context=f"{context}[{index}]",
                    active=active,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ExecutionPlanError(f"{context} contains a reference cycle")
        active.add(identity)
        try:
            copied: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ExecutionPlanError(f"{context} contains a non-string object key")
                copied[key] = _copy_json_value(
                    item,
                    context=f"{context}.{key}",
                    active=active,
                )
            return copied
        finally:
            active.remove(identity)
    raise ExecutionPlanError(f"{context} contains a non-JSON value of type {type(value).__name__}")


def _json_values_equal(left: object, right: object) -> bool:
    """Compare finite JSON values recursively without Python bool/int coercion."""

    return _json_values_equal_inner(
        left,
        right,
        active_left=set(),
        active_right=set(),
    )


def _json_values_equal_inner(
    left: object,
    right: object,
    *,
    active_left: set[int],
    active_right: set[int],
) -> bool:
    """Compare one pair of strict JSON values and reject cyclic containers."""

    if left is None or right is None:
        return left is None and right is None
    if type(left) is bool or type(right) is bool:
        return type(left) is bool and type(right) is bool and left is right
    if type(left) is int or type(right) is int:
        return type(left) is int and type(right) is int and left == right
    if type(left) is float or type(right) is float:
        return (
            type(left) is float
            and type(right) is float
            and math.isfinite(left)
            and math.isfinite(right)
            and left == right
        )
    if type(left) is str or type(right) is str:
        return type(left) is str and type(right) is str and left == right
    if isinstance(left, list) or isinstance(right, list):
        if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
            return False
        left_id = id(left)
        right_id = id(right)
        if left_id in active_left or right_id in active_right:
            return False
        active_left.add(left_id)
        active_right.add(right_id)
        try:
            return all(
                _json_values_equal_inner(
                    left_item,
                    right_item,
                    active_left=active_left,
                    active_right=active_right,
                )
                for left_item, right_item in zip(left, right, strict=True)
            )
        finally:
            active_left.remove(left_id)
            active_right.remove(right_id)
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        if not all(isinstance(key, str) for key in left) or not all(isinstance(key, str) for key in right):
            return False
        left_mapping = cast(Mapping[str, object], left)
        right_mapping = cast(Mapping[str, object], right)
        if left_mapping.keys() != right_mapping.keys():
            return False
        left_id = id(left_mapping)
        right_id = id(right_mapping)
        if left_id in active_left or right_id in active_right:
            return False
        active_left.add(left_id)
        active_right.add(right_id)
        try:
            return all(
                _json_values_equal_inner(
                    left_mapping[key],
                    right_mapping[key],
                    active_left=active_left,
                    active_right=active_right,
                )
                for key in left_mapping
            )
        finally:
            active_left.remove(left_id)
            active_right.remove(right_id)
    return False


def _evaluate_condition(
    condition: SelectCondition,
    values: Mapping[str, object],
) -> bool:
    """Evaluate one closed selector condition tree."""

    if isinstance(condition, ComparisonCondition):
        left = _operand_value(condition.left, values)
        right = _operand_value(condition.right, values)
        if condition.operator == "eq":
            return left == right
        comparison = cast(
            Callable[[object, object], object],
            {
                "lt": operator.lt,
                "lte": operator.le,
                "gt": operator.gt,
                "gte": operator.ge,
            }[condition.operator],
        )
        try:
            return bool(comparison(left, right))
        except TypeError as exc:
            raise ExecutionPlanError(
                f"cannot compare select operands with {condition.operator}: {left!r} and {right!r}"
            ) from exc

    if condition.operator == "not":
        return not _evaluate_condition(condition.conditions[0], values)
    if condition.operator == "and":
        return all(_evaluate_condition(child, values) for child in condition.conditions)
    return any(_evaluate_condition(child, values) for child in condition.conditions)


def _operand_value(
    operand: ArtifactOperand | LiteralOperand,
    values: Mapping[str, object],
) -> object:
    """Resolve one selector operand against materialized artifacts."""

    if isinstance(operand, LiteralOperand):
        return operand.value
    try:
        return values[operand.artifact_id]
    except KeyError:
        raise ExecutionPlanError(f"select condition artifact is unavailable: {operand.artifact_id}") from None


def _reject_cycles(dependencies: dict[OperationId, set[OperationId]]) -> None:
    """Reject circular waits before any fibers are started."""

    indegree = {operation_id: len(awaited) for operation_id, awaited in dependencies.items()}
    dependents = {operation_id: set() for operation_id in dependencies}
    for operation_id, awaited in dependencies.items():
        for producer in awaited:
            dependents[producer].add(operation_id)

    ready = [operation_id for operation_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        operation_id = heapq.heappop(ready)
        visited += 1
        for dependent in sorted(dependents[operation_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)

    if visited != len(dependencies):
        cyclic = sorted(f"{kind}:{operation_id}" for (kind, operation_id), degree in indegree.items() if degree > 0)
        raise ExecutionPlanError(f"cycle requires explicit loop semantics: {cyclic}")
