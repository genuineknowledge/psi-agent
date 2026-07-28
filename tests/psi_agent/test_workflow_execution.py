from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

import anyio
import pytest
from anyio.lowlevel import checkpoint

import psi_agent.workflow_execution as workflow_execution
from psi_agent.workflow_execution import (
    Await,
    DispatchContext,
    ExecutionCheckpoint,
    ExecutionPlan,
    ExecutionPlanError,
    Fiber,
    Invoke,
    ResourceAllocator,
    StepDispatcher,
    create_execution_checkpoint,
    execute_plan,
    execution_plan_digest,
    generate_plan,
)
from psi_agent.workflow_graph import (
    ArtifactNode,
    ArtifactOperand,
    ComparisonCondition,
    ConsumesEdge,
    ForeachEdge,
    LiteralOperand,
    LogicalCondition,
    ProducesEdge,
    ResourceRequirement,
    SelectCondition,
    SelectNode,
    StepNode,
    WorkflowGraph,
    WorkflowPolicy,
)


def _diamond_graph() -> WorkflowGraph:
    return WorkflowGraph(
        workflow_id="article",
        steps=(
            StepNode("research", "research", "researcher"),
            StepNode("draft", "draft", "writer"),
            StepNode("review", "review", "reviewer"),
            StepNode("publish", "publish", "publisher"),
        ),
        artifacts=(
            ArtifactNode("topic", is_input=True),
            ArtifactNode("notes"),
            ArtifactNode("draft_text"),
            ArtifactNode("review_text"),
            ArtifactNode("article", is_output=True),
        ),
        edges=(
            ConsumesEdge("topic", "research"),
            ProducesEdge("research", "notes"),
            ConsumesEdge("notes", "draft"),
            ConsumesEdge("notes", "review"),
            ProducesEdge("draft", "draft_text"),
            ProducesEdge("review", "review_text"),
            ConsumesEdge("draft_text", "publish"),
            ConsumesEdge("review_text", "publish"),
            ProducesEdge("publish", "article"),
        ),
    )


def _select_graph(
    condition: SelectCondition | None = None,
) -> WorkflowGraph:
    return WorkflowGraph(
        workflow_id="select",
        steps=(
            StepNode("primary", "primary", "primary-executor"),
            StepNode("fallback", "fallback", "fallback-executor"),
            StepNode("consumer", "consumer", "consumer-executor"),
        ),
        artifacts=(
            ArtifactNode("flag", is_input=True),
            ArtifactNode("primary_value"),
            ArtifactNode("fallback_value"),
            ArtifactNode("selected", is_output=True),
            ArtifactNode("result", is_output=True),
        ),
        edges=(
            ProducesEdge("primary", "primary_value"),
            ProducesEdge("fallback", "fallback_value"),
            ConsumesEdge("selected", "consumer"),
            ProducesEdge("consumer", "result"),
        ),
        selectors=(
            SelectNode(
                "selected",
                "primary_value",
                "fallback_value",
                condition
                or ComparisonCondition(
                    "eq",
                    ArtifactOperand("flag"),
                    LiteralOperand(True),
                ),
            ),
        ),
    )


async def _unexpected_dispatch(
    step: StepNode,
    inputs: Mapping[str, object],
) -> Mapping[str, object]:
    raise AssertionError((step, inputs))


def test_generate_plan_lowers_artifact_dependencies_to_awaits() -> None:
    assert generate_plan(_diamond_graph()) == ExecutionPlan(
        workflow_id="article",
        fibers=(
            Fiber("draft", (Await(("research",)), Invoke("draft"))),
            Fiber(
                "publish",
                (Await(("draft", "review")), Invoke("publish")),
            ),
            Fiber("research", (Invoke("research"),)),
            Fiber("review", (Await(("research",)), Invoke("review"))),
        ),
    )


def test_generate_plan_lowers_explicit_dependencies_without_data_edges() -> None:
    graph = WorkflowGraph(
        workflow_id="ordered",
        steps=(
            StepNode("prepare", "prepare", "executor"),
            StepNode("publish", "publish", "executor", depends_on=("prepare",)),
        ),
        artifacts=(),
    )

    assert generate_plan(graph) == ExecutionPlan(
        workflow_id="ordered",
        fibers=(
            Fiber("prepare", (Invoke("prepare"),)),
            Fiber("publish", (Await(("prepare",)), Invoke("publish"))),
        ),
    )


def test_generate_plan_lowers_multiple_explicit_dependencies() -> None:
    graph = WorkflowGraph(
        workflow_id="multiple-predecessors",
        steps=(
            StepNode("left", "left", "executor"),
            StepNode("right", "right", "executor"),
            StepNode(
                "join",
                "join",
                "executor",
                depends_on=("right", "left"),
            ),
        ),
        artifacts=(),
    )

    assert generate_plan(graph) == ExecutionPlan(
        workflow_id="multiple-predecessors",
        fibers=(
            Fiber("join", (Await(("left", "right")), Invoke("join"))),
            Fiber("left", (Invoke("left"),)),
            Fiber("right", (Invoke("right"),)),
        ),
    )


