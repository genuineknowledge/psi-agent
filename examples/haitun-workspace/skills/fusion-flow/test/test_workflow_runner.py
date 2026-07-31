from __future__ import annotations

import importlib
import json
from typing import Any, cast

import pytest

run_workflow = cast(Any, importlib.import_module("fusion_flow.workflow_runner"))


class _StringableNonJson:
    def __str__(self) -> str:
        return "must-not-be-stringified"


def test_runner_catalog_includes_typed_depends_on() -> None:
    context = run_workflow._default_parse_context()

    depends_on = context.operators["depends_on"]
    assert tuple(concept.name for concept in depends_on.input_concepts) == (
        "Step",
        "Step",
    )
    assert depends_on.output_concept == context.concepts["Bool"]
    program_path = context.operators["program_path"]
    assert tuple(concept.name for concept in program_path.input_concepts) == ("Program",)
    assert program_path.output_concept == context.concepts["Path"]


def _dispatch_workflow(
    executor_kind: str | None,
    instruction: str,
    *,
    executor_configuration: str = "",
) -> str:
    executor_declaration = "" if executor_kind is None else f"const worker: {executor_kind};"
    return f"""
const dispatch: Workflow;
const dispatch_step: Step;
{executor_declaration}
const request: Artifact;
const result: Artifact;

workflow dispatch {{
    input_workflow(dispatch) == [request];
    output_workflow(dispatch) == [result];
    {executor_configuration}
    step_name(dispatch_step) == "Dispatch";
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
const worker: Agent;
const request: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

workflow select_demo {{
    input_workflow(select_demo) == [request];
    output_workflow(select_demo) == [selected_result, final_result];

    step_name(primary_step) == "Primary";
    step_instruction(primary_step) == "produce_primary";
    step_executor(primary_step) == worker;
    consumes(primary_step) == [request];
    produces(primary_step) == [primary_result];

    step_name(fallback_step) == "Fallback";
    step_instruction(fallback_step) == "produce_fallback";
    step_executor(fallback_step) == worker;
    consumes(fallback_step) == [request];
    produces(fallback_step) == [fallback_result];

    selected_result == if({condition}, primary_result, fallback_result);

    step_name(final_step) == "Final";
    step_instruction(final_step) == "consume_selected";
    step_executor(final_step) == worker;
    consumes(final_step) == [selected_result];
    produces(final_step) == [final_result];
}}
"""


def test_compile_workflow_uses_string_step_name_without_suffix() -> None:
    compiled = run_workflow.compile_workflow(_dispatch_workflow("Agent", "do_work"))

    assert compiled.graph.steps[0].name_id == "Dispatch"
    assert isinstance(compiled.graph.steps[0].name_id, str)


@pytest.mark.parametrize(
    "configuration",
    [
        'program_path(worker) == "./bin/worker";',
        '"./bin/worker" == program_path(worker);',
    ],
)
def test_compile_workflow_extracts_program_path(configuration: str) -> None:
    compiled = run_workflow.compile_workflow(
        _dispatch_workflow(
            "Program",
            "do_work",
            executor_configuration=configuration,
        )
    )

    assert compiled.program_paths == {"worker": "./bin/worker"}


def test_compile_workflow_requires_program_path() -> None:
    with pytest.raises(ValueError, match="has no program_path"):
        run_workflow.compile_workflow(_dispatch_workflow("Program", "do_work"))


def test_compile_workflow_rejects_duplicate_program_path() -> None:
    with pytest.raises(ValueError, match="duplicate program_path"):
        run_workflow.compile_workflow(
            _dispatch_workflow(
                "Program",
                "do_work",
                executor_configuration="""
                program_path(worker) == "./bin/first";
                program_path(worker) == "./bin/second";
                """,
            )
        )


