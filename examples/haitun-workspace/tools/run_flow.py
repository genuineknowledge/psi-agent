"""Compile and execute one FusionFlow G4 workflow."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from contextlib import aclosing
from pathlib import Path
from typing import cast

import anyio

from psi_agent.session.agent import SessionAgent, current_tool_ai_socket
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.schedule_registry import ScheduleRegistry
from psi_agent.session.tool_registry import FileEntry, ToolRegistry
from psi_agent.workflow_execution import ResourceCapacity

_WORKSPACE_DIR = Path(__file__).parent.parent
_SKILL_DIR = _WORKSPACE_DIR / "skills" / "fusion-flow"
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

from fusion_flow_next.workflow_runner import CompletionContext  # noqa: E402
from fusion_flow_next.workflow_runner import execute_workflow as _execute_workflow  # noqa: E402

_STEP_SYSTEM_PROMPT = (
    "You execute exactly one assigned FusionFlow Agent step. "
    "Follow the step instruction and inputs in the user message, using workspace tools when needed. "
    "Do not perform workspace onboarding and do not start another workflow. "
    "Your final response must follow the requested JSON output contract exactly."
)
_STEP_TOOL_SESSION_ID = f"{__name__}_step"
_STEP_TOOLS_LOAD_LOCK = anyio.Lock()
_STEP_TOOLS_SOURCE: ToolRegistry | None = None
_WORKFLOW_LAUNCHERS = frozenset({"flow_run", "run_flow"})


class _StepToolRegistry(ToolRegistry):
    async def refresh(self) -> dict[str, str]:
        return {}


class _StepScheduleRegistry(ScheduleRegistry):
    async def refresh(self) -> dict[str, str]:
        return {}


def _parse_mapping(value: str, *, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} must be a JSON object") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError(f"{label} must be a JSON object with string keys")
    return cast(dict[str, object], parsed)


def _parse_resource_capacities(value: str) -> Mapping[str, ResourceCapacity] | None:
    if not value.strip():
        return None

    parsed = _parse_mapping(value, label="resource_capacities_json")
    capacities: dict[str, ResourceCapacity] = {}
    for resource_id, capacity in parsed.items():
        if type(capacity) is int:
            capacities[resource_id] = capacity
        elif isinstance(capacity, list) and all(isinstance(instance_id, str) for instance_id in capacity):
            capacities[resource_id] = tuple(cast(list[str], capacity))
        else:
            raise ValueError(
                f"resource capacity for {resource_id!r} must be an integer or an array of resource instance IDs"
            )
    return capacities


async def _read_flow_source(flow_path: str) -> str:
    workspace = await anyio.Path(str(_WORKSPACE_DIR)).resolve()
    candidate = anyio.Path(flow_path)
    if candidate.is_absolute():
        raise ValueError("flow_path must be relative to the workspace")
    candidate = workspace / flow_path
    resolved = await candidate.resolve()
    flows_dir = await (workspace / "flows").resolve()
    if not Path(str(resolved)).is_relative_to(Path(str(flows_dir))):
        raise ValueError("flow_path must stay inside the workspace flows directory")
    if resolved.suffix != ".workflow":
        raise ValueError("flow_path must name a .workflow file")
    return await resolved.read_text(encoding="utf-8")


def _resource_payload(context: CompletionContext) -> dict[str, list[str]]:
    return {grant.resource_id: list(grant.instance_ids) for grant in context.dispatch.resource_lease.grants}


async def _load_step_tools() -> ToolRegistry:
    global _STEP_TOOLS_SOURCE

    async with _STEP_TOOLS_LOAD_LOCK:
        if _STEP_TOOLS_SOURCE is None:
            _STEP_TOOLS_SOURCE = await ToolRegistry.load(
                _WORKSPACE_DIR / "tools",
                session_id=_STEP_TOOL_SESSION_ID,
            )
        else:
            await _STEP_TOOLS_SOURCE.refresh()

        tools = {name: tool for name, tool in _STEP_TOOLS_SOURCE.tools.items() if name not in _WORKFLOW_LAUNCHERS}
        funcs = {name: func for name in tools if (func := _STEP_TOOLS_SOURCE.get(name)) is not None}
        return _StepToolRegistry(
            files={
                "__fusion_flow_step_tools__": FileEntry(
                    file_hash="",
                    tools=tools,
                    funcs=funcs,
                )
            }
        )


async def _create_step_agent(
    ai_socket: str,
    tool_registry: ToolRegistry,
) -> tuple[SessionAgent, Conversation]:
    conversation = Conversation(
        messages=[{"role": "system", "content": _STEP_SYSTEM_PROMPT}],
    )
    agent = SessionAgent(
        ai_client=AiClient(ai_socket),
        conversation=conversation,
        schedule_registry=_StepScheduleRegistry(),
        tool_registry=tool_registry,
    )
    return agent, conversation


async def _complete_step_agent(
    agent: SessionAgent,
    conversation: Conversation,
    message: str,
) -> str:
    async with aclosing(agent.run({"role": "user", "content": message})) as chunks:
        async for _ in chunks:
            pass

    if not conversation.messages:
        raise RuntimeError("step agent produced no final assistant text")
    final = conversation.messages[-1]
    content = final.get("content")
    if (
        final.get("role") != "assistant"
        or final.get("tool_calls")
        or not isinstance(content, str)
        or not content.strip()
    ):
        raise RuntimeError("step agent produced no final assistant text")
    return content


async def _complete_agent_step(
    prompt: str,
    context: CompletionContext,
    *,
    ai_socket: str,
    tool_registry: ToolRegistry,
) -> dict[str, object]:
    agent, conversation = await _create_step_agent(ai_socket, tool_registry)
    message = (
        "Execute exactly one assigned FusionFlow step. Do not start another workflow.\n"
        f"Step: {context.step_id}\n"
        f"Executor: {context.executor_id}\n"
        f"Reserved resources: {json.dumps(_resource_payload(context), ensure_ascii=False, sort_keys=True)}\n"
        f"Required output keys: {json.dumps(context.output_ids, ensure_ascii=False)}\n"
        f"{prompt}\n"
        "Respond with exactly one JSON object keyed by exactly those output keys, "
        "with no surrounding prose or Markdown."
    )
    response = await _complete_step_agent(agent, conversation, message)
    return _parse_mapping(response, label=f"response for step {context.step_id!r}")


async def run_flow(
    flow_path: str,
    inputs_json: str = "{}",
    resource_capacities_json: str = "",
) -> str:
    """Run one G4 workflow synchronously and return its output artifacts as JSON.

    Args:
        flow_path: Workspace-relative path to a UTF-8 ``.workflow`` file.
        inputs_json: JSON object keyed by the workflow's input artifact IDs.
        resource_capacities_json: Optional JSON object mapping resource IDs to
            positive counts or concrete instance-ID arrays.

    Returns:
        A JSON object keyed by the workflow's output artifact IDs.
    """

    ai_socket = current_tool_ai_socket()
    if ai_socket is None:
        raise RuntimeError("run_flow must be called by a psi-agent Session")

    source = await _read_flow_source(flow_path)
    inputs = _parse_mapping(inputs_json, label="inputs_json")
    resource_capacities = _parse_resource_capacities(resource_capacities_json)
    step_tools: ToolRegistry | None = None
    step_tools_lock = anyio.Lock()

    async def get_step_tools() -> ToolRegistry:
        nonlocal step_tools
        if step_tools is None:
            async with step_tools_lock:
                if step_tools is None:
                    step_tools = await _load_step_tools()
        return step_tools

    async def complete(prompt: str, context: CompletionContext) -> dict[str, object]:
        return await _complete_agent_step(
            prompt,
            context,
            ai_socket=ai_socket,
            tool_registry=await get_step_tools(),
        )

    outputs = await _execute_workflow(
        source,
        inputs=inputs,
        contextual_complete=complete,
        resource_capacities=resource_capacities,
        strict_executors=True,
        supported_executor_kinds=("Agent",),
    )
    return json.dumps(outputs, ensure_ascii=False, sort_keys=True)