def test_generate_plan_merges_and_deduplicates_data_and_explicit_dependencies() -> None:
    graph = WorkflowGraph(
        workflow_id="merged-dependencies",
        steps=(
            StepNode("producer", "producer", "executor"),
            StepNode(
                "consumer",
                "consumer",
                "executor",
                depends_on=("producer",),
            ),
        ),
        artifacts=(ArtifactNode("value"),),
        edges=(
            ProducesEdge("producer", "value"),
            ConsumesEdge("value", "consumer"),
        ),
    )

    assert generate_plan(graph) == ExecutionPlan(
        workflow_id="merged-dependencies",
        fibers=(
            Fiber("consumer", (Await(("producer",)), Invoke("consumer"))),
            Fiber("producer", (Invoke("producer"),)),
        ),
    )


def test_generate_plan_rejects_cycles() -> None:
    graph = WorkflowGraph(
        workflow_id="cycle",
        steps=(
            StepNode("left", "left", "left-executor"),
            StepNode("right", "right", "right-executor"),
        ),
        artifacts=(ArtifactNode("left_value"), ArtifactNode("right_value")),
        edges=(
            ConsumesEdge("right_value", "left"),
            ProducesEdge("left", "left_value"),
            ConsumesEdge("left_value", "right"),
            ProducesEdge("right", "right_value"),
        ),
    )

    with pytest.raises(ExecutionPlanError, match="cycle"):
        generate_plan(graph)


def test_generate_plan_rejects_explicit_dependency_cycles() -> None:
    graph = WorkflowGraph(
        workflow_id="explicit-cycle",
        steps=(
            StepNode("left", "left", "executor", depends_on=("right",)),
            StepNode("right", "right", "executor", depends_on=("left",)),
        ),
        artifacts=(),
    )

    with pytest.raises(ExecutionPlanError, match="cycle"):
        generate_plan(graph)


def test_generate_plan_rejects_explicit_self_dependency() -> None:
    graph = WorkflowGraph(
        workflow_id="self-cycle",
        steps=(StepNode("step", "step", "executor", depends_on=("step",)),),
        artifacts=(),
    )

    with pytest.raises(ExecutionPlanError, match="cycle"):
        generate_plan(graph)


@pytest.mark.parametrize(
    ("graph", "message"),
    [
        (
            WorkflowGraph(
                "foreach",
                (StepNode("step", "step", "executor"),),
                (
                    ArtifactNode("items", is_input=True),
                    ArtifactNode("item", binding_step_id="step"),
                ),
                (
                    ForeachEdge("items", "step", "item"),
                    ConsumesEdge("item", "step"),
                ),
            ),
            "foreach",
        ),
        (
            WorkflowGraph(
                "retry",
                (StepNode("step", "step", "executor", max_attempts=2),),
                (),
            ),
            "retr",
        ),
        (
            WorkflowGraph(
                "input-producer",
                (
                    StepNode("producer", "producer", "producer-executor"),
                    StepNode("consumer", "consumer", "consumer-executor"),
                ),
                (ArtifactNode("value", is_input=True),),
                (
                    ProducesEdge("producer", "value"),
                    ConsumesEdge("value", "consumer"),
                ),
            ),
            "also produced",
        ),
    ],
)
def test_generate_plan_rejects_unsupported_one_shot_semantics(
    graph: WorkflowGraph,
    message: str,
) -> None:
    with pytest.raises(ExecutionPlanError, match=message):
        generate_plan(graph)


def test_generate_plan_accepts_resource_requirements() -> None:
    graph = WorkflowGraph(
        "resources",
        (
            StepNode(
                "step",
                "step",
                "executor",
                resources=(ResourceRequirement("gpu", 1),),
            ),
        ),
        (),
    )

    assert generate_plan(graph) == ExecutionPlan(
        workflow_id="resources",
        fibers=(Fiber("step", (Invoke("step"),)),),
    )


@pytest.mark.anyio
async def test_execute_plan_starts_ready_steps_in_parallel() -> None:
    graph = _diamond_graph()
    both_started = anyio.Event()
    started: set[str] = set()

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        if step.step_id == "research":
            return {"notes": inputs["topic"]}
        if step.step_id in {"draft", "review"}:
            started.add(step.step_id)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            output_id = "draft_text" if step.step_id == "draft" else "review_text"
            return {output_id: inputs["notes"]}
        return {"article": f"{inputs['draft_text']}/{inputs['review_text']}"}

    with anyio.fail_after(1):
        result = await execute_plan(
            generate_plan(graph),
            graph,
            inputs={"topic": "async"},
            dispatch=dispatch,
        )

    assert result == {"article": "async/async"}


@pytest.mark.anyio
async def test_execute_plan_enforces_explicit_dependency_without_data_edge() -> None:
    graph = WorkflowGraph(
        workflow_id="ordered",
        steps=(
            StepNode("prepare", "prepare", "executor"),
            StepNode("publish", "publish", "executor", depends_on=("prepare",)),
        ),
        artifacts=(),
    )
    prepare_completed = False
    invoked: list[str] = []

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        nonlocal prepare_completed
        assert inputs == {}
        if step.step_id == "prepare":
            await checkpoint()
            prepare_completed = True
        else:
            assert prepare_completed
        invoked.append(step.step_id)
        return {}

    await execute_plan(
        generate_plan(graph),
        graph,
        inputs={},
        dispatch=dispatch,
    )

    assert invoked == ["prepare", "publish"]


