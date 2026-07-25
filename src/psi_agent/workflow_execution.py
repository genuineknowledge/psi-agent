"""Compile workflow graphs into inspectable one-shot async plans."""

from __future__ import annotations

import heapq
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

import anyio
from loguru import logger

from psi_agent.workflow_graph import (
    ConsumesEdge,
    ForeachEdge,
    ProducesEdge,
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
class Invoke:
    """Invoke one graph step."""

    step_id: str


type PlanInstruction = Await | Invoke


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


def generate_plan(graph: WorkflowGraph) -> ExecutionPlan:
    """Lower one-shot producer/consumer dependencies into async fibers."""

    if any(isinstance(edge, ForeachEdge) for edge in graph.edges):
        raise ExecutionPlanError("foreach execution is not supported")
    for step in graph.steps:
        if step.resources:
            raise ExecutionPlanError("resource scheduling is not supported")
        if step.max_attempts != 1:
            raise ExecutionPlanError("step retries are not supported")

    producers = {edge.artifact_id: edge.step_id for edge in graph.edges if isinstance(edge, ProducesEdge)}
    for artifact in graph.artifacts:
        if artifact.is_input and artifact.artifact_id in producers:
            raise ExecutionPlanError(f"input artifact is also produced: {artifact.artifact_id}")

    awaited_by_step: dict[str, set[str]] = {step.step_id: set() for step in graph.steps}
    for edge in graph.edges:
        if not isinstance(edge, ConsumesEdge):
            continue
        producer = producers.get(edge.artifact_id)
        if producer is not None:
            awaited_by_step[edge.step_id].add(producer)

    _reject_cycles(awaited_by_step)

    fibers: list[Fiber] = []
    for step_id in sorted(awaited_by_step):
        awaited = tuple(sorted(awaited_by_step[step_id]))
        instructions: tuple[PlanInstruction, ...] = (Invoke(step_id),)
        if awaited:
            instructions = (Await(awaited), *instructions)
        fibers.append(Fiber(step_id, instructions))
    return ExecutionPlan(graph.workflow_id, tuple(fibers))


async def execute_plan(
    plan: ExecutionPlan,
    graph: WorkflowGraph,
    *,
    inputs: Mapping[str, object],
    dispatch: StepDispatcher,
) -> dict[str, object]:
    """Start all fibers and interpret their awaits and invocations."""

    if plan.workflow_id != graph.workflow_id:
        raise ExecutionPlanError(f"plan targets {plan.workflow_id}, not {graph.workflow_id}")

    expected_inputs = {artifact.artifact_id for artifact in graph.artifacts if artifact.is_input}
    supplied_inputs = set(inputs)
    if supplied_inputs != expected_inputs:
        raise ExecutionPlanError(
            f"workflow inputs must match exactly: expected {sorted(expected_inputs)}, got {sorted(supplied_inputs)}"
        )

    steps = {step.step_id: step for step in graph.steps}
    consumed = {step_id: [] for step_id in steps}
    produced = {step_id: [] for step_id in steps}
    producer_by_artifact = {edge.artifact_id: edge.step_id for edge in graph.edges if isinstance(edge, ProducesEdge)}
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
    if sorted(invoked) != sorted(steps):
        raise ExecutionPlanError("plan must invoke every graph step exactly once")

    plan_dependencies = {step_id: set() for step_id in steps}
    for fiber in plan.fibers:
        awaited: set[str] = set()
        invoked_earlier: set[str] = set()
        for instruction in fiber.instructions:
            if isinstance(instruction, Await):
                unknown = set(instruction.step_ids) - steps.keys()
                if unknown:
                    raise ExecutionPlanError(f"plan awaits unknown steps: {sorted(unknown)}")
                awaited.update(instruction.step_ids)
                continue

            satisfied = awaited | invoked_earlier
            required = {
                producer_by_artifact[artifact_id]
                for artifact_id in consumed[instruction.step_id]
                if artifact_id in producer_by_artifact
            }
            missing = required - satisfied
            if missing:
                raise ExecutionPlanError(f"plan is missing dependencies for {instruction.step_id}: {sorted(missing)}")
            plan_dependencies[instruction.step_id].update(satisfied)
            invoked_earlier.add(instruction.step_id)
    _reject_cycles(plan_dependencies)

    values = dict(inputs)
    completed = {step_id: anyio.Event() for step_id in steps}
    capacity = graph.policy.max_concurrency or max(1, len(steps))
    limiter = anyio.CapacityLimiter(capacity)

    async def run_fiber(fiber: Fiber) -> None:
        for instruction in fiber.instructions:
            if isinstance(instruction, Await):
                for step_id in instruction.step_ids:
                    event = completed.get(step_id)
                    if event is None:
                        raise ExecutionPlanError(f"plan awaits unknown step: {step_id}")
                    await event.wait()
                continue

            step = steps.get(instruction.step_id)
            if step is None:
                raise ExecutionPlanError(f"plan invokes unknown step: {instruction.step_id}")
            step_inputs = {artifact_id: values[artifact_id] for artifact_id in consumed[step.step_id]}
            async with limiter:
                logger.debug(f"Dispatching workflow step: {step.step_id}")
                if step.timeout_seconds is None:
                    outputs = await dispatch(step, step_inputs)
                else:
                    with anyio.fail_after(step.timeout_seconds):
                        outputs = await dispatch(step, step_inputs)

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
            completed[step.step_id].set()
            logger.debug(f"Completed workflow step: {step.step_id}")

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


def _reject_cycles(awaited_by_step: dict[str, set[str]]) -> None:
    """Reject circular waits before any fibers are started."""

    indegree = {step_id: len(awaited) for step_id, awaited in awaited_by_step.items()}
    dependents = {step_id: set() for step_id in awaited_by_step}
    for step_id, awaited in awaited_by_step.items():
        for producer in awaited:
            dependents[producer].add(step_id)

    ready = [step_id for step_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        step_id = heapq.heappop(ready)
        visited += 1
        for dependent in sorted(dependents[step_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)

    if visited != len(awaited_by_step):
        cyclic = sorted(step_id for step_id, degree in indegree.items() if degree > 0)
        raise ExecutionPlanError(f"cycle requires explicit loop semantics: {cyclic}")
