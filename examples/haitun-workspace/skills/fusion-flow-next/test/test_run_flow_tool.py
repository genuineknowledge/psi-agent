from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from psi_agent.session.tool_registry import ToolFunction

_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
_RUNNER_PATH = _WORKSPACE_DIR / "tools" / "run_flow.py"


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return cast(Any, module)


run_flow_tool = _load_module("fusion_flow_next_run_flow_tool", _RUNNER_PATH)

_ORDERED_RESOURCE_WORKFLOW = """
const ordered: Workflow;
const after_step: Step;
const before_step: Step;
const after_name: StepName;
const before_name: StepName;
const worker: Agent;
const gpu: Resource;
const request: Artifact;
const after_result: Artifact;
const before_result: Artifact;

workflow ordered {
    input_workflow(ordered) == [request];
    output_workflow(ordered) == [after_result, before_result];
    max_concurrency(ordered) == 2;

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
    resource_requirement(before_step, gpu) == 1;

    depends_on(after_step, before_step) == True;
}
"""


def test_run_flow_is_the_only_public_async_tool() -> None:
    public_async = {
        name
        for name, value in vars(run_flow_tool).items()
        if not name.startswith("_") and inspect.iscoroutinefunction(value)
    }
    assert public_async == {"run_flow"}

    tool = ToolFunction.from_callable(run_flow_tool.run_flow)
    assert set(tool.parameters["properties"]) == {
        "flow_path",
        "instructions_json",
        "inputs_json",
        "resource_capacities_json",
    }
    assert tool.parameters["required"] == ["flow_path", "instructions_json"]


@pytest.mark.anyio
async def test_inner_agent_is_ephemeral_single_round_and_has_no_tools() -> None:
    first_agent, first_conversation = await run_flow_tool._create_step_agent(
        "http://ai.example",
        "fusion-flow-next-first",
    )
    second_agent, second_conversation = await run_flow_tool._create_step_agent(
        "http://ai.example",
        "fusion-flow-next-second",
    )

    assert first_conversation.session_id == "fusion-flow-next-first"
    assert second_conversation.session_id == "fusion-flow-next-second"
    assert first_conversation.session_id != second_conversation.session_id
    assert first_agent._max_tool_rounds == 1
    assert second_agent._max_tool_rounds == 1
    assert first_agent._tool_registry.tools == {}
    assert second_agent._tool_registry.tools == {}


@pytest.mark.anyio
async def test_run_flow_executes_dependencies_with_unique_step_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "ordered.workflow")
    await flow_path.write_text(_ORDERED_RESOURCE_WORKFLOW, encoding="utf-8")
    prompts: list[str] = []
    session_ids: list[str] = []

    class FakeConversation:
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            self.messages: list[dict[str, object]] = [
                {"role": "system", "content": "step system prompt"},
            ]

    class FakeAgent:
        def __init__(self, conversation: FakeConversation) -> None:
            self.conversation = conversation

        async def run(
            self,
            user_message: dict[str, object],
            extra_params: dict[str, object] | None = None,
        ) -> Any:
            del extra_params
            prompt = cast(str, user_message["content"])
            prompts.append(prompt)
            if "Step: before_step\n" in prompt:
                content = '{"before_result": "BEFORE"}'
            elif "Step: after_step\n" in prompt:
                content = '{"after_result": "AFTER"}'
            else:
                raise AssertionError(f"unexpected prompt: {prompt}")
            self.conversation.messages.extend(
                [
                    user_message,
                    {"role": "assistant", "content": content},
                ]
            )
            if False:
                yield None

    async def create_step_agent(
        ai_socket: str,
        session_id: str,
    ) -> tuple[FakeAgent, FakeConversation]:
        assert ai_socket == "http://ai.example"
        session_ids.append(session_id)
        conversation = FakeConversation(session_id)
        return FakeAgent(conversation), conversation

    monkeypatch.setattr(run_flow_tool._paths, "resolve_workspace", lambda: anyio.Path(tmp_path))
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    result = await run_flow_tool.run_flow(
        "ordered.workflow",
        json.dumps(
            {
                "before": "Prepare the prerequisite result.",
                "after": "Use the prerequisite and finish.",
            }
        ),
        '{"request": "go"}',
        '{"gpu": ["gpu-7"]}',
    )

    assert json.loads(result) == {
        "after_result": "AFTER",
        "before_result": "BEFORE",
    }
    assert len(session_ids) == 2
    assert len(set(session_ids)) == 2
    assert "Instruction ID: before" in prompts[0]
    assert "Instruction body:\nPrepare the prerequisite result." in prompts[0]
    assert '"gpu": ["gpu-7"]' in prompts[0]
    assert "Instruction ID: after" in prompts[1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "instructions_json",
    [
        "{}",
        '{"before": "Prepare.", "after": "Finish.", "extra": "Unexpected."}',
        '{"before": "before", "after": "Finish."}',
        '{"before": 7, "after": "Finish."}',
        '{"before": "Prepare.", "before": "Override.", "after": "Finish."}',
        '{"before": NaN, "after": "Finish."}',
    ],
)
async def test_invalid_instruction_contract_never_creates_step_agent(
    instructions_json: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "ordered.workflow")
    await flow_path.write_text(_ORDERED_RESOURCE_WORKFLOW, encoding="utf-8")
    created = False

    async def create_step_agent(ai_socket: str, session_id: str) -> None:
        nonlocal created
        del ai_socket, session_id
        created = True

    monkeypatch.setattr(run_flow_tool._paths, "resolve_workspace", lambda: anyio.Path(tmp_path))
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    with pytest.raises(ValueError):
        await run_flow_tool.run_flow(
            "ordered.workflow",
            instructions_json,
            '{"request": "go"}',
            '{"gpu": 1}',
        )
    assert not created


