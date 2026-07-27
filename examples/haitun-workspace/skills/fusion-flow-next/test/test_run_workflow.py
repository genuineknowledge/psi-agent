from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, cast

import pytest

_SKILL_DIR = os.path.dirname(os.path.dirname(__file__))
_RUNNER_PATH = os.path.join(_SKILL_DIR, "examples", "run_workflow.py")


def _load_module(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(Any, module)


run_workflow = _load_module("fusion_flow_next_workflow_runner", _RUNNER_PATH)


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


@pytest.mark.anyio
async def test_untyped_executor_defaults_to_agent() -> None:
    prompts: list[str] = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        return "completed"

    instruction = "./instructions/untyped-agent.txt"
    result = await run_workflow.execute_workflow(
        _dispatch_workflow(None, instruction),
        request="Do the work.",
        complete=complete,
    )

    assert result == {"result": "completed"}
    assert prompts[0].splitlines()[0] == f"Instruction: {instruction}"


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