@pytest.mark.anyio
async def test_execute_plan_does_not_invoke_explicit_dependent_after_predecessor_failure() -> None:
    graph = WorkflowGraph(
        workflow_id="ordered-failure",
        steps=(
            StepNode("prepare", "prepare", "executor"),
            StepNode("publish", "publish", "executor", depends_on=("prepare",)),
        ),
        artifacts=(),
    )
    invoked: list[str] = []

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        assert inputs == {}
        invoked.append(step.step_id)
        if step.step_id == "prepare":
            raise RuntimeError("prepare failed")
        return {}

    with pytest.RaisesGroup(pytest.RaisesExc(RuntimeError, match="prepare failed")):
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=dispatch,
        )

    assert invoked == ["prepare"]


@pytest.mark.anyio
async def test_execute_plan_rejects_missing_plan_dependency() -> None:
    graph = _diamond_graph()
    valid_plan = generate_plan(graph)
    plan = ExecutionPlan(
        valid_plan.workflow_id,
        tuple(
            Fiber(fiber.fiber_id, (Invoke("draft"),)) if fiber.fiber_id == "draft" else fiber
            for fiber in valid_plan.fibers
        ),
    )

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        raise AssertionError((step, inputs))

    with pytest.raises(ExecutionPlanError, match=r"missing dependencies.*research"):
        await execute_plan(plan, graph, inputs={"topic": "async"}, dispatch=dispatch)


@pytest.mark.anyio
async def test_execute_plan_rejects_missing_explicit_dependency() -> None:
    graph = WorkflowGraph(
        workflow_id="ordered",
        steps=(
            StepNode("prepare", "prepare", "executor"),
            StepNode("publish", "publish", "executor", depends_on=("prepare",)),
        ),
        artifacts=(),
    )
    plan = ExecutionPlan(
        workflow_id=graph.workflow_id,
        fibers=(
            Fiber("prepare", (Invoke("prepare"),)),
            Fiber("publish", (Invoke("publish"),)),
        ),
    )

    with pytest.raises(ExecutionPlanError, match=r"missing dependencies.*prepare"):
        await execute_plan(plan, graph, inputs={}, dispatch=_unexpected_dispatch)


@pytest.mark.anyio
async def test_execute_plan_rejects_circular_plan_awaits() -> None:
    graph = WorkflowGraph(
        "circular-plan",
        (
            StepNode("left", "left", "left-executor"),
            StepNode("right", "right", "right-executor"),
        ),
        (),
    )
    plan = ExecutionPlan(
        graph.workflow_id,
        (
            Fiber("left", (Await(("right",)), Invoke("left"))),
            Fiber("right", (Await(("left",)), Invoke("right"))),
        ),
    )

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        raise AssertionError((step, inputs))

    with pytest.raises(ExecutionPlanError, match="cycle"):
        with anyio.fail_after(0.1):
            await execute_plan(plan, graph, inputs={}, dispatch=dispatch)


@pytest.mark.anyio
async def test_execute_plan_rejects_non_mapping_dispatcher_output() -> None:
    graph = WorkflowGraph(
        "invalid-output",
        (StepNode("step", "step", "executor"),),
        (ArtifactNode("result", is_output=True),),
        (ProducesEdge("step", "result"),),
    )

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> object:
        return [step.step_id, inputs]

    with pytest.RaisesGroup(pytest.RaisesExc(ExecutionPlanError, match="mapping")):
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=cast(StepDispatcher, dispatch),
        )


