"""Compile workflow graphs into inspectable one-shot async plans."""

from __future__ import annotations

import heapq
import operator
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import cast

import anyio
from loguru import logger

from psi_agent.workflow_graph import (
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


type StepDispatcher = Callable[
    [StepNode, Mapping[str, object]],
    Awaitable[Mapping[str, object]],
]


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
    """Runtime-only scheduling information supplied to a contextual dispatcher."""

    resource_lease: ResourceLease = ResourceLease()


type ContextualStepDispatcher = Callable[
    [StepNode, Mapping[str, object], DispatchContext],
    Awaitable[Mapping[str, object]],
]
type ResourceCapacity = int | Sequence[str]


@dataclass(slots=True)
class _AdmissionState:
    """Run-local counters committed under one allocator condition."""

    max_concurrency: int
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
            lease = await self._acquire(
                requirements,
                state=state,
            )
            yield lease
        finally:
            if lease is not None:
                # A no-resource step still owns a run admission counter.
                with anyio.CancelScope(shield=True):
                    await self._release(
                        lease,
                        state=state,
                    )

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
            while (state is not None and state.running >= state.max_concurrency) or any(
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
    """Lower one-shot data and explicit step dependencies into async fibers."""

    if any(isinstance(edge, ForeachEdge) for edge in graph.edges):
        raise ExecutionPlanError("foreach execution is not supported")
    for step in graph.steps:
        if step.max_attempts != 1:
            raise ExecutionPlanError("step retries are not supported")

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
        if not isinstance(edge, ConsumesEdge):
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


async def execute_plan(
    plan: ExecutionPlan,
    graph: WorkflowGraph,
    *,
    inputs: Mapping[str, object],
    dispatch: StepDispatcher | None = None,
    contextual_dispatch: ContextualStepDispatcher | None = None,
    resource_capacities: Mapping[str, ResourceCapacity] | None = None,
    allocator: ResourceAllocator | None = None,
) -> dict[str, object]:
    """Start all fibers and interpret their awaits and invocations."""

    if plan.workflow_id != graph.workflow_id:
        raise ExecutionPlanError(f"plan targets {plan.workflow_id}, not {graph.workflow_id}")
    if (dispatch is None) == (contextual_dispatch is None):
        raise ExecutionPlanError("provide exactly one of dispatch or contextual_dispatch")
    if resource_capacities is not None and allocator is not None:
        raise ExecutionPlanError("resource_capacities and allocator are mutually exclusive")

    expected_inputs = {artifact.artifact_id for artifact in graph.artifacts if artifact.is_input}
    supplied_inputs = set(inputs)
    if supplied_inputs != expected_inputs:
        raise ExecutionPlanError(
            f"workflow inputs must match exactly: expected {sorted(expected_inputs)}, got {sorted(supplied_inputs)}"
        )

    steps = {step.step_id: step for step in graph.steps}
    selectors = {selector.output_artifact_id: selector for selector in graph.selectors}
    consumed = {step_id: [] for step_id in steps}
    produced = {step_id: [] for step_id in steps}
    step_producer_by_artifact = {
        edge.artifact_id: edge.step_id for edge in graph.edges if isinstance(edge, ProducesEdge)
    }
    for edge in graph.edges:
        if isinstance(edge, ConsumesEdge):
            consumed[edge.step_id].append(edge.artifact_id)
        elif isinstance(edge, ProducesEdge):
            produced[edge.step_id].append(edge.artifact_id)

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
                    for artifact_id in consumed[instruction.step_id]
                    if artifact_id in step_producer_by_artifact
                )
                missing_steps = required_steps - satisfied_steps
                if missing_steps:
                    raise ExecutionPlanError(
                        f"plan is missing dependencies for {instruction.step_id}: {sorted(missing_steps)}"
                    )
                required_selections = {
                    artifact_id for artifact_id in consumed[instruction.step_id] if artifact_id in selectors
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

    requirements_by_step = {step.step_id: step.resources for step in graph.steps}
    has_resources = any(requirements_by_step.values())
    if allocator is None:
        if resource_capacities is None:
            if has_resources:
                raise ExecutionPlanError("resource capacities or an allocator are required")
            allocator = ResourceAllocator({})
        else:
            allocator = ResourceAllocator(resource_capacities)
    await allocator.preflight(requirements_by_step)

    values = dict(inputs)
    completed_steps = {step_id: anyio.Event() for step_id in steps}
    completed_selections = {artifact_id: anyio.Event() for artifact_id in selectors}
    capacity = graph.policy.max_concurrency or max(1, len(steps))
    admission_state = _AdmissionState(
        max_concurrency=capacity,
    )

    async def invoke_step(
        step: StepNode,
        step_inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        async with allocator._admit(
            step.resources,
            state=admission_state,
        ) as resource_lease:
            logger.debug(f"Dispatching workflow step: {step.step_id}")
            context = DispatchContext(
                resource_lease=resource_lease,
            )
            if step.timeout_seconds is None:
                if contextual_dispatch is not None:
                    return await contextual_dispatch(step, step_inputs, context)
                if dispatch is None:
                    raise AssertionError("dispatcher preflight did not select a dispatcher")
                return await dispatch(step, step_inputs)

            with anyio.fail_after(step.timeout_seconds):
                if contextual_dispatch is not None:
                    return await contextual_dispatch(step, step_inputs, context)
                if dispatch is None:
                    raise AssertionError("dispatcher preflight did not select a dispatcher")
                return await dispatch(step, step_inputs)

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
                step = steps.get(instruction.step_id)
                if step is None:
                    raise ExecutionPlanError(f"plan invokes unknown step: {instruction.step_id}")
                step_inputs = {artifact_id: values[artifact_id] for artifact_id in consumed[step.step_id]}
                outputs = await invoke_step(step, step_inputs)

                if not isinstance(outputs, Mapping) or not all(isinstance(artifact_id, str) for artifact_id in outputs):
                    raise ExecutionPlanError(f"outputs for {step.step_id} must be a mapping with string keys")
                expected_outputs = set(produced[step.step_id])
                actual_outputs = set(outputs)
                if actual_outputs != expected_outputs:
                    raise ExecutionPlanError(
                        f"outputs for {step.step_id} must match exactly: "
                        f"expected {sorted(expected_outputs)}, "
                        f"got {sorted(actual_outputs)}"
                    )
                values.update(outputs)
                completed_steps[step.step_id].set()
                logger.debug(f"Completed workflow step: {step.step_id}")
                continue

            if isinstance(instruction, Select):
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
                    values[selector.output_artifact_id] = values[candidate_artifact_id]
                except KeyError:
                    raise ExecutionPlanError(
                        f"select {selector.output_artifact_id} is missing candidate artifact: {candidate_artifact_id}"
                    ) from None
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
