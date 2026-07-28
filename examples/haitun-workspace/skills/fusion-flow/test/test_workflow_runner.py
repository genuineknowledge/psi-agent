from __future__ import annotations

import importlib
from typing import Any, cast

import pytest

run_workflow = cast(Any, importlib.import_module("fusion_flow_next.workflow_runner"))


def test_runner_catalog_includes_typed_depends_on() -> None:
    context = run_workflow._default_parse_context()

    depends_on = context.operators["depends_on"]
    assert tuple(concept.name for concept in depends_on.input_concepts) == (
        "Step",
        "Step",
    )
    assert depends_on.output_concept == context.concepts["Bool"]


def _dispatch_workflow(executor_kind: str | None, instruction: str) -> str:
    executor_declaration = "" if executor_kind is None else f"const worker: {executor_kind};"
    return f"""
const dispatch: Workflow;
const dispatch_step: Step;
const dispatch_name: StepName;
{executor_declaration}
const request: Artifact;
const result: Artifact;

workflow dispatch {{
    input_workflow(dispatch) == [request];
    output_workflow(dispatch) == [result];
    step_name(dispatch_step) == dispatch_name;
    step_instruction(dispatch_step) == "{instruction}";
    step_executor(dispatch_step) == worker;
    consumes(dispatch_step) == [request];
    produces(dispatch_step) == [result];
}}
"""


def _select_workflow(condition: str) -> str:
    return f"""
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

workflow select_demo {{
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

    selected_result == if({condition}, primary_result, fallback_result);

    step_name(final_step) == final_name;
    step_instruction(final_step) == "consume_selected";
    step_executor(final_step) == worker;
    consumes(final_step) == [selected_result];
    produces(final_step) == [final_result];
}}
"""


@pytest.mark.anyio
async def test_in_memory_workflow_compiles_and_executes() -> None:
    prompts: list[str] = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "completed"

    result = await run_workflow.execute_workflow(
        _dispatch_workflow("Agent", "summarize_request"),
        request="Explain structured concurrency.",
        complete=complete,
    )

    assert result == {"result": "completed"}
    assert prompts[0].splitlines()[0] == "Instruction: summarize_request"


def test_runner_compiles_ordered_select_condition() -> None:
    compiled = run_workflow.compile_workflow(_select_workflow("request >= 10"))

    assert compiled.graph.to_dict()["selectors"][0] == {
        "output_artifact_id": "selected_result",
        "when_true_artifact_id": "primary_result",
        "when_false_artifact_id": "fallback_result",
        "condition": {
            "kind": "comparison",
            "operator": "gte",
            "left": {"kind": "artifact", "artifact_id": "request"},
            "right": {"kind": "literal", "value": 10},
        },
    }


@pytest.mark.anyio
async def test_named_select_executes_both_candidates_and_feeds_final_step() -> None:
    prompts: dict[str, str] = {}

    async def complete(prompt: str) -> str:
        instruction = prompt.splitlines()[0].removeprefix("Instruction: ")
        prompts[instruction] = prompt
        if instruction == "produce_primary":
            return "PRIMARY"
        if instruction == "produce_fallback":
            return "FALLBACK"
        assert instruction == "consume_selected"
        assert prompt.splitlines()[1] == 'Inputs: {"selected_result": "PRIMARY"}'
        return "FINAL"

    result = await run_workflow.execute_workflow(
        _select_workflow('request = "primary"'),
        request="primary",
        complete=complete,
    )

    assert result == {
        "final_result": "FINAL",
        "selected_result": "PRIMARY",
    }
    assert set(prompts) == {
        "consume_selected",
        "produce_fallback",
        "produce_primary",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("executor_kind", "instruction"),
    [
        ("Agent", "./instructions/missing-agent.txt"),
        ("Program", "./instructions/missing-program.txt"),
    ],
)
async def test_non_human_executor_receives_instruction_path_unchanged(
    executor_kind: str,
    instruction: str,
) -> None:
    prompts: list[str] = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "completed"

    result = await run_workflow.execute_workflow(
        _dispatch_workflow(executor_kind, instruction),
        request="Do the work.",
        complete=complete,
    )

    assert result == {"result": "completed"}
    assert prompts[0].splitlines()[0] == f"Instruction: {instruction}"
    assert f'"{instruction}"' not in prompts[0]


