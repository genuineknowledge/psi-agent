from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from psi_agent.session.tool_registry import FileEntry, ToolFunction, ToolRegistry

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
const selected_result: Artifact;

workflow ordered {
    input_workflow(ordered) == [request];
    output_workflow(ordered) == [after_result, before_result, selected_result];
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
    selected_result == if(request = "go", before_result, after_result);
}
"""

_PROGRAM_WORKFLOW = _ORDERED_RESOURCE_WORKFLOW.replace(
    "const worker: Agent;",
    "const worker: Program;",
).replace(
    "    resource_requirement(before_step, gpu) == 1;\n",
    "",
)


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
        "inputs_json",
        "resource_capacities_json",
    }
    assert tool.parameters["required"] == ["flow_path"]


@pytest.mark.anyio
async def test_run_flow_executes_once_with_dependencies_and_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flows = anyio.Path(tmp_path / "flows")
    await flows.mkdir()
    await (flows / "ordered.workflow").write_text(_ORDERED_RESOURCE_WORKFLOW, encoding="utf-8")
    prompts: list[str] = []

    class FakeConversation:
        def __init__(self) -> None:
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

    agents: list[FakeAgent] = []

    async def create_step_agent(
        ai_socket: str,
        tool_registry: ToolRegistry,
    ) -> tuple[FakeAgent, FakeConversation]:
        assert ai_socket == "http://ai.example"
        assert "run_flow" not in tool_registry.tools
        conversation = FakeConversation()
        agent = FakeAgent(conversation)
        agents.append(agent)
        return agent, conversation

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    result = await run_flow_tool.run_flow(
        "flows/ordered.workflow",
        '{"request": "go"}',
        '{"gpu": ["cuda:0"]}',
    )

    assert json.loads(result) == {
        "after_result": "AFTER",
        "before_result": "BEFORE",
        "selected_result": "BEFORE",
    }
    assert ["Step: before_step\n" in prompt for prompt in prompts] == [True, False]
    assert 'Reserved resources: {"gpu": ["cuda:0"]}' in prompts[0]
    assert "Reserved resources: {}" in prompts[1]
    assert len(agents) == 2


@pytest.mark.anyio
async def test_run_flow_rejects_program_before_creating_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flow_path = anyio.Path(tmp_path / "program.workflow")
    await flow_path.write_text(_PROGRAM_WORKFLOW, encoding="utf-8")
    created = False
    loaded_tools = False

    async def create_step_agent(ai_socket: str, tool_registry: ToolRegistry) -> None:
        nonlocal created
        del ai_socket, tool_registry
        created = True

    async def load_step_tools() -> ToolRegistry:
        nonlocal loaded_tools
        loaded_tools = True
        return ToolRegistry()

    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)
    monkeypatch.setattr(run_flow_tool, "_create_step_agent", create_step_agent)
    monkeypatch.setattr(run_flow_tool, "_load_step_tools", load_step_tools)
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: "http://ai.example")

    with pytest.raises(ValueError, match=r"unsupported executors: .*Program"):
        await run_flow_tool.run_flow(
            "program.workflow",
            '{"request": "go"}',
        )

    assert not created
    assert not loaded_tools


@pytest.mark.anyio
async def test_step_agent_uses_in_memory_history_and_explicit_system_prompt() -> None:
    agent, conversation = await run_flow_tool._create_step_agent(
        "http://ai.example",
        ToolRegistry(),
    )

    assert agent._conversation is conversation
    assert agent._ai_client.ai_socket == "http://ai.example"
    assert conversation.messages == [
        {"role": "system", "content": run_flow_tool._STEP_SYSTEM_PROMPT},
    ]
    assert conversation._path is None


@pytest.mark.anyio
async def test_step_tool_snapshot_filters_run_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def echo(message: str) -> str:
        return message

    async def nested_run_flow(flow_path: str) -> str:
        return flow_path

    async def legacy_flow_run(flow_path: str) -> str:
        return flow_path

    loads = 0
    refreshes = 0

    class FakeToolRegistry(ToolRegistry):
        @classmethod
        async def load(
            cls,
            tools_dir: Path,
            session_id: str = "",
        ) -> ToolRegistry:
            nonlocal loads
            del cls, tools_dir, session_id
            loads += 1
            return source

        async def refresh(self) -> dict[str, str]:
            nonlocal refreshes
            refreshes += 1
            return {}

    source = FakeToolRegistry(
        files={
            "__test__": FileEntry(
                file_hash="",
                tools={
                    "echo": ToolFunction.from_callable(echo),
                    "flow_run": ToolFunction.from_callable(legacy_flow_run),
                    "run_flow": ToolFunction.from_callable(nested_run_flow),
                },
                funcs={
                    "echo": echo,
                    "flow_run": legacy_flow_run,
                    "run_flow": nested_run_flow,
                },
            )
        }
    )

    monkeypatch.setattr(run_flow_tool, "ToolRegistry", FakeToolRegistry)
    monkeypatch.setattr(run_flow_tool, "_STEP_TOOLS_SOURCE", None)

    snapshot = await run_flow_tool._load_step_tools()
    refreshed_snapshot = await run_flow_tool._load_step_tools()

    assert set(snapshot.tools) == {"echo"}
    assert snapshot.get("echo") is echo
    assert snapshot.get("flow_run") is None
    assert snapshot.get("run_flow") is None
    assert set(refreshed_snapshot.tools) == {"echo"}
    assert loads == 1
    assert refreshes == 1


@pytest.mark.anyio
async def test_run_flow_rejects_paths_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_flow_tool, "_WORKSPACE_DIR", tmp_path)

    with pytest.raises(ValueError, match="inside the workspace"):
        await run_flow_tool._read_flow_source("../outside.workflow")


@pytest.mark.anyio
async def test_run_flow_requires_invoking_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_flow_tool, "current_tool_ai_socket", lambda: None)

    with pytest.raises(RuntimeError, match="called by a psi-agent Session"):
        await run_flow_tool.run_flow("flows/example.workflow", "{}")
