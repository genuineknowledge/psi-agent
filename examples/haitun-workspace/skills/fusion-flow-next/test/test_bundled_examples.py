from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, cast

import anyio
import pytest

_SKILL_DIR = os.path.dirname(os.path.dirname(__file__))
_EXAMPLES_DIR = os.path.join(_SKILL_DIR, "examples")
_RUNNER_PATH = os.path.join(_EXAMPLES_DIR, "run_workflow.py")


def _load_module(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(Any, module)


run_workflow = _load_module(
    "fusion_flow_next_bundled_example_runner",
    _RUNNER_PATH,
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "expected_calls", "expected_result"),
    [
        ("single_step.workflow", 1, "answer_the_request: done"),
        ("sequential.workflow", 2, "polish_for_clarity: done"),
        ("parallel_join.workflow", 3, "combine_benefits_and_risks: done"),
    ],
)
async def test_bundled_example_executes(
    filename: str,
    expected_calls: int,
    expected_result: str,
) -> None:
    source = await anyio.Path(os.path.join(_EXAMPLES_DIR, filename)).read_text(encoding="utf-8")
    prompts: list[str] = []

    async def complete(prompt: str) -> str:
        prompts.append(prompt)
        instruction = prompt.splitlines()[0].removeprefix("Instruction: ")
        return f"{instruction}: done"

    result = await run_workflow.execute_workflow(
        source,
        inputs={"request": "Explain one benefit and one risk of structured concurrency."},
        complete=complete,
        strict_executors=True,
    )

    assert result == {"result": expected_result}
    assert len(prompts) == expected_calls