def test_untyped_executor_defaults_to_agent_for_compatibility() -> None:
    compiled = run_workflow.compile_workflow(_dispatch_workflow(None, "./instructions/untyped-agent.txt"))

    assert compiled.executor_kinds == {"worker": "Agent"}


def test_strict_runner_rejects_untyped_executor() -> None:
    with pytest.raises(ValueError, match="must be declared as exactly one"):
        run_workflow.compile_workflow(
            _dispatch_workflow(None, "./instructions/untyped-agent.txt"),
            strict_executors=True,
        )


@pytest.mark.anyio
async def test_human_instruction_is_prepared_by_agent_before_request() -> None:
    preparation_prompts: list[str] = []
    human_prompts: list[str] = []

    async def complete(prompt: str) -> str:
        pytest.fail(f"ordinary completion called with {prompt!r}")

    async def prepare_human_instruction(prompt: str) -> str:
        preparation_prompts.append(prompt)
        return "Review the supplied proposal and answer approve or reject."

    async def request_human(prompt: str) -> object:
        human_prompts.append(prompt)
        return {"decision": "approve"}

    result = await run_workflow.execute_workflow(
        _dispatch_workflow("Human", "./instructions/../proposal.txt"),
        request="proposal-v2",
        complete=complete,
        prepare_human_instruction=prepare_human_instruction,
        request_human=request_human,
    )

    assert result == {"result": {"decision": "approve"}}
    assert len(preparation_prompts) == 1
    assert "Step: dispatch_step" in preparation_prompts[0]
    assert "Instruction or reference: ./instructions/../proposal.txt" in preparation_prompts[0]
    assert '"request": "proposal-v2"' in preparation_prompts[0]
    assert "available tools and normal approval flow" in preparation_prompts[0]
    assert human_prompts == ["Review the supplied proposal and answer approve or reject."]


@pytest.mark.anyio
async def test_human_preparation_failure_does_not_request_human() -> None:
    async def complete(prompt: str) -> str:
        pytest.fail(f"ordinary completion called with {prompt!r}")

    async def prepare_human_instruction(prompt: str) -> str:
        del prompt
        raise PermissionError("resource access was not approved")

    async def request_human(prompt: str) -> str:
        pytest.fail(f"human called with {prompt!r}")

    with pytest.RaisesGroup(pytest.RaisesExc(PermissionError, match="not approved")):
        await run_workflow.execute_workflow(
            _dispatch_workflow("Human", "./private/reference.txt"),
            request="review",
            complete=complete,
            prepare_human_instruction=prepare_human_instruction,
            request_human=request_human,
        )


@pytest.mark.anyio
async def test_human_preparation_must_return_text() -> None:
    async def complete(prompt: str) -> str:
        pytest.fail(f"ordinary completion called with {prompt!r}")

    async def prepare_human_instruction(prompt: str) -> str:
        del prompt
        return "  "

    async def request_human(prompt: str) -> str:
        pytest.fail(f"human called with {prompt!r}")

    with pytest.RaisesGroup(pytest.RaisesExc(ValueError, match="preparation returned no text")):
        await run_workflow.execute_workflow(
            _dispatch_workflow("Human", "review_reference"),
            request="review",
            complete=complete,
            prepare_human_instruction=prepare_human_instruction,
            request_human=request_human,
        )


@pytest.mark.anyio
async def test_human_step_requires_preparer_and_requester() -> None:
    async def complete(prompt: str) -> str:
        pytest.fail(f"ordinary completion called with {prompt!r}")

    with pytest.RaisesGroup(pytest.RaisesExc(ValueError, match="requires prepare_human_instruction and request_human")):
        await run_workflow.execute_workflow(
            _dispatch_workflow("Human", "review_reference"),
            request="review",
            complete=complete,
        )


