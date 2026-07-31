from __future__ import annotations

import importlib
from typing import Any, cast

import pytest

run_workflow = cast(Any, importlib.import_module("fusion_flow.workflow_runner"))


def _dispatch_workflow(
    executor_kind: str = "Agent",
    instruction_id: str = "summarize_request",
    *,
    policy: str = "",
) -> str:
    return f"""
const dispatch: Workflow;
const dispatch_step: Step;
const dispatch_name: StepName;
const worker: {executor_kind};
const request: Artifact;
const result: Artifact;

workflow dispatch {{
    input_workflow(dispatch) == [request];
    output_workflow(dispatch) == [result];
    {policy}
    step_name(dispatch_step) == dispatch_name;
    step_instruction(dispatch_step) == "{instruction_id}";
    step_executor(dispatch_step) == worker;
    consumes(dispatch_step) == [request];
    produces(dispatch_step) == [result];
}}
"""


def _select_workflow() -> str:
    return """
const select_demo: Workflow;
const primary_step: Step;
const fallback_step: Step;
const final_step: Step;
const primary_name: StepName;
const fallback_name: StepName;
const final_name: StepName;
const worker: Agent;
const request: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

workflow select_demo {
    input_workflow(select_demo) == [request];
    output_workflow(select_demo) == [selected_result, final_result];

    step_name(primary_step) == primary_name;
    step_instruction(primary_step) == "produce_primary";
    step_executor(primary_step) == worker;
    consumes(primary_step) == [request];
    produces(primary_step) == [primary_result];

    step_name(fallback_step) == fallback_name;
    step_instruction(fallback_step) == "produce_fallback";
    step_executor(fallback_step) == worker;
    consumes(fallback_step) == [request];
    produces(fallback_step) == [fallback_result];

    selected_result == if(request = "primary", primary_result, fallback_result);

    step_name(final_step) == final_name;
    step_instruction(final_step) == "consume_selected";
    step_executor(final_step) == worker;
    consumes(final_step) == [selected_result];
    produces(final_step) == [final_result];
}
"""


def test_runner_catalog_includes_typed_depends_on() -> None:
    context = run_workflow._default_parse_context()

    depends_on = context.operators["depends_on"]
    assert tuple(concept.name for concept in depends_on.input_concepts) == ("Step", "Step")
    assert depends_on.output_concept == context.concepts["Bool"]


@pytest.mark.anyio
async def test_agent_workflow_uses_supplied_instruction_body() -> None:
    prompts: list[str] = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "completed"

    result = await run_workflow.execute_workflow(
        _dispatch_workflow(),
        instruction_bodies={"summarize_request": "Summarize the request in two sentences."},
        request="Explain structured concurrency.",
        complete=complete,
    )

    assert result == {"result": "completed"}
    assert "Instruction ID: summarize_request" in prompts[0]
    assert "Instruction body:\nSummarize the request in two sentences." in prompts[0]
    assert 'Inputs: {"request": "Explain structured concurrency."}' in prompts[0]


@pytest.mark.anyio
async def test_select_executes_both_candidates_and_feeds_selected_value() -> None:
    seen: set[str] = set()

    async def complete(prompt: str) -> str:
        instruction_id = prompt.splitlines()[0].removeprefix("Instruction ID: ")
        seen.add(instruction_id)
        if instruction_id == "produce_primary":
            return "PRIMARY"
        if instruction_id == "produce_fallback":
            return "FALLBACK"
        assert instruction_id == "consume_selected"
        assert 'Inputs: {"selected_result": "PRIMARY"}' in prompt
        return "FINAL"

    result = await run_workflow.execute_workflow(
        _select_workflow(),
        instruction_bodies={
            "produce_primary": "Produce the primary answer.",
            "produce_fallback": "Produce the fallback answer.",
            "consume_selected": "Finalize the selected answer.",
        },
        request="primary",
        complete=complete,
    )

    assert result == {"final_result": "FINAL", "selected_result": "PRIMARY"}
    assert seen == {"produce_primary", "produce_fallback", "consume_selected"}


@pytest.mark.anyio
@pytest.mark.parametrize("executor_kind", ["Human", "Program"])
async def test_non_agent_executor_is_rejected_before_dispatch(executor_kind: str) -> None:
    called = False

    async def complete(prompt: str) -> str:
        nonlocal called
        called = True
        return prompt

    with pytest.raises(ValueError, match="supports Agent executors only"):
        await run_workflow.execute_workflow(
            _dispatch_workflow(executor_kind),
            instruction_bodies={"summarize_request": "Summarize the supplied request."},
            request="work",
            complete=complete,
        )
    assert not called


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("instruction_bodies", "message"),
    [
        ({}, "match workflow instruction IDs exactly"),
        (
            {
                "summarize_request": "Summarize the request.",
                "extra": "Do something else.",
            },
            "match workflow instruction IDs exactly",
        ),
        ({"summarize_request": "   "}, "actual instruction"),
        ({"summarize_request": "summarize_request"}, "actual instruction"),
    ],
)
async def test_instruction_contract_is_exact(
    instruction_bodies: dict[str, str],
    message: str,
) -> None:
    async def complete(prompt: str) -> str:
        pytest.fail(f"completion should not run: {prompt}")

    with pytest.raises(ValueError, match=message):
        await run_workflow.execute_workflow(
            _dispatch_workflow(),
            instruction_bodies=instruction_bodies,
            request="work",
            complete=complete,
        )