@pytest.mark.anyio
async def test_in_memory_workflow_compiles_and_executes() -> None:
    prompts: list[str] = []
    instruction = "Explain the request, identify the key trade-offs, and return a concise recommendation."

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "completed"

    result = await run_workflow.execute_workflow(
        _dispatch_workflow("Agent", instruction),
        request="Explain structured concurrency.",
        complete=complete,
    )

    assert result == {"result": "completed"}
    assert prompts[0].startswith(f"Instruction:\n{instruction}\n\nInputs: ")


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
        instruction = prompt.split("\n\n", 1)[0].removeprefix("Instruction:\n")
        prompts[instruction] = prompt
        if instruction == "produce_primary":
            return "PRIMARY"
        if instruction == "produce_fallback":
            return "FALLBACK"
        assert instruction == "consume_selected"
        assert 'Inputs: {"selected_result": "PRIMARY"}' in prompt
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
async def test_instruction_path_requires_a_resolver_before_dispatch() -> None:
    instruction = "./instructions/missing-agent.md"

    async def complete(prompt: str) -> str:
        pytest.fail(f"completion called with {prompt!r}")

    with pytest.raises(ValueError, match="instruction path but no instruction resolver"):
        await run_workflow.execute_workflow(
            _dispatch_workflow("Agent", instruction),
            request="Do the work.",
            complete=complete,
        )


@pytest.mark.anyio
async def test_agent_executor_receives_resolved_instruction_text() -> None:
    prompts: list[str] = []
    references: list[str] = []
    reference = "./instructions/research.md"
    instruction = (
        "Research semantic parsing methods and representative systems.\n"
        "Cite the supplied evidence, distinguish findings from inference, and summarize limitations."
    )

    async def resolve_instruction(value: str) -> str:
        references.append(value)
        return instruction

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "completed"

    result = await run_workflow.execute_workflow(
        _dispatch_workflow("Agent", reference),
        request="Survey semantic parsing.",
        complete=complete,
        resolve_instruction=resolve_instruction,
    )

    assert result == {"result": "completed"}
    assert references == [reference]
    assert prompts[0].startswith(f"Instruction:\n{instruction}\n\nInputs: ")
    assert reference not in prompts[0]


@pytest.mark.anyio
async def test_instruction_resolver_must_return_non_empty_text() -> None:
    async def resolve_instruction(reference: str) -> str:
        assert reference == "./instructions/empty.md"
        return " \n"

    async def complete(prompt: str) -> str:
        pytest.fail(f"completion called with {prompt!r}")

    with pytest.raises(ValueError, match="instruction resolved to no text"):
        await run_workflow.execute_workflow(
            _dispatch_workflow("Agent", "./instructions/empty.md"),
            request="Do the work.",
            complete=complete,
            resolve_instruction=resolve_instruction,
        )


@pytest.mark.anyio
async def test_instruction_files_are_materialized_once_before_dispatch() -> None:
    source = _select_workflow('request = "primary"')
    for instruction in ("produce_primary", "produce_fallback", "consume_selected"):
        source = source.replace(f'"{instruction}"', '"./instructions/shared.md"')
    resolutions: list[str] = []
    dispatched: list[str] = []

    async def resolve_instruction(reference: str) -> str:
        resolutions.append(reference)
        return "Perform the assigned step using its inputs and return the exact requested output."

    async def complete(prompt: str, context: Any) -> dict[str, object]:
        del prompt
        dispatched.append(context.step_id)
        values: dict[str, dict[str, object]] = {
            "primary_step": {"primary_result": "PRIMARY"},
            "fallback_step": {"fallback_result": "FALLBACK"},
            "final_step": {"final_result": "FINAL"},
        }
        return values[context.step_id]

    result = await run_workflow.execute_workflow(
        source,
        request="primary",
        contextual_complete=complete,
        resolve_instruction=resolve_instruction,
    )

    assert result == {
        "final_result": "FINAL",
        "selected_result": "PRIMARY",
    }
    assert resolutions == ["./instructions/shared.md"]
    assert set(dispatched) == {"primary_step", "fallback_step", "final_step"}