def test_unconsumed_assertions_report_operator_counts() -> None:
    context = run_workflow._default_parse_context()
    context.operators["custom_policy"] = run_workflow.Operator(
        name="custom_policy",
        output_concept=context.concepts["Bool"],
    )
    source = _dispatch_workflow("Agent", "do_work").replace(
        "\n}",
        "\n    custom_policy(dispatch_step) == True;\n}",
    )

    with pytest.raises(
        ValueError,
        match=r"unconsumed assertions: custom_policy=1",
    ):
        run_workflow.compile_workflow(source, context=context)


@pytest.mark.anyio
async def test_contextual_completion_receives_resource_lease() -> None:
    source = (
        _dispatch_workflow("Agent", "use_gpu")
        .replace(
            "const result: Artifact;",
            "const result: Artifact;\nconst gpu: Resource;",
        )
        .replace(
            "\n}",
            "\n    resource_requirement(dispatch_step, gpu) == 1;\n}",
        )
    )
    leases: list[tuple[str, ...]] = []

    async def contextual_complete(
        prompt: str,
        context: Any,
    ) -> dict[str, object]:
        assert prompt.startswith("Instruction: use_gpu")
        leases.append(context.dispatch.resource_lease.instances("gpu"))
        return {"result": "completed"}

    result = await run_workflow.execute_workflow(
        source,
        request="Do the work.",
        contextual_complete=contextual_complete,
        resource_capacities={"gpu": ("cuda:0",)},
    )

    assert result == {"result": "completed"}
    assert leases == [("cuda:0",)]


@pytest.mark.anyio
async def test_depends_on_orders_steps_without_an_artifact_dependency() -> None:
    source = """
const explicit_order: Workflow;
const after_step: Step;
const before_step: Step;
const after_name: StepName;
const before_name: StepName;
const worker: Agent;
const request: Artifact;
const after_result: Artifact;
const before_result: Artifact;

workflow explicit_order {
    input_workflow(explicit_order) == [request];
    output_workflow(explicit_order) == [after_result, before_result];

    step_name(after_step) == after_name;
    step_instruction(after_step) == "after";
    step_executor(after_step) == worker;
    consumes(after_step) == [request];
    produces(after_step) == [after_result];

    step_name(before_step) == before_name;
    step_instruction(before_step) == "before";
    step_executor(before_step) == worker;
    consumes(before_step) == [request];
    produces(before_step) == [before_result];

    depends_on(after_step, before_step) == True;
}
"""
    before_finished = False

    async def complete(prompt: str) -> str:
        nonlocal before_finished
        instruction = prompt.splitlines()[0].removeprefix("Instruction: ")
        if instruction == "before":
            before_finished = True
            return "BEFORE"
        assert instruction == "after"
        assert before_finished
        return "AFTER"

    result = await run_workflow.execute_workflow(
        source,
        request="run",
        complete=complete,
    )

    assert result == {
        "after_result": "AFTER",
        "before_result": "BEFORE",
    }


@pytest.mark.anyio
async def test_legacy_completion_can_return_multiple_named_outputs() -> None:
    source = (
        _dispatch_workflow("Agent", "produce_two")
        .replace(
            "const result: Artifact;",
            "const result: Artifact;\nconst extra: Artifact;",
        )
        .replace(
            "output_workflow(dispatch) == [result];",
            "output_workflow(dispatch) == [result, extra];",
        )
        .replace(
            "produces(dispatch_step) == [result];",
            "produces(dispatch_step) == [result, extra];",
        )
    )

    async def complete(prompt: str) -> dict[str, object]:
        del prompt
        return {"result": "first", "extra": "second"}

    result = await run_workflow.execute_workflow(
        source,
        request="Do the work.",
        complete=complete,
    )

    assert result == {"extra": "second", "result": "first"}


@pytest.mark.anyio
async def test_legacy_single_output_preserves_same_named_mapping_artifact() -> None:
    async def complete(prompt: str) -> dict[str, object]:
        del prompt
        return {"result": "mapping artifact"}

    result = await run_workflow.execute_workflow(
        _dispatch_workflow("Agent", "return_mapping"),
        request="Do the work.",
        complete=complete,
    )

    assert result == {"result": {"result": "mapping artifact"}}