def test_generate_plan_includes_eager_select_fiber_and_mixed_waits() -> None:
    assert generate_plan(_select_graph()) == ExecutionPlan(
        workflow_id="select",
        fibers=(
            Fiber(
                "consumer",
                (
                    workflow_execution.AwaitSelections(("selected",)),
                    Invoke("consumer"),
                ),
            ),
            Fiber("fallback", (Invoke("fallback"),)),
            Fiber("primary", (Invoke("primary"),)),
            Fiber(
                "selected",
                (
                    Await(("fallback", "primary")),
                    workflow_execution.Select("selected"),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("flag", "expected"),
    [(True, "primary"), (False, "fallback")],
)
@pytest.mark.anyio
async def test_execute_plan_eagerly_selects_named_artifact(
    flag: bool,
    expected: str,
) -> None:
    graph = _select_graph()
    invoked: list[str] = []

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        invoked.append(step.step_id)
        if step.step_id == "primary":
            return {"primary_value": "primary"}
        if step.step_id == "fallback":
            return {"fallback_value": "fallback"}
        assert set(inputs) == {"selected"}
        return {"result": inputs["selected"]}

    result = await execute_plan(
        generate_plan(graph),
        graph,
        inputs={"flag": flag},
        dispatch=dispatch,
    )

    assert sorted(invoked) == ["consumer", "fallback", "primary"]
    assert result == {"selected": expected, "result": expected}


@pytest.mark.anyio
async def test_execute_plan_supports_chained_selects() -> None:
    graph = WorkflowGraph(
        "chained-selects",
        (
            StepNode("first", "first", "executor"),
            StepNode("second", "second", "executor"),
            StepNode("third", "third", "executor"),
            StepNode("consumer", "consumer", "executor"),
        ),
        (
            ArtifactNode("first_flag", is_input=True),
            ArtifactNode("second_flag", is_input=True),
            ArtifactNode("first_value"),
            ArtifactNode("second_value"),
            ArtifactNode("third_value"),
            ArtifactNode("first_selected"),
            ArtifactNode("second_selected"),
            ArtifactNode("result", is_output=True),
        ),
        (
            ProducesEdge("first", "first_value"),
            ProducesEdge("second", "second_value"),
            ProducesEdge("third", "third_value"),
            ConsumesEdge("second_selected", "consumer"),
            ProducesEdge("consumer", "result"),
        ),
        selectors=(
            SelectNode(
                "second_selected",
                "first_selected",
                "third_value",
                ComparisonCondition(
                    "eq",
                    ArtifactOperand("second_flag"),
                    LiteralOperand(True),
                ),
            ),
            SelectNode(
                "first_selected",
                "first_value",
                "second_value",
                ComparisonCondition(
                    "eq",
                    ArtifactOperand("first_flag"),
                    LiteralOperand(True),
                ),
            ),
        ),
    )
    plan = generate_plan(graph)
    second_select_fiber = next(fiber for fiber in plan.fibers if fiber.fiber_id == "second_selected")
    assert second_select_fiber.instructions == (
        Await(("third",)),
        workflow_execution.AwaitSelections(("first_selected",)),
        workflow_execution.Select("second_selected"),
    )

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        if step.step_id == "consumer":
            return {"result": inputs["second_selected"]}
        return {f"{step.step_id}_value": step.step_id}

    result = await execute_plan(
        plan,
        graph,
        inputs={"first_flag": True, "second_flag": True},
        dispatch=dispatch,
    )

    assert result == {"result": "first"}


def test_generate_plan_rejects_mixed_step_select_cycle() -> None:
    graph = WorkflowGraph(
        "mixed-cycle",
        (StepNode("step", "step", "executor"),),
        (
            ArtifactNode("flag", is_input=True),
            ArtifactNode("fallback", is_input=True),
            ArtifactNode("candidate"),
            ArtifactNode("selected"),
        ),
        (
            ConsumesEdge("selected", "step"),
            ProducesEdge("step", "candidate"),
        ),
        selectors=(
            SelectNode(
                "selected",
                "candidate",
                "fallback",
                ComparisonCondition(
                    "eq",
                    ArtifactOperand("flag"),
                    LiteralOperand(True),
                ),
            ),
        ),
    )

    with pytest.raises(ExecutionPlanError, match="cycle"):
        generate_plan(graph)


@pytest.mark.anyio
async def test_execute_plan_rejects_missing_selection_dependency() -> None:
    graph = _select_graph()
    valid_plan = generate_plan(graph)
    plan = ExecutionPlan(
        valid_plan.workflow_id,
        tuple(
            Fiber(fiber.fiber_id, (Invoke("consumer"),)) if fiber.fiber_id == "consumer" else fiber
            for fiber in valid_plan.fibers
        ),
    )

    with pytest.raises(
        ExecutionPlanError,
        match=r"missing selection dependencies.*selected",
    ):
        await execute_plan(
            plan,
            graph,
            inputs={"flag": True},
            dispatch=_unexpected_dispatch,
        )


@pytest.mark.parametrize(
    "condition",
    [
        ComparisonCondition("eq", LiteralOperand(1), LiteralOperand(1)),
        ComparisonCondition("lt", LiteralOperand(1), LiteralOperand(2)),
        ComparisonCondition("lte", LiteralOperand(2), LiteralOperand(2)),
        ComparisonCondition("gt", LiteralOperand(2), LiteralOperand(1)),
        ComparisonCondition("gte", LiteralOperand(2), LiteralOperand(2)),
        LogicalCondition(
            "not",
            (
                ComparisonCondition(
                    "eq",
                    LiteralOperand(1),
                    LiteralOperand(2),
                ),
            ),
        ),
        LogicalCondition(
            "and",
            (
                ComparisonCondition(
                    "eq",
                    LiteralOperand(1),
                    LiteralOperand(1),
                ),
                ComparisonCondition(
                    "lt",
                    LiteralOperand(1),
                    LiteralOperand(2),
                ),
            ),
        ),
        LogicalCondition(
            "or",
            (
                ComparisonCondition(
                    "eq",
                    LiteralOperand(1),
                    LiteralOperand(2),
                ),
                ComparisonCondition(
                    "gt",
                    LiteralOperand(2),
                    LiteralOperand(1),
                ),
            ),
        ),
    ],
)
@pytest.mark.anyio
async def test_execute_plan_evaluates_select_conditions(
    condition: SelectCondition,
) -> None:
    graph = WorkflowGraph(
        "condition",
        (),
        (
            ArtifactNode("primary", is_input=True),
            ArtifactNode("fallback", is_input=True),
            ArtifactNode("selected", is_output=True),
        ),
        selectors=(
            SelectNode(
                "selected",
                "primary",
                "fallback",
                condition,
            ),
        ),
    )

    result = await execute_plan(
        generate_plan(graph),
        graph,
        inputs={"primary": "yes", "fallback": "no"},
        dispatch=_unexpected_dispatch,
    )

    assert result == {"selected": "yes"}


@pytest.mark.anyio
async def test_execute_plan_rejects_incompatible_ordered_comparison() -> None:
    graph = WorkflowGraph(
        "incompatible-condition",
        (),
        (
            ArtifactNode("primary", is_input=True),
            ArtifactNode("fallback", is_input=True),
            ArtifactNode("selected", is_output=True),
        ),
        selectors=(
            SelectNode(
                "selected",
                "primary",
                "fallback",
                ComparisonCondition(
                    "lt",
                    LiteralOperand(1),
                    LiteralOperand("two"),
                ),
            ),
        ),
    )

    with pytest.RaisesGroup(
        pytest.RaisesExc(
            ExecutionPlanError,
            match="cannot compare.*lt",
        )
    ):
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={"primary": "yes", "fallback": "no"},
            dispatch=_unexpected_dispatch,
        )


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.anyio
async def test_execute_plan_requires_every_select_exactly_once(
    duplicate: bool,
) -> None:
    graph = _select_graph()
    valid_plan = generate_plan(graph)
    select_fiber = next(fiber for fiber in valid_plan.fibers if fiber.fiber_id == "selected")
    fibers = valid_plan.fibers + ((select_fiber,) if duplicate else ())
    if not duplicate:
        fibers = tuple(fiber for fiber in fibers if fiber.fiber_id != "selected")
    plan = ExecutionPlan(valid_plan.workflow_id, fibers)

    with pytest.raises(ExecutionPlanError, match="every graph select exactly once"):
        await execute_plan(
            plan,
            graph,
            inputs={"flag": True},
            dispatch=_unexpected_dispatch,
        )


@pytest.mark.parametrize(
    "replacement_factory",
    [
        lambda: (Invoke("selected"),),
        lambda: (workflow_execution.Select("primary_value"),),
        lambda: (
            workflow_execution.AwaitSelections(("missing",)),
            Invoke("consumer"),
        ),
    ],
)
@pytest.mark.anyio
async def test_execute_plan_rejects_unknown_instruction_targets(
    replacement_factory: Callable[[], tuple[object, ...]],
) -> None:
    graph = _select_graph()
    valid_plan = generate_plan(graph)
    replacement = replacement_factory()
    target_fiber_id = (
        "selected"
        if any(isinstance(instruction, workflow_execution.Select) for instruction in replacement)
        else "consumer"
    )
    plan = ExecutionPlan(
        valid_plan.workflow_id,
        tuple(
            Fiber(
                fiber.fiber_id,
                cast(tuple[workflow_execution.PlanInstruction, ...], replacement),
            )
            if fiber.fiber_id == target_fiber_id
            else fiber
            for fiber in valid_plan.fibers
        ),
    )

    with pytest.raises(ExecutionPlanError, match="unknown"):
        await execute_plan(
            plan,
            graph,
            inputs={"flag": True},
            dispatch=_unexpected_dispatch,
        )


def _steps_graph(
    steps: tuple[StepNode, ...],
    *,
    max_concurrency: int | None = None,
) -> WorkflowGraph:
    return WorkflowGraph(
        "scheduled",
        steps,
        (),
        policy=WorkflowPolicy(max_concurrency=max_concurrency),
    )


async def _assert_max_active(
    graph: WorkflowGraph,
    expected: int,
    *,
    resource_capacities: Mapping[str, int | tuple[str, ...]] | None = None,
) -> None:
    active = 0
    maximum = 0
    reached = anyio.Event()
    release = anyio.Event()

    async def dispatch(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal active, maximum
        del step, inputs
        active += 1
        maximum = max(maximum, active)
        if active == expected:
            reached.set()
        try:
            await release.wait()
            return {}
        finally:
            active -= 1

    async def run() -> None:
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=dispatch,
            resource_capacities=resource_capacities,
        )

    with anyio.fail_after(1):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run)
            await reached.wait()
            await checkpoint()
            assert maximum == expected
            release.set()


@pytest.mark.anyio
@pytest.mark.parametrize(("capacity", "expected"), [(1, 1), (2, 2)])
async def test_resource_capacity_limits_parallel_steps(capacity: int, expected: int) -> None:
    requirement = (ResourceRequirement("gpu_device", 1),)
    graph = _steps_graph(
        (
            StepNode("left", "left", "agent", resources=requirement),
            StepNode("right", "right", "agent", resources=requirement),
        )
    )

    await _assert_max_active(
        graph,
        expected,
        resource_capacities={"gpu_device": capacity},
    )


@pytest.mark.anyio
async def test_different_resources_can_run_in_parallel() -> None:
    graph = _steps_graph(
        (
            StepNode("gpu", "gpu", "agent", resources=(ResourceRequirement("gpu_device", 1),)),
            StepNode("license", "license", "agent", resources=(ResourceRequirement("license", 1),)),
        )
    )

    await _assert_max_active(
        graph,
        2,
        resource_capacities={"gpu_device": 1, "license": 1},
    )


@pytest.mark.anyio
async def test_multiple_resources_are_acquired_without_deadlock() -> None:
    graph = _steps_graph(
        (
            StepNode(
                "left",
                "left",
                "agent",
                resources=(
                    ResourceRequirement("gpu_device", 1),
                    ResourceRequirement("license", 1),
                ),
            ),
            StepNode(
                "right",
                "right",
                "agent",
                resources=(
                    ResourceRequirement("license", 1),
                    ResourceRequirement("gpu_device", 1),
                ),
            ),
        )
    )

    await _assert_max_active(
        graph,
        1,
        resource_capacities={"gpu_device": 1, "license": 1},
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("capacities", "message"),
    [
        (None, "capacities"),
        ({"cpu": 1}, "missing resource"),
        ({"gpu_device": 0}, "positive integer"),
        ({"gpu_device": True}, "positive integer or instance"),
        ({"gpu_device": ()}, "must not be empty"),
        ({"gpu_device": ("gpu-0", "gpu-0")}, "unique"),
    ],
)
async def test_resource_preflight_errors_dispatch_nothing(
    capacities: Mapping[str, object] | None,
    message: str,
) -> None:
    graph = _steps_graph(
        (
            StepNode(
                "step",
                "step",
                "agent",
                resources=(ResourceRequirement("gpu_device", 2),),
            ),
        )
    )
    dispatch_count = 0

    async def dispatch(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal dispatch_count
        del step, inputs
        dispatch_count += 1
        return {}

    with pytest.raises(ExecutionPlanError, match=message):
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=dispatch,
            resource_capacities=cast(Mapping[str, int | tuple[str, ...]] | None, capacities),
        )
    assert dispatch_count == 0


@pytest.mark.anyio
async def test_requirement_over_capacity_dispatches_nothing() -> None:
    graph = _steps_graph(
        (
            StepNode(
                "step",
                "step",
                "agent",
                resources=(ResourceRequirement("gpu_device", 2),),
            ),
        )
    )
    dispatch_count = 0

    async def dispatch(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal dispatch_count
        del step, inputs
        dispatch_count += 1
        return {}

    with pytest.raises(ExecutionPlanError, match="total capacity is 1"):
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=dispatch,
            resource_capacities={"gpu_device": 1},
        )
    assert dispatch_count == 0


@pytest.mark.anyio
async def test_context_contains_resource_lease() -> None:
    graph = _steps_graph(
        (
            StepNode(
                "step",
                "step",
                "agent",
                resources=(ResourceRequirement("gpu_device", 1),),
            ),
        ),
    )
    contexts: list[DispatchContext] = []

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
        context: DispatchContext,
    ) -> Mapping[str, object]:
        del step, inputs
        contexts.append(context)
        return {}

    await execute_plan(
        generate_plan(graph),
        graph,
        inputs={},
        contextual_dispatch=dispatch,
        resource_capacities={"gpu_device": 1},
    )

    assert contexts[0].resource_lease.instances("gpu_device") == ("gpu_device-0",)