@pytest.mark.anyio
async def test_program_path_is_executed_with_instruction_and_inputs(
    tmp_path: Any,
) -> None:
    calls: list[Any] = []
    path_references: list[str] = []
    instruction = "Run the configured worker on the supplied request and return its result."
    inputs = {"request": {"topic": "structured concurrency"}}

    async def resolve_path(reference: str) -> str:
        path_references.append(reference)
        return "./bin/worker"

    async def execute_program(invocation: Any) -> str:
        calls.append(invocation)
        return "completed"

    source = _dispatch_workflow(
        "Program",
        instruction,
        executor_configuration="program_path(worker) == worker_path;",
    ).replace(
        "const result: Artifact;",
        "const worker_path: Path;\nconst result: Artifact;",
    )

    result = await run_workflow.execute_workflow(
        source,
        inputs=inputs,
        resolve_path=resolve_path,
        work_dir=tmp_path,
        run_program=execute_program,
    )

    assert result == {"result": "completed"}
    assert path_references == ["worker_path"]
    assert len(calls) == 1
    invocation = calls[0]
    assert invocation.name == "worker"
    assert invocation.argv == ("./bin/worker",)
    assert invocation.cwd == tmp_path
    assert invocation.binding_name == "dispatch_step"
    assert invocation.instruction == instruction
    assert invocation.inputs == inputs
    assert invocation.output_ids == ("result",)
    assert invocation.stdin == (
        json.dumps(
            {
                "instruction": instruction,
                "inputs": inputs,
            },
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def _program_workflow_with_two_outputs() -> str:
    return (
        _dispatch_workflow(
            "Program",
            "produce_two",
            executor_configuration='program_path(worker) == "./bin/worker";',
        )
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


@pytest.mark.anyio
async def test_program_runner_mapping_uses_exact_output_ids(
    tmp_path: Any,
) -> None:
    output_ids: list[tuple[str, ...]] = []

    async def execute_program(invocation: Any) -> dict[str, object]:
        output_ids.append(invocation.output_ids)
        return {
            "result": "first",
            "extra": {"rank": 2},
        }

    result = await run_workflow.execute_workflow(
        _program_workflow_with_two_outputs(),
        request="Do the work.",
        work_dir=tmp_path,
        run_program=execute_program,
    )

    assert output_ids == [("extra", "result")]
    assert result == {
        "extra": {"rank": 2},
        "result": "first",
    }


@pytest.mark.parametrize(
    "program_result",
    (
        pytest.param(
            {"result": "first"},
            id="missing-key",
        ),
        pytest.param(
            {
                "extra": {"rank": 2},
                "result": "first",
                "surplus": True,
            },
            id="extra-key",
        ),
    ),
)
@pytest.mark.anyio
async def test_program_runner_mapping_rejects_non_exact_output_ids(
    tmp_path: Any,
    program_result: dict[str, object],
) -> None:
    async def execute_program(invocation: Any) -> dict[str, object]:
        assert invocation.output_ids == ("extra", "result")
        return program_result

    with pytest.RaisesGroup(pytest.RaisesExc(ValueError, match="must match exactly")):
        await run_workflow.execute_workflow(
            _program_workflow_with_two_outputs(),
            request="Do the work.",
            work_dir=tmp_path,
            run_program=execute_program,
        )


@pytest.mark.parametrize(
    "invalid_input",
    (
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(_StringableNonJson(), id="stringable-non-json"),
    ),
)
@pytest.mark.anyio
async def test_program_inputs_must_be_strict_finite_json(
    tmp_path: Any,
    invalid_input: object,
) -> None:
    async def execute_program(invocation: Any) -> str:
        pytest.fail(f"program called with {invocation!r}")

    with pytest.RaisesGroup(
        pytest.RaisesExc(
            ValueError,
            match=r"Program step 'dispatch_step' inputs must be finite JSON values",
        )
    ):
        await run_workflow.execute_workflow(
            _dispatch_workflow(
                "Program",
                "do_work",
                executor_configuration='program_path(worker) == "./bin/worker";',
            ),
            inputs={"request": {"value": invalid_input}},
            work_dir=tmp_path,
            run_program=execute_program,
        )


@pytest.mark.anyio
async def test_program_multiple_outputs_are_read_from_json_stdout(
    tmp_path: Any,
) -> None:
    async def execute_program(invocation: Any) -> str:
        del invocation
        return '{"result": "first", "extra": {"rank": 2}}'

    result = await run_workflow.execute_workflow(
        _program_workflow_with_two_outputs(),
        request="Do the work.",
        work_dir=tmp_path,
        run_program=execute_program,
    )

    assert result == {
        "extra": {"rank": 2},
        "result": "first",
    }


@pytest.mark.parametrize(
    "invalid_value",
    (
        "NaN",
        "Infinity",
        "-Infinity",
        "1e400",
        '{"nested": NaN}',
        '{"duplicate": 1, "duplicate": 2}',
    ),
)
@pytest.mark.anyio
async def test_program_multiple_outputs_reject_non_finite_json(
    tmp_path: Any,
    invalid_value: str,
) -> None:
    async def execute_program(invocation: Any) -> str:
        del invocation
        return f'{{"result": {invalid_value}, "extra": 1}}'

    with pytest.RaisesGroup(pytest.RaisesExc(ValueError, match="strict JSON object")):
        await run_workflow.execute_workflow(
            _program_workflow_with_two_outputs(),
            request="Do the work.",
            work_dir=tmp_path,
            run_program=execute_program,
        )


@pytest.mark.anyio
async def test_relative_program_path_requires_work_dir() -> None:
    with pytest.raises(
        ValueError,
        match="relative program_path requires an explicit work_dir",
    ):
        await run_workflow.execute_workflow(
            _dispatch_workflow(
                "Program",
                "do_work",
                executor_configuration='program_path(worker) == "./bin/worker";',
            ),
            request="Do the work.",
        )


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
        _dispatch_workflow("Human", "Ask the reviewer to approve the proposal or request concrete edits."),
        request="proposal-v2",
        complete=complete,
        prepare_human_instruction=prepare_human_instruction,
        request_human=request_human,
    )

    assert result == {"result": {"decision": "approve"}}
    assert len(preparation_prompts) == 1
    assert "Step: dispatch_step" in preparation_prompts[0]
    assert (
        "Instruction:\nAsk the reviewer to approve the proposal or request concrete edits.\n\n"
        in preparation_prompts[0]
    )
    assert '"request": "proposal-v2"' in preparation_prompts[0]
    assert "inspect supporting resources named by the inputs" in preparation_prompts[0]
    assert "Do not ask the human directly" in preparation_prompts[0]
    assert human_prompts == ["Review the supplied proposal and answer approve or reject."]


@pytest.mark.anyio
async def test_contextual_human_callbacks_receive_step_contract() -> None:
    contexts: list[Any] = []

    async def prepare_human_instruction(prompt: str, context: Any) -> str:
        assert "Instruction:\nreview_reference\n\n" in prompt
        contexts.append(context)
        return "Provide approval or detailed edits."

    async def request_human(prompt: str, context: Any) -> object:
        assert prompt == "Provide approval or detailed edits."
        contexts.append(context)
        return "Tighten the conclusion."

    result = await run_workflow.execute_workflow(
        _dispatch_workflow("Human", "review_reference"),
        request="proposal-v2",
        contextual_prepare_human_instruction=prepare_human_instruction,
        contextual_request_human=request_human,
    )

    assert result == {"result": "Tighten the conclusion."}
    assert len(contexts) == 2
    assert contexts[0] is contexts[1]
    assert contexts[0].step_id == "dispatch_step"
    assert contexts[0].executor_id == "worker"
    assert contexts[0].executor_kind == "Human"
    assert contexts[0].inputs == {"request": "proposal-v2"}
    assert contexts[0].output_ids == ("result",)


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
            _dispatch_workflow("Human", "Prepare a review question from the supplied request."),
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
        assert prompt.startswith("Instruction:\nuse_gpu\n\n")
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
const worker: Agent;
const request: Artifact;
const after_result: Artifact;
const before_result: Artifact;

workflow explicit_order {
    input_workflow(explicit_order) == [request];
    output_workflow(explicit_order) == [after_result, before_result];

    step_name(after_step) == "After";
    step_instruction(after_step) == "after";
    step_executor(after_step) == worker;
    consumes(after_step) == [request];
    produces(after_step) == [after_result];

    step_name(before_step) == "Before";
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
        instruction = prompt.split("\n\n", 1)[0].removeprefix("Instruction:\n")
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
