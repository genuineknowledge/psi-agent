"""Compile and execute one bounded FusionFlow Next G4 workflow."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Mapping
from contextlib import aclosing
from pathlib import Path
from typing import cast

import anyio

from psi_agent.session.agent import SessionAgent, current_tool_ai_socket
from psi_agent.session.ai_client import AiClient
from psi_agent.session.conversation import Conversation
from psi_agent.session.schedule_registry import ScheduleRegistry
from psi_agent.session.tool_registry import ToolRegistry

_TOOLS_DIR = Path(__file__).resolve().parent
_AGENT_DIR = _TOOLS_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
_SKILL_DIR = _AGENT_DIR / "skills" / "fusion-flow-next"
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

import _runtime_paths as _paths  # noqa: E402
from fusion_flow_next.workflow_execution import ResourceCapacity  # noqa: E402
from fusion_flow_next.workflow_runner import (  # noqa: E402
    CompletionContext,
    compile_workflow,
    validate_instruction_bodies,
)
from fusion_flow_next.workflow_runner import execute_workflow as _execute_workflow  # noqa: E402

_MAX_INSTRUCTIONS_JSON_BYTES = 256 * 1024
_MAX_INPUTS_JSON_BYTES = 1024 * 1024
_MAX_RESOURCE_CAPACITIES_JSON_BYTES = 64 * 1024
_MAX_FLOW_SOURCE_BYTES = 1024 * 1024
_STEP_SYSTEM_PROMPT = (
    "You execute exactly one assigned FusionFlow Agent step. "
    "Use only the instruction and artifact inputs in the user message. "
    "No tools are available. Do not plan or start another workflow. "
    "Reply with exactly one JSON object that follows the output contract."
)


class _EphemeralConversation(Conversation):
    """In-memory conversation with a unique routing identity."""

    def __init__(self, session_id: str) -> None:
        super().__init__(messages=[{"role": "system", "content": _STEP_SYSTEM_PROMPT}])
        self._ephemeral_session_id = session_id

    @property
    def session_id(self) -> str:
        return self._ephemeral_session_id


def _parse_mapping(
    value: str,
    *,
    label: str,
    max_bytes: int | None = None,
) -> dict[str, object]:
    if max_bytes is not None and len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")

    def reject_constant(constant: str) -> object:
        raise ValueError(f"{label} must use strict JSON; {constant} is not allowed")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed_object: dict[str, object] = {}
        for key, item in pairs:
            if key in parsed_object:
                raise ValueError(f"{label} contains duplicate key {key!r}")
            parsed_object[key] = item
        return parsed_object

    try:
        parsed = json.loads(
            value,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} must be a JSON object") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError(f"{label} must be a JSON object with string keys")
    return cast(dict[str, object], parsed)


def _parse_instruction_bodies(value: str) -> dict[str, str]:
    if len(value.encode("utf-8")) > _MAX_INSTRUCTIONS_JSON_BYTES:
        raise ValueError(f"instructions_json exceeds {_MAX_INSTRUCTIONS_JSON_BYTES} UTF-8 bytes")
    parsed = _parse_mapping(value, label="instructions_json")
    if not all(isinstance(body, str) for body in parsed.values()):
        raise ValueError("instructions_json must map instruction IDs to string bodies")
    return cast(dict[str, str], parsed)


def _parse_resource_capacities(value: str) -> Mapping[str, ResourceCapacity] | None:
    if not value.strip():
        return None

    parsed = _parse_mapping(
        value,
        label="resource_capacities_json",
        max_bytes=_MAX_RESOURCE_CAPACITIES_JSON_BYTES,
    )
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
    workspace = await _paths.resolve_workspace().resolve()
    candidate = anyio.Path(flow_path)
    if not candidate.is_absolute():
        candidate = workspace / flow_path
    resolved = await candidate.resolve()
    if not Path(str(resolved)).is_relative_to(Path(str(workspace))):
        raise ValueError("flow_path must stay inside the workspace")
    if resolved.suffix not in {".workflow", ".g4"}:
        raise ValueError("flow_path must name a .workflow or .g4 file")
    if (await resolved.stat()).st_size > _MAX_FLOW_SOURCE_BYTES:
        raise ValueError(f"flow source exceeds {_MAX_FLOW_SOURCE_BYTES} bytes")
    return await resolved.read_text(encoding="utf-8")


def _resource_payload(context: CompletionContext) -> dict[str, list[str]]:
    return {grant.resource_id: list(grant.instance_ids) for grant in context.dispatch.resource_lease.grants}


async def _create_step_agent(
    ai_socket: str,
    session_id: str,
) -> tuple[SessionAgent, Conversation]:
    conversation = _EphemeralConversation(session_id)
    agent = SessionAgent(
        ai_client=AiClient(ai_socket),
        conversation=conversation,
        schedule_registry=ScheduleRegistry(),
        tool_registry=ToolRegistry(session_id=session_id),
        max_tool_rounds=1,
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
) -> dict[str, object]:
    session_id = f"fusion-flow-next-{uuid.uuid4().hex}"
    agent, conversation = await _create_step_agent(ai_socket, session_id)
    message = (
        "Execute exactly one assigned FusionFlow step.\n"
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


def _flatten_execution_error(error: ExceptionGroup) -> Exception:
    leaves: list[Exception] = []

    def visit(current: Exception) -> None:
        if isinstance(current, ExceptionGroup):
            for nested in current.exceptions:
                visit(nested)
        else:
            leaves.append(current)

    visit(error)
    if len(leaves) == 1:
        return leaves[0]
    details = "; ".join(f"{item.__class__.__name__}: {item}" for item in leaves)
    return RuntimeError(f"multiple workflow step failures: {details}")


async def run_flow(
    flow_path: str,
    instructions_json: str,
    inputs_json: str = "{}",
    resource_capacities_json: str = "",
) -> str:
    """Run one FusionFlow Next workflow and return its output artifacts.

    Args:
        flow_path: Workspace-relative path to a UTF-8 ``.workflow`` or ``.g4`` file.
        instructions_json: JSON object mapping every declared Instruction ID
            to its actual instruction body.
        inputs_json: JSON object keyed by the workflow input artifact IDs.
        resource_capacities_json: Optional JSON object mapping resource IDs to
            positive counts or concrete instance-ID arrays.

    Returns:
        A JSON object keyed by the workflow output artifact IDs.
    """

    source = await _read_flow_source(flow_path)
    instruction_bodies = _parse_instruction_bodies(instructions_json)
    inputs = _parse_mapping(
        inputs_json,
        label="inputs_json",
        max_bytes=_MAX_INPUTS_JSON_BYTES,
    )
    resource_capacities = _parse_resource_capacities(resource_capacities_json)

    # Validate the exact instruction contract before constructing any inner
    # SessionAgent or tool registry.
    compiled = compile_workflow(source, strict_executors=True)
    validate_instruction_bodies(compiled, instruction_bodies)

    ai_socket = current_tool_ai_socket()
    if ai_socket is None:
        raise RuntimeError("run_flow must be called by a psi-agent Session")

    async def complete(prompt: str, context: CompletionContext) -> dict[str, object]:
        return await _complete_agent_step(
            prompt,
            context,
            ai_socket=ai_socket,
        )

    try:
        outputs = await _execute_workflow(
            source,
            instruction_bodies=instruction_bodies,
            inputs=inputs,
            contextual_complete=complete,
            resource_capacities=resource_capacities,
        )
    except ExceptionGroup as error:
        raise _flatten_execution_error(error) from error
    return json.dumps(
        outputs,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
