from __future__ import annotations

from oracle import evaluate_response

VALID_NAMED_SELECT = """```fusionflow
const select_demo: Workflow;
const primary_step: Step;
const fallback_step: Step;
const final_step: Step;
const primary_name: StepName;
const fallback_name: StepName;
const final_name: StepName;
const worker: Agent, Executor;
const model: Model;
const engine: Engine;
const api: ApiBase;
const request: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

workflow select_demo {
    input_workflow(select_demo) == [request];
    output_workflow(select_demo) == [final_result];
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
    selected_result == if(request = True, primary_result, fallback_result);
    step_name(final_step) == final_name;
    step_instruction(final_step) == "consume_selected";
    step_executor(final_step) == worker;
    consumes(final_step) == [selected_result];
    produces(final_step) == [final_result];
    agent_config(worker, model, engine, api);
}
```"""


def test_valid_named_selection_passes() -> None:
    case = {
        "id": "fixture",
        "kind": "positive",
        "oracle": {
            "response": "single_fusionflow_fence",
            "compiles": True,
            "named_selector_count": 1,
            "selector_chain_depth": 1,
            "eager_candidate_step_count": 2,
            "final_step_consumes_selector_outputs": 1,
        },
    }

    result = evaluate_response(case, VALID_NAMED_SELECT)

    assert result["passed"] is True


def test_inline_if_in_consumes_fails() -> None:
    answer = VALID_NAMED_SELECT.replace(
        "selected_result == if(request = True, primary_result, fallback_result);",
        "",
    ).replace(
        "consumes(final_step) == [selected_result];",
        "consumes(final_step) == [if(request = True, primary_result, fallback_result)];",
    )
    case = {
        "id": "fixture",
        "kind": "positive",
        "oracle": {
            "response": "single_fusionflow_fence",
            "compiles": True,
            "named_selector_count": 1,
            "inline_if_in_consumes_count": 0,
        },
    }

    result = evaluate_response(case, answer)

    assert result["passed"] is False
    assert result["predicates"]["compiles"] is False
