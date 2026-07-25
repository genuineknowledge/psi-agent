from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import anyio
import pytest

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
    ConsumesEdge,
    ForeachEdge,
    ProducesEdge,
    ResourceRequirement,
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