@pytest.mark.anyio
async def test_program_is_rejected_before_creating_step_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _ORDERED_RESOURCE_WORKFLOW.replace("const worker: Agent;", "const worker: Program;")
    flow_path = anyio.Path(tmp_path / "program.workflow")
    await flow_path.write_text(source, encoding="utf-8")
    created = False

    async def create_step_agent(ai_socket: str, session_id: str) -> None:
        nonlocal created
        del ai_socket, session_id
        created = True

    monkeypatch.setattr(run_flow_tool._paths, "resolve_workspace", lambda: anyio.Path(tmp_path))
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)

    with pytest.raises(ValueError, match="supports Agent executors only"):
        await run_flow_tool.run_flow(
            "program.workflow",
            '{"before": "Prepare.", "after": "Finish."}',
            '{"request": "go"}',
            '{"gpu": 1}',
        )
    assert not created


@pytest.mark.anyio
async def test_instructions_json_has_raw_size_limit_before_parsing() -> None:
    oversized = '{"instruction":"' + ("x" * run_flow_tool._MAX_INSTRUCTIONS_JSON_BYTES) + '"}'
    with pytest.raises(ValueError, match="instructions_json exceeds"):
        run_flow_tool._parse_instruction_bodies(oversized)


def test_inputs_json_has_raw_size_limit_before_parsing() -> None:
    oversized = '{"input":"' + ("x" * run_flow_tool._MAX_INPUTS_JSON_BYTES) + '"}'
    with pytest.raises(ValueError, match="inputs_json exceeds"):
        run_flow_tool._parse_mapping(
            oversized,
            label="inputs_json",
            max_bytes=run_flow_tool._MAX_INPUTS_JSON_BYTES,
        )


def test_resource_capacities_json_has_raw_size_limit_before_parsing() -> None:
    oversized = '{"gpu":["' + ("x" * run_flow_tool._MAX_RESOURCE_CAPACITIES_JSON_BYTES) + '"]}'
    with pytest.raises(ValueError, match="resource_capacities_json exceeds"):
        run_flow_tool._parse_resource_capacities(oversized)


@pytest.mark.anyio
async def test_run_flow_requires_invoking_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "ordered.workflow")
    await flow_path.write_text(_ORDERED_RESOURCE_WORKFLOW, encoding="utf-8")
    monkeypatch.setattr(run_flow_tool._paths, "resolve_workspace", lambda: anyio.Path(tmp_path))
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: None)

    with pytest.raises(RuntimeError, match="called by a psi-agent Session"):
        await run_flow_tool.run_flow(
            "ordered.workflow",
            '{"before": "Prepare.", "after": "Finish."}',
            '{"request": "go"}',
            '{"gpu": 1}',
        )


@pytest.mark.anyio
async def test_flow_source_uses_runtime_workspace_when_agent_root_is_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "split-root.workflow")
    await flow_path.write_text("workflow split_root {}", encoding="utf-8")
    monkeypatch.setattr(run_flow_tool._paths, "resolve_workspace", lambda: anyio.Path(tmp_path))

    assert Path(run_flow_tool._AGENT_DIR) != tmp_path
    assert await run_flow_tool._read_flow_source("split-root.workflow") == "workflow split_root {}"


@pytest.mark.anyio
async def test_flow_source_has_raw_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "large.workflow")
    await flow_path.write_bytes(b"x" * (run_flow_tool._MAX_FLOW_SOURCE_BYTES + 1))
    monkeypatch.setattr(run_flow_tool._paths, "resolve_workspace", lambda: anyio.Path(tmp_path))

    with pytest.raises(ValueError, match="flow source exceeds"):
        await run_flow_tool._read_flow_source("large.workflow")


@pytest.mark.anyio
async def test_flow_path_must_stay_inside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_flow_tool._paths, "resolve_workspace", lambda: anyio.Path(tmp_path))

    with pytest.raises(ValueError, match="stay inside the workspace"):
        await run_flow_tool._read_flow_source("../outside.workflow")


def test_exception_group_is_flattened_without_losing_step_identity() -> None:
    leaf = RuntimeError("workflow step 'draft' failed: boom")
    flattened = run_flow_tool._flatten_execution_error(ExceptionGroup("outer", [ExceptionGroup("inner", [leaf])]))

    assert flattened is leaf
