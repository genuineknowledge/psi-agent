from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

import anyio
import pytest

import psi_agent.workflow_execution as workflow_execution
from psi_agent.workflow_execution import (
    Await,
    ExecutionPlan,
    ExecutionPlanError,
    Fiber,
    Invoke,
    StepDispatcher,
    execute_plan,
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
            ),
            "resource",
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