@pytest.mark.anyio
async def test_explicit_resource_instance_ids_are_preserved() -> None:
    graph = _steps_graph(
        (
            StepNode(
                "step",
                "step",
                "agent",
                resources=(ResourceRequirement("gpu_device", 1),),
            ),
        )
    )
    instance_ids: tuple[str, ...] = ()

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
        context: DispatchContext,
    ) -> Mapping[str, object]:
        nonlocal instance_ids
        del step, inputs
        instance_ids = context.resource_lease.instances("gpu_device")
        return {}

    await execute_plan(
        generate_plan(graph),
        graph,
        inputs={},
        contextual_dispatch=dispatch,
        resource_capacities={"gpu_device": ("cuda:3",)},
    )
    assert instance_ids == ("cuda:3",)


@pytest.mark.anyio
async def test_global_max_concurrency_is_stricter_than_resource_capacity() -> None:
    requirement = (ResourceRequirement("gpu_device", 1),)
    steps = tuple(StepNode(f"step-{index}", f"step-{index}", "agent", resources=requirement) for index in range(3))
    graph = _steps_graph(steps, max_concurrency=2)

    await _assert_max_active(
        graph,
        2,
        resource_capacities={"gpu_device": 3},
    )


@pytest.mark.anyio
async def test_resource_waiter_does_not_hold_global_slot() -> None:
    gpu = (ResourceRequirement("gpu_device", 1),)
    graph = _steps_graph(
        (
            StepNode("a-gpu-holder", "a-gpu-holder", "agent", resources=gpu),
            StepNode("b-gpu-waiter", "b-gpu-waiter", "agent", resources=gpu),
            StepNode("c-cpu", "c-cpu", "agent"),
        ),
        max_concurrency=2,
    )
    started: set[str] = set()
    holder_started = anyio.Event()
    cpu_started = anyio.Event()
    release = anyio.Event()

    async def dispatch(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        del inputs
        started.add(step.step_id)
        if step.step_id == "a-gpu-holder":
            holder_started.set()
        elif step.step_id == "c-cpu":
            cpu_started.set()
        await release.wait()
        return {}

    async def run() -> None:
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=dispatch,
            resource_capacities={"gpu_device": 1},
        )

    with anyio.fail_after(1):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run)
            await holder_started.wait()
            await cpu_started.wait()
            assert "b-gpu-waiter" not in started
            release.set()


