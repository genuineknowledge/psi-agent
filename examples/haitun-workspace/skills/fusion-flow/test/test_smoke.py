from __future__ import annotations

from collections.abc import Mapping

import anyio
import pytest
from fusion_flow.workflow_execution import (
    Await,
    ExecutionPlanError,
    ResourceAllocator,
    StepExecutionError,
    execute_plan,
    generate_plan,
)
from fusion_flow.workflow_graph import (
    ArtifactNode,
    ConsumesEdge,
    ForeachEdge,
    ProducesEdge,
    ResourceRequirement,
    StepNode,
    WorkflowGraph,
    WorkflowGraphError,
    WorkflowPolicy,
)
from fusion_flow.workflow_runner import execute_workflow


def test_graph_contract_serializes_and_validates() -> None:
    graph = WorkflowGraph(
        "map_items",
        (
            StepNode(
                "map",
                "map",
                "worker",
                instruction_id="map_item",
                resources=(ResourceRequirement("gpu", 1),),
            ),
        ),
        (
            ArtifactNode("items", is_input=True),
            ArtifactNode("item", binding_step_id="map"),
            ArtifactNode("result", is_output=True),
        ),
        (
            ConsumesEdge("item", "map"),
            ForeachEdge("items", "map", "item"),
            ProducesEdge("map", "result"),
        ),
    )

    payload = graph.to_dict()
    assert payload["workflow_id"] == "map_items"
    assert payload["steps"][0]["resources"] == [{"resource_id": "gpu", "amount": 1}]
    assert [edge["kind"] for edge in payload["edges"]] == ["consumes", "foreach", "produces"]

    with pytest.raises(WorkflowGraphError, match="unknown consumed artifact"):
        WorkflowGraph(
            "invalid",
            (StepNode("step", "step", "worker"),),
            (),
            (ConsumesEdge("missing", "step"),),
        )


def test_plan_lowers_dependencies_and_rejects_cycles() -> None:
    graph = WorkflowGraph(
        "ordered",
        (
            StepNode("prepare", "prepare", "worker"),
            StepNode("publish", "publish", "worker", depends_on=("prepare",)),
        ),
        (),
    )
    fibers = {fiber.fiber_id: fiber.instructions for fiber in generate_plan(graph).fibers}

    assert fibers["publish"][0] == Await(("prepare",))

    cyclic = WorkflowGraph(
        "cycle",
        (
            StepNode("left", "left", "worker", depends_on=("right",)),
            StepNode("right", "right", "worker", depends_on=("left",)),
        ),
        (),
    )
    with pytest.raises(ExecutionPlanError, match="cycle"):
        generate_plan(cyclic)


@pytest.mark.anyio
async def test_execution_respects_resource_capacity() -> None:
    requirement = (ResourceRequirement("gpu", 1),)
    graph = WorkflowGraph(
        "capacity",
        (
            StepNode("left", "left", "worker", resources=requirement),
            StepNode("right", "right", "worker", resources=requirement),
        ),
        (),
        policy=WorkflowPolicy(max_concurrency=2),
    )
    active = 0
    maximum = 0
    invoked: set[str] = set()

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        nonlocal active, maximum
        assert inputs == {}
        invoked.add(step.step_id)
        active += 1
        maximum = max(maximum, active)
        try:
            await anyio.sleep(0.01)
            return {}
        finally:
            active -= 1

    await execute_plan(
        generate_plan(graph),
        graph,
        inputs={},
        dispatch=dispatch,
        resource_capacities={"gpu": 1},
    )

    assert invoked == {"left", "right"}
    assert maximum == 1


@pytest.mark.anyio
async def test_timeout_releases_resource_for_the_next_run() -> None:
    graph = WorkflowGraph(
        "timeout",
        (
            StepNode(
                "step",
                "step",
                "worker",
                timeout_seconds=1,
                resources=(ResourceRequirement("gpu", 1),),
            ),
        ),
        (),
    )
    allocator = ResourceAllocator({"gpu": 1})

    async def hang(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        del step, inputs
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    with pytest.RaisesGroup(pytest.RaisesExc(StepExecutionError, match="workflow step 'step' failed")):
        await execute_plan(
            generate_plan(graph),
            graph,
            inputs={},
            dispatch=hang,
            allocator=allocator,
        )

    async def succeed(
        step: StepNode,
        inputs: Mapping[str, object],
    ) -> Mapping[str, object]:
        del step, inputs
        return {}

    with anyio.fail_after(1):
        assert (
            await execute_plan(
                generate_plan(graph),
                graph,
                inputs={},
                dispatch=succeed,
                allocator=allocator,
            )
            == {}
        )


@pytest.mark.anyio
async def test_g4_program_compiles_and_executes_end_to_end() -> None:
    source = """
const dispatch: Workflow;
const dispatch_step: Step;
const dispatch_name: StepName;
const worker: Agent;
const request: Artifact;
const result: Artifact;

workflow dispatch {
    input_workflow(dispatch) == [request];
    output_workflow(dispatch) == [result];
    step_name(dispatch_step) == dispatch_name;
    step_instruction(dispatch_step) == "summarize_request";
    step_executor(dispatch_step) == worker;
    consumes(dispatch_step) == [request];
    produces(dispatch_step) == [result];
}
"""
    prompts: list[str] = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "completed"

    result = await execute_workflow(
        source,
        instruction_bodies={"summarize_request": "Summarize the request."},
        request="Explain structured concurrency.",
        complete=complete,
    )

    assert result == {"result": "completed"}
    assert "Instruction body:\nSummarize the request." in prompts[0]
    assert 'Inputs: {"request": "Explain structured concurrency."}' in prompts[0]
