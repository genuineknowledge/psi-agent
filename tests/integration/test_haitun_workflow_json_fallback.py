from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from anyio.lowlevel import checkpoint

TOOLS_DIR = Path(__file__).parents[2] / "examples" / "haitun-workspace" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

workflow_tool = import_module("run_flow")


class _EmptyToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def get(self, _name: str) -> None:
        return None


def _agent_context(*output_ids: str) -> Any:
    return SimpleNamespace(
        step_id="review",
        executor_id="reviewer",
        output_ids=output_ids,
        dispatch=SimpleNamespace(
            resource_lease=SimpleNamespace(grants=()),
            invocation_id="review",
            iteration_index=None,
            attempt=1,
        ),
    )


@pytest.mark.anyio
async def test_only_the_third_agent_response_calls_json_repair_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        '{"artifact":"first",}',
        '{"artifact":"second",}',
        '{"artifact":"final",}',
    ]
    prompts: list[str] = []
    repair_calls: list[tuple[str, dict[str, object]]] = []
    real_repair_json = workflow_tool.repair_json

    async def create_step_agent(*_args: object, **_kwargs: object) -> tuple[object, SimpleNamespace]:
        await checkpoint()
        return object(), SimpleNamespace(messages=[])

    async def complete_step_agent(
        _agent: object,
        _conversation: object,
        message: str,
        **_kwargs: object,
    ) -> str:
        await checkpoint()
        prompts.append(message)
        return responses.pop(0)

    def repair_json_spy(value: str, **kwargs: object) -> str:
        repair_calls.append((value, kwargs))
        return cast(str, real_repair_json(value, **kwargs))

    monkeypatch.setattr(workflow_tool, "_create_step_agent", create_step_agent)
    monkeypatch.setattr(workflow_tool, "_complete_step_agent", complete_step_agent)
    monkeypatch.setattr(workflow_tool, "repair_json", repair_json_spy)

    result = await workflow_tool._complete_agent_step(
        "Review the input.",
        _agent_context("artifact"),
        ai_socket="unused",
        tool_registry=cast(Any, _EmptyToolRegistry()),
    )

    assert result == {"artifact": "final"}
    assert len(prompts) == 3
    assert repair_calls == [
        (
            '{"artifact":"final",}',
            {
                "return_objects": False,
                "skip_json_loads": True,
                "logging": False,
                "stream_stable": False,
                "strict": True,
                "ensure_ascii": False,
            },
        )
    ]


def test_fallback_repairs_nested_trailing_commas_without_touching_strings() -> None:
    result, repair_count, response_form = workflow_tool._parse_agent_step_result_with_json_repair(
        '```json\n{"artifact":{"literal":",} and ,]","items":[1,2,],},}\n```',
        step_id="review",
        output_ids=("artifact",),
    )

    assert result == {"artifact": {"literal": ",} and ,]", "items": [1, 2]}}
    assert repair_count == 3
    assert response_form == "json_fence"


def test_fallback_preserves_the_exact_multi_output_contract() -> None:
    result, repair_count, response_form = workflow_tool._parse_agent_step_result_with_json_repair(
        '{"left":1,"right":2,}',
        step_id="review",
        output_ids=("left", "right"),
    )

    assert result == {"left": 1, "right": 2}
    assert repair_count == 1
    assert response_form == "raw"


@pytest.mark.parametrize(
    "response",
    [
        '{"artifact":1,"artifact":2,}',
        '{"artifact":,}',
        '{"artifact":NaN,}',
        '{"artifact":1e400,}',
        '{"artifact":1,}{"artifact":2}',
        '{"artifact":[1,,],}',
        'result:\n```json\n{"artifact":1,}\n```',
        '```json\n{"artifact":1,}\n```\n```json\n{"artifact":2}\n```',
    ],
)
def test_fallback_rejects_ambiguous_or_nonfinite_json_before_library(
    response: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_repair(*_args: object, **_kwargs: object) -> str:
        pytest.fail("invalid local trailing-comma candidate reached json-repair")

    monkeypatch.setattr(workflow_tool, "repair_json", unexpected_repair)

    with pytest.raises(ValueError):
        workflow_tool._parse_agent_step_result_with_json_repair(
            response,
            step_id="review",
            output_ids=("artifact",),
        )


def test_fallback_does_not_turn_a_comma_into_an_empty_zero_output_object() -> None:
    with pytest.raises(ValueError):
        workflow_tool._parse_agent_step_result_with_json_repair(
            "{,}",
            step_id="notification",
            output_ids=(),
        )


def test_fallback_still_requires_exact_output_keys() -> None:
    with pytest.raises(ValueError, match="must match exactly"):
        workflow_tool._parse_agent_step_result_with_json_repair(
            '{"unexpected":1,}',
            step_id="review",
            output_ids=("artifact",),
        )


@pytest.mark.parametrize(
    ("response", "output_ids", "library_result"),
    [
        ('{"artifact":1,}', ("artifact",), '{"artifact":2}'),
        ('{"artifact":1,}', ("artifact",), '{"artifact":true}'),
        ('{"left":1,"right":2,}', ("left", "right"), '{"left":1}'),
    ],
)
def test_fallback_rejects_json_repair_semantic_changes(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
    output_ids: tuple[str, ...],
    library_result: str,
) -> None:
    monkeypatch.setattr(workflow_tool, "repair_json", lambda *_args, **_kwargs: library_result)

    with pytest.raises(ValueError):
        workflow_tool._parse_agent_step_result_with_json_repair(
            response,
            step_id="review",
            output_ids=output_ids,
        )


@pytest.mark.parametrize("error", [ValueError("broken"), RecursionError("too deep")])
def test_fallback_fails_closed_when_json_repair_raises(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    def fail_repair(*_args: object, **_kwargs: object) -> str:
        raise error

    monkeypatch.setattr(workflow_tool, "repair_json", fail_repair)

    with pytest.raises(ValueError, match="json-repair failed"):
        workflow_tool._parse_agent_step_result_with_json_repair(
            '{"artifact":1,}',
            step_id="review",
            output_ids=("artifact",),
        )