@pytest.mark.anyio
async def test_resource_is_released_after_failure_timeout_and_cancel() -> None:
    allocator = ResourceAllocator({"gpu_device": 1})
    normal = _steps_graph(
        (
            StepNode(
                "step",
                "step",
                "agent",
                resources=(ResourceRequirement("gpu_device", 1),),
            ),
        )
    )

    async def fail(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        del step, inputs
        raise RuntimeError("failed")

    with pytest.RaisesGroup(pytest.RaisesExc(RuntimeError, match="failed")):
        await execute_plan(generate_plan(normal), normal, inputs={}, dispatch=fail, allocator=allocator)

    timed = _steps_graph(
        (
            StepNode(
                "step",
                "step",
                "agent",
                timeout_seconds=1,
                resources=(ResourceRequirement("gpu_device", 1),),
            ),
        )
    )

    async def hang(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        del step, inputs
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    with pytest.RaisesGroup(pytest.RaisesExc(TimeoutError)):
        await execute_plan(generate_plan(timed), timed, inputs={}, dispatch=hang, allocator=allocator)

    entered = anyio.Event()

    async def cancellable(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        del step, inputs
        entered.set()
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    async def run_cancellable() -> None:
        await execute_plan(
            generate_plan(normal),
            normal,
            inputs={},
            dispatch=cancellable,
            allocator=allocator,
        )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(run_cancellable)
        await entered.wait()
        task_group.cancel_scope.cancel()

    async def succeed(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        del step, inputs
        return {}

    with anyio.fail_after(1):
        await execute_plan(generate_plan(normal), normal, inputs={}, dispatch=succeed, allocator=allocator)


@pytest.mark.anyio
async def test_execute_plan_resumes_from_dependency_closed_checkpoint() -> None:
    graph = _diamond_graph()
    plan = generate_plan(graph)
    invoked: list[str] = []

    async def dispatch(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        invoked.append(step.step_id)
        if step.step_id == "draft":
            return {"draft_text": inputs["notes"]}
        if step.step_id == "review":
            return {"review_text": inputs["notes"]}
        if step.step_id == "publish":
            return {"article": f"{inputs['draft_text']}/{inputs['review_text']}"}
        raise AssertionError(step.step_id)

    result = await execute_plan(
        plan,
        graph,
        inputs={"topic": "async"},
        dispatch=dispatch,
        checkpoint=create_execution_checkpoint(
            plan,
            graph,
            values={"topic": "async", "notes": "resumed"},
            completed_step_ids=("research",),
        ),
    )

    assert set(invoked) == {"draft", "review", "publish"}
    assert result == {"article": "resumed/resumed"}


def _checkpoint_input_graph(
    *,
    workflow_id: str = "checkpoint-input",
    executor_id: str = "processor",
) -> WorkflowGraph:
    return WorkflowGraph(
        workflow_id=workflow_id,
        steps=(StepNode("process", "process", executor_id),),
        artifacts=(
            ArtifactNode("payload", is_input=True),
            ArtifactNode("result", is_output=True),
        ),
        edges=(
            ConsumesEdge("payload", "process"),
            ProducesEdge("process", "result"),
        ),
    )


@pytest.mark.parametrize(
    ("saved_input", "current_input"),
    [
        (True, 1),
        (1, 1.0),
        ({"items": [True, {"count": 1}]}, {"items": [1, {"count": 1}]}),
        ([1, 2], [2, 1]),
    ],
    ids=("bool-int", "int-float", "nested-bool-int", "list-order"),
)
@pytest.mark.anyio
async def test_checkpoint_input_comparison_is_json_type_strict(
    saved_input: object,
    current_input: object,
) -> None:
    graph = _checkpoint_input_graph()
    plan = generate_plan(graph)
    checkpoint_value = create_execution_checkpoint(
        plan,
        graph,
        values={
            "payload": saved_input,
            "result": "stale",
        },
        completed_step_ids=("process",),
    )

    with pytest.raises(ExecutionPlanError, match="checkpoint input does not match"):
        await execute_plan(
            plan,
            graph,
            inputs={"payload": current_input},
            dispatch=_unexpected_dispatch,
            checkpoint=checkpoint_value,
        )


@pytest.mark.anyio
async def test_checkpoint_input_comparison_ignores_object_key_order() -> None:
    graph = _checkpoint_input_graph()
    plan = generate_plan(graph)
    checkpoint_value = create_execution_checkpoint(
        plan,
        graph,
        values={
            "payload": {
                "first": [True, 1, 1.0],
                "second": {"value": None},
            },
            "result": "resumed",
        },
        completed_step_ids=("process",),
    )

    result = await execute_plan(
        plan,
        graph,
        inputs={
            "payload": {
                "second": {"value": None},
                "first": [True, 1, 1.0],
            }
        },
        dispatch=_unexpected_dispatch,
        checkpoint=checkpoint_value,
    )

    assert result == {"result": "resumed"}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_checkpoint_rejects_non_finite_json_values(value: float) -> None:
    graph = _checkpoint_input_graph()
    plan = generate_plan(graph)

    with pytest.raises(ExecutionPlanError, match="non-finite"):
        create_execution_checkpoint(
            plan,
            graph,
            values={"payload": value},
        )


@pytest.mark.parametrize(
    ("field_name", "identifiers"),
    [
        ("completed_step_ids", ("",)),
        ("completed_step_ids", ("process", "process")),
        ("completed_selection_ids", ("",)),
        ("completed_selection_ids", ("result", "result")),
    ],
)
def test_checkpoint_rejects_invalid_completed_operation_ids(
    field_name: str,
    identifiers: tuple[str, ...],
) -> None:
    graph = _checkpoint_input_graph()
    plan = generate_plan(graph)

    with pytest.raises(ValueError, match=field_name):
        create_execution_checkpoint(
            plan,
            graph,
            values={"payload": "saved"},
            completed_step_ids=(identifiers if field_name == "completed_step_ids" else ()),
            completed_selection_ids=(identifiers if field_name == "completed_selection_ids" else ()),
        )


def test_checkpoint_rejects_non_string_completed_operation_id() -> None:
    graph = _checkpoint_input_graph()
    plan = generate_plan(graph)

    with pytest.raises(ValueError, match="completed_step_ids"):
        create_execution_checkpoint(
            plan,
            graph,
            values={"payload": "saved"},
            completed_step_ids=cast(tuple[str, ...], (1,)),
        )


def test_checkpoint_rejects_string_as_completed_operation_collection() -> None:
    graph = _checkpoint_input_graph()
    plan = generate_plan(graph)

    with pytest.raises(ValueError, match="completed_step_ids"):
        create_execution_checkpoint(
            plan,
            graph,
            values={"payload": "saved"},
            completed_step_ids=cast(tuple[str, ...], "process"),
        )


@pytest.mark.anyio
async def test_execute_plan_rejects_non_finite_current_checkpoint_input() -> None:
    graph = _checkpoint_input_graph()
    plan = generate_plan(graph)
    checkpoint_value = create_execution_checkpoint(
        plan,
        graph,
        values={"payload": 1},
    )

    with pytest.raises(ExecutionPlanError, match="checkpoint input does not match"):
        await execute_plan(
            plan,
            graph,
            inputs={"payload": float("inf")},
            dispatch=_unexpected_dispatch,
            checkpoint=checkpoint_value,
        )


@pytest.mark.anyio
async def test_checkpoint_rejects_another_workflow_with_matching_operation_ids() -> None:
    original_graph = _checkpoint_input_graph(workflow_id="original")
    original_plan = generate_plan(original_graph)
    checkpoint_value = create_execution_checkpoint(
        original_plan,
        original_graph,
        values={"payload": "saved", "result": "stale"},
        completed_step_ids=("process",),
    )
    current_graph = _checkpoint_input_graph(workflow_id="current")
    current_plan = generate_plan(current_graph)

    with pytest.raises(ExecutionPlanError, match=r"checkpoint targets workflow 'original'.*'current'"):
        await execute_plan(
            current_plan,
            current_graph,
            inputs={"payload": "saved"},
            dispatch=_unexpected_dispatch,
            checkpoint=checkpoint_value,
        )


@pytest.mark.anyio
async def test_checkpoint_rejects_changed_graph_with_same_workflow_and_operation_ids() -> None:
    original_graph = _checkpoint_input_graph(executor_id="processor-v1")
    original_plan = generate_plan(original_graph)
    checkpoint_value = create_execution_checkpoint(
        original_plan,
        original_graph,
        values={"payload": "saved", "result": "stale"},
        completed_step_ids=("process",),
    )
    current_graph = _checkpoint_input_graph(executor_id="processor-v2")
    current_plan = generate_plan(current_graph)

    with pytest.raises(ExecutionPlanError, match="checkpoint plan digest does not match"):
        await execute_plan(
            current_plan,
            current_graph,
            inputs={"payload": "saved"},
            dispatch=_unexpected_dispatch,
            checkpoint=checkpoint_value,
        )


def test_execution_plan_digest_is_stable_for_concurrent_fiber_order() -> None:
    graph = _diamond_graph()
    plan = generate_plan(graph)
    reordered_plan = ExecutionPlan(
        workflow_id=plan.workflow_id,
        fibers=tuple(reversed(plan.fibers)),
    )

    assert execution_plan_digest(plan, graph) == execution_plan_digest(reordered_plan, graph)


def test_execution_plan_digest_covers_explicit_plan_structure() -> None:
    graph = _steps_graph(
        (
            StepNode("first", "first", "executor"),
            StepNode("second", "second", "executor"),
        )
    )
    concurrent_plan = generate_plan(graph)
    ordered_plan = ExecutionPlan(
        workflow_id=graph.workflow_id,
        fibers=(
            Fiber("first", (Invoke("first"),)),
            Fiber("second", (Await(("first",)), Invoke("second"))),
        ),
    )

    assert execution_plan_digest(concurrent_plan, graph) != execution_plan_digest(ordered_plan, graph)


@pytest.mark.anyio
async def test_execute_plan_rejects_checkpoint_without_dependency_closure() -> None:
    graph = _diamond_graph()
    plan = generate_plan(graph)

    with pytest.raises(ExecutionPlanError, match=r"dependency-closed.*research"):
        await execute_plan(
            plan,
            graph,
            inputs={"topic": "async"},
            dispatch=_unexpected_dispatch,
            checkpoint=create_execution_checkpoint(
                plan,
                graph,
                values={"topic": "async", "draft_text": "draft"},
                completed_step_ids=("draft",),
            ),
        )


@pytest.mark.anyio
async def test_checkpoint_observer_finishes_before_dependent_is_released() -> None:
    graph = WorkflowGraph(
        "persist-before-release",
        (
            StepNode("prepare", "prepare", "executor"),
            StepNode("publish", "publish", "executor", depends_on=("prepare",)),
        ),
        (),
    )
    observer_entered = anyio.Event()
    release_observer = anyio.Event()
    publish_entered = anyio.Event()
    checkpoints: list[ExecutionCheckpoint] = []

    async def dispatch(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        assert inputs == {}
        if step.step_id == "publish":
            publish_entered.set()
        return {}

    async def observe(checkpoint: ExecutionCheckpoint) -> None:
        checkpoints.append(checkpoint)
        if checkpoint.completed_step_ids == ("prepare",):
            observer_entered.set()
            await release_observer.wait()

    async def run() -> None:
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=dispatch,
            checkpoint_observer=observe,
        )

    with anyio.fail_after(1):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(run)
            await observer_entered.wait()
            await checkpoint()
            assert not publish_entered.is_set()
            release_observer.set()

    assert publish_entered.is_set()
    assert checkpoints[-1].completed_step_ids == ("prepare", "publish")
    assert checkpoints[-1].workflow_id == graph.workflow_id
    assert checkpoints[-1].plan_digest == execution_plan_digest(generate_plan(graph), graph)