@pytest.mark.anyio
async def test_instruction_body_size_limit_is_checked_before_dispatch() -> None:
    called = False

    async def complete(prompt: str) -> str:
        nonlocal called
        called = True
        return prompt

    with pytest.raises(ValueError, match="exceeds 16384 UTF-8 bytes"):
        await run_workflow.execute_workflow(
            _dispatch_workflow(),
            instruction_bodies={"summarize_request": "x" * 16385},
            request="work",
            complete=complete,
        )
    assert not called


@pytest.mark.anyio
async def test_missing_step_instruction_is_rejected_before_any_dispatch() -> None:
    source = _select_workflow().replace(
        '    step_instruction(fallback_step) == "produce_fallback";\n',
        "",
    )
    called = False

    async def complete(prompt: str) -> str:
        nonlocal called
        called = True
        return prompt

    with pytest.raises(ValueError, match="every step must declare step_instruction"):
        await run_workflow.execute_workflow(
            source,
            instruction_bodies={
                "produce_primary": "Produce the primary answer.",
                "consume_selected": "Finalize the selected answer.",
            },
            request="primary",
            complete=complete,
        )
    assert not called


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ("max_concurrency(dispatch) == 9;", "max_concurrency exceeds 8"),
        ("workflow_timeout(dispatch) == 901;", "timeout exceeds 900 seconds"),
    ],
)
async def test_workflow_policy_caps_are_enforced(policy: str, message: str) -> None:
    async def complete(prompt: str) -> str:
        pytest.fail(f"completion should not run: {prompt}")

    with pytest.raises(ValueError, match=message):
        await run_workflow.execute_workflow(
            _dispatch_workflow(policy=policy),
            instruction_bodies={"summarize_request": "Summarize the supplied request."},
            request="work",
            complete=complete,
        )


@pytest.mark.anyio
async def test_contextual_completion_receives_resource_lease() -> None:
    source = (
        _dispatch_workflow()
        .replace(
            "const request: Artifact;",
            "const request: Artifact;\nconst gpu: Resource;",
        )
        .replace(
            "produces(dispatch_step) == [result];",
            "produces(dispatch_step) == [result];\nresource_requirement(dispatch_step, gpu) == 1;",
        )
    )
    grants: list[tuple[str, ...]] = []

    async def complete(
        prompt: str,
        context: run_workflow.CompletionContext,
    ) -> dict[str, object]:
        del prompt
        grants.append(context.dispatch.resource_lease.instances("gpu"))
        return {"result": "done"}

    result = await run_workflow.execute_workflow(
        source,
        instruction_bodies={"summarize_request": "Summarize the supplied request."},
        request="work",
        contextual_complete=complete,
        resource_capacities={"gpu": ("gpu-a",)},
    )

    assert result == {"result": "done"}
    assert grants == [("gpu-a",)]


@pytest.mark.anyio
async def test_depends_on_orders_steps_without_value_transfer() -> None:
    source = """
const ordered: Workflow;
const before_step: Step;
const after_step: Step;
const before_name: StepName;
const after_name: StepName;
const worker: Agent;
const before_result: Artifact;
const after_result: Artifact;

workflow ordered {
    output_workflow(ordered) == [before_result, after_result];
    step_name(before_step) == before_name;
    step_instruction(before_step) == "before";
    step_executor(before_step) == worker;
    produces(before_step) == [before_result];
    step_name(after_step) == after_name;
    step_instruction(after_step) == "after";
    step_executor(after_step) == worker;
    produces(after_step) == [after_result];
    depends_on(after_step, before_step) == True;
}
"""
    order: list[str] = []

    async def complete(prompt: str) -> str:
        instruction_id = prompt.splitlines()[0].removeprefix("Instruction ID: ")
        order.append(instruction_id)
        return instruction_id.upper()

    result = await run_workflow.execute_workflow(
        source,
        instruction_bodies={
            "before": "Run the prerequisite.",
            "after": "Run after the prerequisite.",
        },
        inputs={},
        complete=complete,
    )

    assert order == ["before", "after"]
    assert result == {"before_result": "BEFORE", "after_result": "AFTER"}
