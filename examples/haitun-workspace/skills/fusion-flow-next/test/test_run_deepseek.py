from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest

from psi_agent.workflow_execution import ExecutionPlanError

_SKILL_DIR = os.path.dirname(os.path.dirname(__file__))

run_deepseek = cast(Any, importlib.import_module("examples.run_deepseek"))


def test_parse_mapping_requires_json_object() -> None:
    assert run_deepseek._parse_mapping('{"request": "hello"}', label="inputs") == {"request": "hello"}

    with pytest.raises(ValueError, match="inputs must be a JSON object"):
        run_deepseek._parse_mapping('["hello"]', label="inputs")


def test_parse_resource_capacities_accepts_counts_and_instance_ids() -> None:
    assert run_deepseek._parse_resource_capacities(
        '{"gpu_device": 2, "license": ["license-a"]}',
        label="resources",
    ) == {
        "gpu_device": 2,
        "license": ("license-a",),
    }

    with pytest.raises(ValueError, match="integer capacity or an array"):
        run_deepseek._parse_resource_capacities(
            '{"gpu_device": "all"}',
            label="resources",
        )


@pytest.mark.anyio
async def test_module_cli_help_starts_from_documented_working_directory() -> None:
    completed = await anyio.run_process(
        [sys.executable, "-m", "examples.run_deepseek", "--help"],
        cwd=_SKILL_DIR,
        check=True,
    )

    assert completed.stdout is not None
    assert b"--resource-capacities" in completed.stdout


@pytest.mark.anyio
async def test_read_inputs_from_file(tmp_path: Any) -> None:
    inputs_path = os.path.join(str(tmp_path), "inputs.json")
    await anyio.Path(inputs_path).write_text('{"request": "hello"}', encoding="utf-8")

    assert await run_deepseek._read_inputs(None, inputs_path) == {"request": "hello"}


@pytest.mark.anyio
async def test_deepseek_completion_returns_named_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, object]] = []

    async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
        requests.append(dict(kwargs))
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"draft": "short answer"}',
                    )
                )
            ]
        )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(run_deepseek, "acompletion", fake_acompletion)

    result = await run_deepseek._complete_with_deepseek('Outputs: ["draft"]')

    assert result == {"draft": "short answer"}
    assert requests[0]["response_format"] == {"type": "json_object"}


@pytest.mark.anyio
async def test_resource_preflight_failure_sends_no_deepseek_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = 0

    async def fake_acompletion(**kwargs: object) -> SimpleNamespace:
        nonlocal requests
        del kwargs
        requests += 1
        return SimpleNamespace()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(run_deepseek, "acompletion", fake_acompletion)
    source = """
const resource_flow: Workflow;
const work_step: Step;
const work_name: StepName;
const work_instruction: Instruction;
const worker: Agent;
const request: Artifact;
const result: Artifact;
const gpu_device: Resource;

workflow resource_flow {
    input_workflow(resource_flow) == [request];
    output_workflow(resource_flow) == [result];
    step_name(work_step) == work_name;
    step_instruction(work_step) == work_instruction;
    step_executor(work_step) == worker;
    consumes(work_step) == [request];
    produces(work_step) == [result];
    resource_requirement(work_step, gpu_device) == 1;
}
"""

    with pytest.raises(ExecutionPlanError, match="capacities or an allocator"):
        await run_deepseek.execute_workflow(
            source,
            inputs={"request": "do work"},
            contextual_complete=run_deepseek._contextual_complete_with_deepseek,
            strict_executors=True,
        )

    assert requests == 0
